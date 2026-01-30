import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters, PollAnswerHandler

from core.telegram_client import TelegramClient
from core.game_manager import GameManager
from core.player_manager import PlayerManager
from core.prompt_manager import PromptManager
from config import BOT_KEY, MAX_PLAYERS, ROUND_COUNT

# --- Managers ---
telegram_client = TelegramClient(token=BOT_KEY)
game_manager = GameManager()
prompt_manager = PromptManager()

# -------------------------
# TEST HANDLER
# -------------------------
async def test_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await telegram_client.send_message_to_group(
        update.effective_chat.id,
        "Testing systems, everything functional"
    )

# -------------------------
# COMMAND HANDLERS
# -------------------------
async def init_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id

    # Only allow in groups
    if update.effective_chat.type == "private":
        await telegram_client.send_message_to_group(group_id, "Please use this command in a group")
        return

    if game_manager.get_game(group_id):
        await telegram_client.send_message_to_group(group_id, "A game already exists")
        return

    game = game_manager.create_game(group_id)

    join_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Join Game", callback_data=f"join_{group_id}")]]
    )

    await telegram_client.send_message_to_group(
        group_id,
        "🕹 New lobby created! Click Join Game to enter. When ready, use /start_game to begin.",
        reply_markup=join_markup
    )

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id

    game = game_manager.get_game(group_id)
    if not game:
        await telegram_client.send_message_to_group(group_id, "No lobby found, sir. Use /init_game first.")
        return

    if len(game.players) < 2:
        await telegram_client.send_message_to_group(group_id, "Need at least two players to start.")
        return

    # Lock further joins
    game.locked = True

    player_names = ", ".join([p.username or 'No-Name' for p in game.players.values()])
    await telegram_client.send_message_to_group(
        group_id,
        f"🎬 Game starting with {len(game.players)} players: {player_names}\nPrepare for Round {game.round + 1}!"
    )

    await game_manager.start_round(game=game, telegram_client=telegram_client, prompt_manager=prompt_manager)

async def next_round(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    game = game_manager.get_game(group_id)

    player_names = ", ".join([p.username for p in game.players.values()])
    await telegram_client.send_message_to_group(
        group_id,
        f"🎬 Game starting with {len(game.players)} players: {player_names}\nPrepare for Round {game.round}!"
    )

    await game_manager.start_round(game=game, telegram_client=telegram_client, prompt_manager=prompt_manager)

async def join_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data.split("_")
    group_id = int(data[1])

    game = game_manager.get_game(group_id)
    if not game:
        await telegram_client.send_message_to_group(group_id, "That game no longer exists")
        return

    if game.locked:
        await telegram_client.send_message_to_group(group_id, "The game has already begun, cannot join now...")
        return

    if game.is_full(MAX_PLAYERS):
        await telegram_client.send_message_to_group(group_id, "The lobby is full!")
        return

    added = PlayerManager.add_player(game, user.id, user.username)
    if added:
        try:
            # Attempt to DM the new player
            await telegram_client.send_message_to_person(
                user.id,
                f"Welcome {user.username}! You’ve joined the game. You’ll receive your prompts here when the round starts."
            )
            # Notify the group that the player joined
            await telegram_client.send_message_to_group(
                group_id,
                f"{user.username} joined the game!"
            )
        except Exception as e:
            # Likely a Forbidden error (user hasn't started a chat)
            await telegram_client.send_message_to_group(
                group_id,
                f"{user.username} joined the game, but I can't DM them yet. "
                f"@{user.username}, please start a private chat with me: [@tele_quip_bot](https://t.me/tele_quip_bot)",
                parse_mode="Markdown"
            )
    else:
        await telegram_client.send_message_to_group(group_id, "You’re already in this game!")

async def handle_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reply_to = update.message.reply_to_message
    text = update.message.text

    if not reply_to:
        # not a reply, ignore or warn
        await update.message.reply_text("Please reply to the prompt message when it arrives")
        return

    message_id = reply_to.message_id

    # find game for this player
    for game in game_manager.active_games.values():
        if user_id in game.players:
            # check if this message_id maps to a prompt
            prompt_idx = game.prompt_messages.get(user_id, {}).get(message_id)
            if prompt_idx is None:
                await update.message.reply_text("That message isn't a valid prompt")
                return

            # store the answer
            game.pending_answers[user_id][prompt_idx] = text
            await update.message.reply_text("✅ Answer has been recorded")
            break

async def handle_poll_answer(update, context):
    poll_answer = update.poll_answer
    user_id = poll_answer.user.id
    poll_id = poll_answer.poll_id
    choice = poll_answer.option_ids[0]

    for game in game_manager.active_games.values():
        if poll_id in game.votes:
            game.votes[poll_id][user_id] = choice
            print(f"Vote registered: user {user_id} -> option {choice} for poll {poll_id}")

            # Once all players vote, calculate and update scores
            if len(game.votes[poll_id]) >= len(game.players):
                await game_manager.calculate_poll_score(game, poll_id)
            break

async def end_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id

    game = game_manager.get_game(group_id)
    if not game:
        await telegram_client.send_message_to_group(group_id, "No active game to end")
        return

    game_manager.stop_game(group_id)

    await telegram_client.send_message_to_group(
        group_id,
        "🛑 The current game has been ended. All systems reset."
    )

# -------------------------
# MAIN
# -------------------------
def main():
    app = ApplicationBuilder().token(BOT_KEY).build()

    # Test
    app.add_handler(CommandHandler("test", test_message))

    # Game commands
    app.add_handler(CommandHandler("init_game", init_game))
    app.add_handler(CommandHandler("start_game", start_game))
    app.add_handler(CommandHandler("next_round", next_round))
    app.add_handler(CommandHandler("end_game", end_game))

    # Callback for join button
    app.add_handler(CallbackQueryHandler(join_game, pattern="^join_"))

    # DM handler
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_dm))

    # Poll Handler
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
