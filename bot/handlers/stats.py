from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from bot.db import repo

router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    total = await repo.count_users()

    if u:
        await message.answer(
            f"✅ Ваш Telegram ID: <b>{message.from_user.id}</b>\n"
            f"Пользователь: <b>{u.fio}</b>\n"
            f"Роль: <b>{u.role.value}</b>\n"
            f"Всего пользователей в БД: <b>{total}</b>"
        )
    else:
        await message.answer(
            f"✅ Ваш Telegram ID: <b>{message.from_user.id}</b>\n"
            f"Вы еще не зарегистрированы. Наберите /start\n"
            f"Всего пользователей в БД: <b>{total}</b>"
        )
