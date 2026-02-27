import asyncio
import logging

from bot.config import load_config
from bot.db import init_db
from bot.dispatcher import build_dispatcher


async def main() -> None:
    cfg = load_config("config.yml")

    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db(cfg.db_url)
    dp, bot = build_dispatcher(cfg)

    # Передаем cfg в aiogram dependency injection, чтобы хендлеры могли принимать cfg: Config
    await dp.start_polling(bot, cfg=cfg)


if __name__ == "__main__":
    asyncio.run(main())
