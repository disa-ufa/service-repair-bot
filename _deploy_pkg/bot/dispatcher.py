from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Config

from bot.handlers.start import router as start_router
from bot.handlers.menu_actions import router as menu_actions_router

from bot.handlers.admin_masters import router as admin_masters_router

# ✅ НОВОЕ: управление справочником техники (офферами)
from bot.handlers.admin_offers import router as admin_offers_router
from bot.handlers.orders import router as orders_router
from bot.handlers.all_orders import router as all_orders_router
from bot.handlers.orders_reports import router as orders_reports_router

from bot.handlers.admin_roles import router as admin_roles_router
from bot.handlers.user_roles import router as user_roles_router

from bot.handlers.finance import router as finance_router

# ✅ НОВОЕ: управление диспетчерами
from bot.handlers.admin_dispatchers import router as admin_dispatchers_router
from bot.handlers.admin_requisites import router as admin_requisites_router

from bot.services.no_accept_alert import no_accept_watchdog


async def _on_startup(dispatcher: Dispatcher, bot: Bot) -> None:
    cfg: Config = dispatcher["cfg"]
    dispatcher["no_accept_task"] = asyncio.create_task(
        no_accept_watchdog(bot, cfg, interval_seconds=60)
    )


async def _on_shutdown(dispatcher: Dispatcher, bot: Bot) -> None:
    task: asyncio.Task | None = dispatcher.get("no_accept_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def build_dispatcher(cfg: Config) -> tuple[Dispatcher, Bot]:
    bot = Bot(token=cfg.bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())

    dp["cfg"] = cfg

    dp.include_router(start_router)
    dp.include_router(menu_actions_router)

    dp.include_router(admin_masters_router)

    # “Каталог техники” (только супер-админ)
    dp.include_router(admin_offers_router)

    # “Диспетчеры”
    dp.include_router(admin_dispatchers_router)

    dp.include_router(admin_requisites_router)

    dp.include_router(orders_router)
    dp.include_router(all_orders_router)
    dp.include_router(orders_reports_router)

    dp.include_router(admin_roles_router)
    dp.include_router(user_roles_router)

    dp.include_router(finance_router)

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    return dp, bot
