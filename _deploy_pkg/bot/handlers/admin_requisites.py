from __future__ import annotations

from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.db import repo
from bot.db.models import Role
from bot.states_admin import AdminRequisites

try:
    from bot.constants import BTN_PAYOUT_REQUISITES
except Exception:  # pragma: no cover
    BTN_PAYOUT_REQUISITES = "🏦 Реквизиты для сдачи"


router = Router()


def _kb_manage() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="req:edit")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="req:close")],
        ]
    )


def _fmt(bank: str, phone: str) -> str:
    b = (bank or "").strip() or "—"
    p = (phone or "").strip() or "—"
    return (
        "<b>Реквизиты для сдачи денег</b>\n"
        f"Банк: <b>{b}</b>\n"
        f"Телефон: <b>{p}</b>"
    )


async def _deny_if_not_super_admin_msg(message: Message) -> bool:
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Сначала /start")
        return True
    if u.role == Role.FIRED:
        await message.answer("Доступ заблокирован")
        return True
    if u.role != Role.SUPER_ADMIN:
        await message.answer("⛔ Только супер-администратор")
        return True
    return False


async def _deny_if_not_super_admin_cb(cb: CallbackQuery) -> bool:
    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.answer("Сначала /start", show_alert=True)
        return True
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return True
    if u.role != Role.SUPER_ADMIN:
        await cb.answer("Только супер-администратор", show_alert=True)
        return True
    return False


@router.message(F.text == BTN_PAYOUT_REQUISITES)
async def open_requisites(message: Message, state: FSMContext):
    if await _deny_if_not_super_admin_msg(message):
        return
    await state.clear()

    bank, phone = await repo.get_payout_requisites()
    await message.answer(_fmt(bank, phone), reply_markup=_kb_manage(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "req:close")
async def close_requisites(cb: CallbackQuery, state: FSMContext):
    if await _deny_if_not_super_admin_cb(cb):
        return
    await state.clear()
    if cb.message:
        await cb.message.edit_text("Ок, закрыл.", parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "req:edit")
async def edit_requisites(cb: CallbackQuery, state: FSMContext):
    if await _deny_if_not_super_admin_cb(cb):
        return
    await state.clear()
    await state.set_state(AdminRequisites.wait_bank)

    bank, phone = await repo.get_payout_requisites()
    hint = f"\n\nТекущее значение: <b>{(bank or '').strip() or '—'}</b>"
    if cb.message:
        await cb.message.answer(
            "Введите <b>банк</b> для сдачи денег (например: Сбер / Тинькофф)."
            "\nМожно отправить <code>-</code>, чтобы очистить." + hint,
            parse_mode=ParseMode.HTML,
        )
    await cb.answer()


@router.message(AdminRequisites.wait_bank, F.text)
async def save_bank(message: Message, state: FSMContext):
    if message.text.startswith("/"):
        await message.answer("Пожалуйста, введите банк текстом (не командой).")
        return
    bank = message.text.strip()
    if bank in {"-", "—"}:
        bank = ""
    await state.update_data(bank=bank)

    _, phone = await repo.get_payout_requisites()
    hint = f"\n\nТекущее значение: <b>{(phone or '').strip() or '—'}</b>"
    await message.answer(
        "Теперь введите <b>телефон</b> для сдачи денег (пример: +7 999 123-45-67)."
        "\nМожно отправить <code>-</code>, чтобы очистить." + hint,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AdminRequisites.wait_phone)


@router.message(AdminRequisites.wait_phone, F.text)
async def save_phone(message: Message, state: FSMContext):
    if message.text.startswith("/"):
        await message.answer("Пожалуйста, введите телефон текстом (не командой).")
        return
    phone = message.text.strip()
    if phone in {"-", "—"}:
        phone = ""

    data = await state.get_data()
    bank = (data.get("bank") or "").strip()

    await repo.set_payout_requisites(bank, phone)
    await state.clear()

    bank2, phone2 = await repo.get_payout_requisites()
    await message.answer("✅ Сохранено!\n\n" + _fmt(bank2, phone2), parse_mode=ParseMode.HTML)
