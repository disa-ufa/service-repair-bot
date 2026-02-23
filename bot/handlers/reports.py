from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("reports"))
async def reports_help(message: Message):
    text = (
        "ℹ️ <b>Отчёты</b>\n\n"
        "Команды отчётов перенесены:\n"
        "• <code>/payouts</code> или <code>/payouts 7</code> или <code>/payouts 01.02.2026 15.02.2026</code>\n"
        "• <code>/masters_summary</code> или <code>/masters_summary 7</code>\n"
        "• <code>/payouts_csv</code> — выгрузка CSV\n"
        "• <code>/masters_summary_csv</code> — выгрузка CSV\n\n"
        "Интерактивная сводка (кнопки/периоды/CSV):\n"
        "• <code>/masters_ui</code> (или <code>/ms</code>)\n"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)
