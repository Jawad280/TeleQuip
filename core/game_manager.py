import math
import asyncio
from config import RESPONSE_TIMEOUT, POLL_TIMING, LAST_REMINDER_TIME
from models.Game import Game
from core.telegram_client import TelegramClient
from core.prompt_manager import PromptManager


class GameManager:
    def __init__(self):
        self.active_games = {}  # {group_id: Game}

    # -------------------------
    # Game lifecycle
    # -------------------------
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

    # -------------------------
    # Round management
    # -------------------------
    async def _round_timer(self, game: Game, telegram_client: TelegramClient):
        """Manages timer and sends time warnings during the round."""
        half_time = RESPONSE_TIMEOUT // 2
        last_reminder_time = RESPONSE_TIMEOUT - LAST_REMINDER_TIME

        # Half-time warning
        await asyncio.sleep(half_time)
        for player in game.players.values():
            await telegram_client.send_message_to_person(
                player.user_id,
                f"⏱ Half the time has passed! {RESPONSE_TIMEOUT - half_time} seconds left to submit your answers."
            )

        # 10-second warning
        await asyncio.sleep(last_reminder_time - half_time)
        for player in game.players.values():
            await telegram_client.send_message_to_person(
                player.user_id,
                f"⚠️ Only {last_reminder_time} seconds left! Quickly finish your prompts!"
            )

        # Wait for the last few seconds
        await asyncio.sleep(10)

        # Mark unanswered prompts
        for user_id, answers in game.pending_answers.items():
            for idx, ans in enumerate(answers):
                if ans is None:
                    game.pending_answers[user_id][idx] = "❌ No response"

        # Move to versus phase
        await self.start_versus_phase(game, telegram_client)

    async def start_round(
        self, game: Game, telegram_client: TelegramClient, prompt_manager: PromptManager, m=2
    ):
        """Start a new round and send prompts to all players."""
        n = len(game.players)
        game.init_scores()
        game.round += 1
        game.pending_answers = {uid: [None] * m for uid in game.players}
        game.prompt_messages = {uid: {} for uid in game.players}
        game.assigned_prompts = {uid: [] for uid in game.players}  # user_id -> list[str]

        # Initialize poll tracking
        game.votes = {}
        game.poll_map = {}
        game.completed_polls = set()

        # Pick enough prompts for this round
        total_prompts = math.ceil(n / 2) * m
        prompts = prompt_manager.get_random_prompts(total_prompts)

        players_list = list(game.players.values())
        for i, player in enumerate(players_list):
            assigned_prompts = [prompts[(i + j) % len(prompts)] for j in range(m)]
            game.assigned_prompts[player.user_id] = assigned_prompts

            # Send prompts individually
            await telegram_client.send_message_to_person(
                player.user_id,
                f"🎬 Round {game.round} has begun! You have {RESPONSE_TIMEOUT} seconds to respond to all prompts."
            )
            for idx, prompt in enumerate(assigned_prompts):
                message = await telegram_client.send_message_to_person(
                    player.user_id,
                    f"Prompt {idx+1}:\n\n{prompt}\n\nPlease reply to this message with your answer."
                )
                game.prompt_messages[player.user_id][message.message_id] = idx

        # Start the timer (includes half-time and 10s warnings)
        asyncio.create_task(self._round_timer(game, telegram_client))

    # -------------------------
    # Pairing & polling
    # -------------------------
    def build_versus_pairs(self, game: Game):
        """Build unique player pairs for each prompt."""
        players = list(game.players.values())
        pairs = []
        if not players:
            return pairs

        m = len(game.pending_answers[players[0].user_id])  # prompts per player
        n = len(players)

        for prompt_idx in range(m):
            for i in range(0, n, 2):
                p1 = players[i]
                p2 = players[(i + 1) % n]  # wrap-around if odd count
                pairs.append((prompt_idx, p1.user_id, p2.user_id))

        game.versus_pairs = pairs

    async def conduct_versus_poll(self, game: Game, telegram_client: TelegramClient, group_id):
        """(Unused separately) Create polls for versus pairs."""
        for prompt_idx, p1_id, p2_id in game.versus_pairs:
            prompt_text = game.assigned_prompts[p1_id][prompt_idx]
            p1_answer = game.pending_answers[p1_id][prompt_idx]
            p2_answer = game.pending_answers[p2_id][prompt_idx]

            question = f"{prompt_text}\n\nVote for the better answer:"
            options = [
                f"{game.players[p1_id].username}: {p1_answer}",
                f"{game.players[p2_id].username}: {p2_answer}",
            ]

            poll_message = await telegram_client.send_poll(group_id, question, options)

            # Track poll by poll_id (not message_id)
            poll_id = poll_message.poll.id
            game.votes[poll_id] = {}
            game.poll_map[poll_id] = (prompt_idx, p1_id, p2_id)

    # -------------------------
    # Scoring
    # -------------------------
    async def calculate_poll_score(self, game: Game, poll_id):
        """Compute and assign points after a poll finishes."""
        if poll_id not in game.poll_map:
            return

        prompt_idx, p1_id, p2_id = game.poll_map[poll_id]
        votes = game.votes.get(poll_id, {})
        print(f"Votes for poll {poll_id}:", votes)
        total_votes = len(votes)

        game.scores.setdefault(p1_id, 0.0)
        game.scores.setdefault(p2_id, 0.0)

        if total_votes == 0:
            game.scores[p1_id] += 50.0
            game.scores[p2_id] += 50.0
        else:
            p1_votes = sum(1 for v in votes.values() if v == 0)
            p2_votes = total_votes - p1_votes

            p1_points = 100.0 * (p1_votes / total_votes)
            p2_points = 100.0 * (p2_votes / total_votes)

            game.scores[p1_id] += p1_points
            game.scores[p2_id] += p2_points

        game.completed_polls.add(poll_id)

    async def send_scoreboard(self, game: Game, telegram_client: TelegramClient, group_id):
        """Send the current scoreboard to the group."""
        scoreboard = "🏆 Current Scores:\n"
        sorted_players = sorted(game.scores.items(), key=lambda x: x[1], reverse=True)
        for uid, score in sorted_players:
            scoreboard += f"{game.players[uid].username}: {round(score, 1)}\n"
        await telegram_client.send_message_to_group(group_id, scoreboard)

    # -------------------------
    # Versus phase
    # -------------------------
    async def start_versus_phase(self, game: Game, telegram_client: TelegramClient):
        """Run the versus voting phase with polls."""
        group_id = game.group_id
        self.build_versus_pairs(game)

        for prompt_idx, p1_id, p2_id in game.versus_pairs:
            prompt_text = game.assigned_prompts[p1_id][prompt_idx]
            p1_answer = game.pending_answers[p1_id][prompt_idx]
            p2_answer = game.pending_answers[p2_id][prompt_idx]

            question = f"{prompt_text}\n\nVote for the better answer!"
            options = [
                f"{game.players[p1_id].username}: {p1_answer}",
                f"{game.players[p2_id].username}: {p2_answer}",
            ]

            poll_message = await telegram_client.send_poll(group_id, question, options)

            poll_id = poll_message.poll.id
            game.votes[poll_id] = {}
            game.poll_map[poll_id] = (prompt_idx, p1_id, p2_id)

            start_time = asyncio.get_event_loop().time()
            while len(game.votes[poll_id]) < len(game.players):
                if asyncio.get_event_loop().time() - start_time > POLL_TIMING:
                    break
                await asyncio.sleep(1)

            # await self.calculate_poll_score(game, poll_id)

        await self.send_scoreboard(game, telegram_client, group_id)
