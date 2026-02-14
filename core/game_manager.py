import asyncio
import random
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
        half_elapsed = RESPONSE_TIMEOUT / 2
        warning_elapsed = max(0, RESPONSE_TIMEOUT - LAST_REMINDER_TIME)

        # Half-time warning
        await asyncio.sleep(half_elapsed)
        for player in game.players.values():
            await telegram_client.send_message_to_person(
                player.user_id,
                f"⏱ Half the time has passed! {int(RESPONSE_TIMEOUT - half_elapsed)} seconds left to submit your answers."
            )

        # 10-second warning
        await asyncio.sleep(max(0, warning_elapsed - half_elapsed))
        for player in game.players.values():
            await telegram_client.send_message_to_person(
                player.user_id,
                f"⚠️ Only {int(RESPONSE_TIMEOUT - warning_elapsed)} seconds left! Quickly finish your prompts!"
            )

        # Wait for the last few seconds
        await asyncio.sleep(max(0, RESPONSE_TIMEOUT - warning_elapsed))

        # Mark unanswered prompts
        for user_id, answers in game.pending_answers.items():
            for idx, ans in enumerate(answers):
                if ans is None:
                    game.pending_answers[user_id][idx] = "❌ No response"

        # Move to versus phase
        await self.start_versus_phase(game, telegram_client)

    def _build_prompt_edges(self, user_ids, m):
        """
        Build a set of head-to-head matchups (edges).

        Each edge corresponds to exactly one prompt, and that prompt is answered by
        exactly two players (the endpoints of the edge). This guarantees there are
        no "extra" prompts that won't be used during versus.
        """
        n = len(user_ids)
        if n < 2 or m <= 0:
            return [], 0

        # Special-case: only one opponent exists; allow repeated matchups.
        if n == 2:
            return [(user_ids[0], user_ids[1])] * m, m

        effective_m = min(m, n - 1)
        if (n * effective_m) % 2 == 1:
            effective_m -= 1
        if effective_m <= 0:
            return [], 0

        edges = set()

        # Circulant graph construction:
        # connect each i to i+k for k=1..floor(effective_m/2).
        for k in range(1, (effective_m // 2) + 1):
            for i in range(n):
                a = user_ids[i]
                b = user_ids[(i + k) % n]
                if a == b:
                    continue
                edges.add((a, b) if a < b else (b, a))

        # If degree is odd, add the "opposite" perfect matching (requires even n).
        if effective_m % 2 == 1 and n % 2 == 0:
            k = n // 2
            for i in range(n // 2):
                a = user_ids[i]
                b = user_ids[i + k]
                edges.add((a, b) if a < b else (b, a))

        edges_list = list(edges)
        random.shuffle(edges_list)
        return edges_list, effective_m

    async def start_round(
        self, game: Game, telegram_client: TelegramClient, prompt_manager: PromptManager, m=2
    ):
        """Start a new round and send prompts to all players."""
        game.init_scores()
        game.round += 1
        game.prompt_messages = {uid: {} for uid in game.players}
        game.assigned_prompts = {uid: [] for uid in game.players}  # user_id -> list[(prompt_id, prompt_text)]

        # Initialize poll tracking
        game.votes = {}
        game.poll_map = {}
        game.completed_polls = set()

        user_ids = list(game.players.keys())
        random.shuffle(user_ids)

        edges, effective_m = self._build_prompt_edges(user_ids, m)
        game.pending_answers = {uid: [None] * effective_m for uid in game.players}

        # Each edge gets a prompt; both players on the edge receive it.
        prompt_texts = prompt_manager.get_random_prompts(len(edges))
        if not prompt_texts:
            prompt_texts = ["⚠️ No prompts available (check data/prompts.txt)."]

        for edge_idx, (u, v) in enumerate(edges):
            prompt_id = f"r{game.round}_e{edge_idx}"
            prompt_text = prompt_texts[edge_idx % len(prompt_texts)]
            game.assigned_prompts[u].append((prompt_id, prompt_text))
            game.assigned_prompts[v].append((prompt_id, prompt_text))

        for player in game.players.values():
            # Send prompts individually
            await telegram_client.send_message_to_person(
                player.user_id,
                f"🎬 Round {game.round} has begun! You have {RESPONSE_TIMEOUT} seconds to respond to all prompts."
            )
            for idx, (_, prompt_text) in enumerate(game.assigned_prompts.get(player.user_id, [])):
                message = await telegram_client.send_message_to_person(
                    player.user_id,
                    f"Prompt {idx+1}:\n\n{prompt_text}\n\nPlease reply to this message with your answer."
                )
                game.prompt_messages[player.user_id][message.message_id] = idx

        # Start the timer (includes half-time and 10s warnings)
        asyncio.create_task(self._round_timer(game, telegram_client))

    # -------------------------
    # Pairing & polling
    # -------------------------
    def build_versus_pairs(self, game: Game):
        """
        Build versus pairs by matching players that received the same prompt instance.

        Prompts can be reused across edges (e.g., if the prompt pool is small), so we
        match using a per-round prompt_id rather than just prompt text.
        """
        prompt_map = {}  # prompt_id -> {"text": prompt_text, "entries": list[(user_id, prompt_idx)]}

        for user_id, prompts in game.assigned_prompts.items():
            for idx, prompt_item in enumerate(prompts):
                prompt_id, prompt_text = prompt_item
                if not prompt_id:
                    continue
                prompt_map.setdefault(prompt_id, {"text": prompt_text, "entries": []})["entries"].append((user_id, idx))

        pairs = []
        for prompt in prompt_map.values():
            prompt_text = prompt["text"]
            entries = prompt["entries"]
            if len(entries) < 2:
                continue

            # deterministic ordering
            entries.sort(key=lambda x: x[0])

            # Pair sequentially: (0,1), (2,3), ...
            for i in range(0, len(entries) - 1, 2):
                (p1_id, idx1) = entries[i]
                (p2_id, idx2) = entries[i + 1]
                pairs.append((prompt_text, p1_id, idx1, p2_id, idx2))

        game.versus_pairs = pairs

    async def conduct_versus_poll(self, game: Game, telegram_client: TelegramClient, group_id):
        """(Unused separately) Create polls for versus pairs."""
        for prompt_text, p1_id, idx1, p2_id, idx2 in game.versus_pairs:
            p1_answer = game.pending_answers[p1_id][idx1]
            p2_answer = game.pending_answers[p2_id][idx2]

            question = f"{prompt_text}\n\nVote for the better answer:"
            options = [
                f"{game.players[p1_id].username}: {p1_answer}",
                f"{game.players[p2_id].username}: {p2_answer}",
            ]

            poll_message = await telegram_client.send_poll(group_id, question, options)

            # Track poll by poll_id (not message_id)
            poll_id = poll_message.poll.id
            game.votes[poll_id] = {}
            game.poll_map[poll_id] = (p1_id, p2_id)

    # -------------------------
    # Scoring
    # -------------------------
    async def calculate_poll_score(self, game: Game, poll_id):
        """Compute and assign points after a poll finishes."""
        if poll_id not in game.poll_map:
            return

        p1_id, p2_id = game.poll_map[poll_id]
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

        for prompt_text, p1_id, idx1, p2_id, idx2 in game.versus_pairs:
            p1_answer = game.pending_answers[p1_id][idx1]
            p2_answer = game.pending_answers[p2_id][idx2]

            question = f"{prompt_text}\n\nVote for the better answer!"
            options = [
                f"{game.players[p1_id].username}: {p1_answer}",
                f"{game.players[p2_id].username}: {p2_answer}",
            ]

            poll_message = await telegram_client.send_poll(group_id, question, options)

            poll_id = poll_message.poll.id
            game.votes[poll_id] = {}
            game.poll_map[poll_id] = (p1_id, p2_id)

            start_time = asyncio.get_event_loop().time()
            while len(game.votes[poll_id]) < len(game.players):
                if asyncio.get_event_loop().time() - start_time > POLL_TIMING:
                    break
                await asyncio.sleep(1)

            # await self.calculate_poll_score(game, poll_id)

        await self.send_scoreboard(game, telegram_client, group_id)
