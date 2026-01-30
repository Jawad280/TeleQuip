import math
import asyncio
from config import RESPONSE_TIMEOUT, TIME_GIFS
from models.Game import Game
from core.telegram_client import TelegramClient
from core.prompt_manager import PromptManager

class GameManager:
    def __init__(self):
        self.active_games = {}  # {group_id: Game}

    def create_game(self, group_id):
        if group_id in self.active_games:
            return None
        game = Game(group_id)
        self.active_games[group_id] = game
        return game

    def get_game(self, group_id) -> Game:
        return self.active_games.get(group_id)

    def stop_game(self, group_id):
        if group_id in self.active_games:
            del self.active_games[group_id]

    async def _round_timer(self, game: Game, telegram_client: TelegramClient):
        await asyncio.sleep(RESPONSE_TIMEOUT)

        # Mark unanswered prompts
        for user_id, answers in game.pending_answers.items():
            for idx, ans in enumerate(answers):
                if ans is None:
                    game.pending_answers[user_id][idx] = "❌ No response"

        # Start versus phase
        await self.start_versus_phase(game, telegram_client)

    async def start_round(self, game: Game, telegram_client: TelegramClient, prompt_manager: PromptManager, m=2):
        n = len(game.players)
        game.round += 1
        game.pending_answers = {uid: [None]*m for uid in game.players}
        game.prompt_messages = {uid: {} for uid in game.players}

        # Initialize votes, scores, poll map, completed polls
        game.votes = {}
        # game.scores = {uid: 0 for uid in game.players}
        game.poll_map = {}
        game.completed_polls = set()

        # Pick enough prompts
        total_prompts = math.ceil(n/2) * m
        prompts = prompt_manager.get_random_prompts(total_prompts)

        players_list = list(game.players.values())
        for i, player in enumerate(players_list):
            assigned_prompts = [prompts[(i + j) % len(prompts)] for j in range(m)]

            # Send GIF timer
            gif_path = TIME_GIFS.get(RESPONSE_TIMEOUT)
            if gif_path:
                await telegram_client.send_gif_to_person(
                    player.user_id,
                    gif_path,
                    caption=f"Round {game.round} has started! ⏱ You have {RESPONSE_TIMEOUT - 5} seconds to answer all prompts.",
                    ttl=None
                )

            # Send prompts individually
            for idx, prompt in enumerate(assigned_prompts):
                message = await telegram_client.send_message_to_person(
                    player.user_id,
                    f"Round {game.round} - Prompt {idx+1}:\n\n{prompt}\n\nPlease reply to this message with your answer."
                )
                game.prompt_messages[player.user_id][message.message_id] = idx

        # Start round timer
        asyncio.create_task(self._round_timer(game, telegram_client))

    def build_versus_pairs(self, game: Game):
        players = list(game.players.values())
        pairs = []
        if not players:
            return pairs

        m = len(game.pending_answers[players[0].user_id])  # prompts per player

        for prompt_idx in range(m):
            for i, player in enumerate(players):
                p1 = player
                p2 = players[(i + 1) % len(players)]
                pairs.append((prompt_idx, p1.user_id, p2.user_id))

        game.versus_pairs = pairs

    async def conduct_versus_poll(self, game: Game, telegram_client: TelegramClient, group_id):
        for prompt_idx, p1_id, p2_id in game.versus_pairs:
            p1_answer = game.pending_answers[p1_id][prompt_idx]
            p2_answer = game.pending_answers[p2_id][prompt_idx]

            question = f"Prompt {prompt_idx+1}:\nVote for the better answer!"
            options = [f"{game.players[p1_id].username}: {p1_answer}",
                       f"{game.players[p2_id].username}: {p2_answer}"]

            poll_message = await telegram_client.send_poll(group_id, question, options)

            # Track poll for votes
            game.votes[poll_message.message_id] = {}
            game.poll_map[poll_message.message_id] = (prompt_idx, p1_id, p2_id)

    async def calculate_poll_score(self, game: Game, poll_id):
        if poll_id not in game.poll_map:
            return

        prompt_idx, p1_id, p2_id = game.poll_map[poll_id]
        votes = game.votes.get(poll_id, {})
        total_votes = len(votes)
        if total_votes == 0:
            # Nobody voted, split points equally
            game.scores[p1_id] += 50
            game.scores[p2_id] += 50
        else:
            p1_votes = sum(1 for v in votes.values() if v == 0)
            p2_votes = total_votes - p1_votes
            game.scores[p1_id] += 100 * (p1_votes / total_votes)
            game.scores[p2_id] += 100 * (p2_votes / total_votes)

        game.completed_polls.add(poll_id)

    async def send_scoreboard(self, game: Game, telegram_client: TelegramClient, group_id):
        scoreboard = "🏆 Current Scores:\n"
        sorted_players = sorted(game.scores.items(), key=lambda x: x[1], reverse=True)
        for uid, score in sorted_players:
            scoreboard += f"{game.players[uid].username}: {score}\n"
        await telegram_client.send_message_to_group(group_id, scoreboard)

    async def start_versus_phase(self, game: Game, telegram_client: TelegramClient):
        group_id = game.group_id
        self.build_versus_pairs(game)

        # Sequentially conduct polls
        for prompt_idx, p1_id, p2_id in game.versus_pairs:
            p1_answer = game.pending_answers[p1_id][prompt_idx]
            p2_answer = game.pending_answers[p2_id][prompt_idx]

            question = f"Prompt {prompt_idx+1}:\nVote for the better answer!"
            options = [
                f"{game.players[p1_id].username}: {p1_answer}",
                f"{game.players[p2_id].username}: {p2_answer}"
            ]

            poll_message = await telegram_client.send_poll(group_id, question, options)

            # Track poll
            game.votes[poll_message.message_id] = {}
            game.poll_map[poll_message.message_id] = (prompt_idx, p1_id, p2_id)

            # Wait for all players to vote on this poll
            start_time = asyncio.get_event_loop().time()
            while len(game.votes[poll_message.message_id]) < len(game.players):
                if asyncio.get_event_loop().time() - start_time > 15:
                    break
                await asyncio.sleep(1)

            # Once all votes in, calculate score for this poll
            await self.calculate_poll_score(game, poll_message.message_id)

        # After all polls completed, send scoreboard
        await self.send_scoreboard(game, telegram_client, group_id)

