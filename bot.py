import asyncio
import logging

from bot_instance import bot
from database import init_db, close_db

from handlers import client  # noqa: F401
from handlers import admin  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("farm_bot")

async def main():
    logger.info("Запуск бота семейной фермы на aiomax...")
    try:
        await init_db()
        await bot.start_polling()
    finally:
        await close_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
