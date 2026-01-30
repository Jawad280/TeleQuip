import os
from dotenv import load_dotenv

load_dotenv()

BOT_KEY = os.getenv("BOT_KEY")
MAX_PLAYERS = 8
ROUND_COUNT = 3

# seconds
RESPONSE_TIMEOUT = 30  
LAST_REMINDER_TIME=10
POLL_TIMING = 10