import os
from dotenv import load_dotenv

load_dotenv()

BOT_KEY = os.getenv("BOT_KEY")
MAX_PLAYERS = 8
ROUND_COUNT = 3
RESPONSE_TIMEOUT = 65  # seconds
TIME_GIFS = {
    35: "data/30s.gif",
    65: "data/60s.gif",
    75: "data/70s.gif"
}