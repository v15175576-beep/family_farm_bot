import os

import aiomax
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения!")

bot = aiomax.Bot(TOKEN, default_format="html", use_certificate=True)
