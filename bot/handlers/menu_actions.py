# bot/handlers/menu_actions.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.db import repo
from bot.db.models import Role
from bot.handlers.menu import menu_for_role
from bot.constants import BTN_RESET

router = Router()

@router.message(Command("menu"))
async def cmd_menu(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Сначала зарегистрируйтесь: отправьте /start")
        return
    await message.answer("Меню:", reply_markup=menu_for_role(u.role))


@router.callback_query(F.data == "open_admin_menu")
async def open_admin_menu(callback: CallbackQuery):
    u = await repo.get_user_by_tg_id(callback.from_user.id)
    if not u:
        await callback.answer("Сначала зарегистрируйтесь (/start)", show_alert=True)
        return
    if u.role not in (Role.SUPER_ADMIN, Role.DISPATCHER):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await callback.message.answer("Меню администратора:", reply_markup=menu_for_role(u.role))
    await callback.answer()


@router.callback_query(F.data == "open_master_menu")
async def open_master_menu(callback: CallbackQuery):
    u = await repo.get_user_by_tg_id(callback.from_user.id)
    if not u:
        await callback.answer("Сначала зарегистрируйтесь (/start)", show_alert=True)
        return
    if u.role != Role.MASTER:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await callback.message.answer("Меню мастера:", reply_markup=menu_for_role(u.role))
    await callback.answer()


# поддержим и старый текст "🧹 Сброс", чтобы не ломать
@router.message(F.text.in_({BTN_RESET, "🧹 Сброс"}))
async def reset_fsm(message: Message, state: FSMContext):
    await state.clear()
    u = await repo.get_user_by_tg_id(message.from_user.id)
    kb = menu_for_role(u.role) if u else None
    await message.answer("Сбросил текущий сценарий.", reply_markup=kb)
