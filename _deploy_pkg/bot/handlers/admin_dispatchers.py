from __future__ import annotations

import re
from typing import List, Optional

from aiogram import F, Router
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext

from bot.db import repo
from bot.db.models import Role, User
from bot.states_admin import AdminDispatchers

from bot.constants import BTN_DISPATCHERS

router = Router()

CB_REFRESH = "disp:refresh"
CB_ADD = "disp:add"
CB_CLOSE = "disp:close"
CB_DEL_PREFIX = "disp:del:"


def _is_digits(s: str) -> bool:
    return bool(re.fullmatch(r"\d{5,20}", s.strip()))


def _kb(dispatchers: List[User]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    rows.append(
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data=CB_ADD),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=CB_REFRESH),
        ]
    )

    for u in dispatchers[:30]:
        fio = (getattr(u, "fio", "") or "").strip() or f"tg_id {u.tg_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Удалить: {fio}",
                    callback_data=f"{CB_DEL_PREFIX}{u.tg_id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="✖ Закрыть", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text(dispatchers: List[User]) -> str:
    if not dispatchers:
        return "🧑‍💼 <b>Диспетчеры</b>\n\nПока диспетчеров нет."
    lines = ["🧑‍💼 <b>Диспетчеры</b>:"]
    for u in dispatchers[:50]:
        fio = (getattr(u, "fio", "") or "").strip() or "—"
        lines.append(f"• {fio} (tg_id=<code>{u.tg_id}</code>)")
    if len(dispatchers) > 50:
        lines.append(f"\nПоказаны первые 50 из {len(dispatchers)}.")
    return "\n".join(lines)


async def _require_super_admin(tg_id: int) -> Optional[User]:
    u = await repo.get_user_by_tg_id(tg_id)
    if not u:
        return None
    if u.role == Role.FIRED:
        return None
    if u.role != Role.SUPER_ADMIN:
        return None
    return u


async def _render(message_or_callback) -> tuple[str, InlineKeyboardMarkup]:
    dispatchers = await repo.list_users_by_role(Role.DISPATCHER)
    return _text(dispatchers), _kb(dispatchers)


@router.message(F.text == BTN_DISPATCHERS)
async def dispatchers_entry(message: Message, state: FSMContext):
    admin = await _require_super_admin(message.from_user.id)
    if not admin:
        await message.answer("Недостаточно прав. Только SUPER_ADMIN.")
        return

    await state.clear()
    text, kb = await _render(message)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == CB_REFRESH)
async def dispatchers_refresh(cb: CallbackQuery):
    admin = await _require_super_admin(cb.from_user.id)
    if not admin:
        await cb.answer("Недостаточно прав", show_alert=True)
        return

    text, kb = await _render(cb)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == CB_CLOSE)
async def dispatchers_close(cb: CallbackQuery, state: FSMContext):
    admin = await _require_super_admin(cb.from_user.id)
    if not admin:
        await cb.answer("Недостаточно прав", show_alert=True)
        return

    await state.clear()
    await cb.message.edit_text("Ок, закрыто.")
    await cb.answer()


@router.callback_query(F.data == CB_ADD)
async def dispatchers_add(cb: CallbackQuery, state: FSMContext):
    admin = await _require_super_admin(cb.from_user.id)
    if not admin:
        await cb.answer("Недостаточно прав", show_alert=True)
        return

    await state.set_state(AdminDispatchers.wait_tg_id)
    await cb.message.answer(
        "Отправьте <b>tg_id</b> нового диспетчера (числом).\n\n"
        "Можно также <b>ответить</b> на сообщение пользователя и отправить любое число — "
        "я возьму tg_id из reply.\n\n"
        "Отмена: напишите <code>отмена</code>."
    )
    await cb.answer()


@router.message(AdminDispatchers.wait_tg_id)
async def dispatchers_add_tg_id(message: Message, state: FSMContext):
    admin = await _require_super_admin(message.from_user.id)
    if not admin:
        await message.answer("Недостаточно прав. Только SUPER_ADMIN.")
        await state.clear()
        return

    raw = (message.text or "").strip().lower()
    if raw in {"отмена", "cancel"}:
        await state.clear()
        text, kb = await _render(message)
        await message.answer("Отменено.\n\n" + text, reply_markup=kb)
        return

    # 1) если ответом на сообщение — берем tg_id из reply
    target_tg_id: Optional[int] = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_tg_id = message.reply_to_message.from_user.id

    # 2) иначе пробуем из текста
    if target_tg_id is None:
        if not _is_digits(message.text or ""):
            await message.answer("Не похоже на tg_id. Пришлите число (например: <code>7301465713</code>) или напишите <code>отмена</code>.")
            return
        target_tg_id = int((message.text or "").strip())

    # запрет трогать самого себя
    if target_tg_id == message.from_user.id:
        await message.answer("Нельзя назначить диспетчером самого себя таким способом 🙂")
        return

    # если пользователя нет — создаём минимально
    u = await repo.get_user_by_tg_id(target_tg_id)
    if u is None:
        await repo.upsert_user(
            tg_id=target_tg_id,
            username=None,
            fio=f"tg_id {target_tg_id}",
            role=Role.DISPATCHER,
        )
    else:
        if u.role == Role.SUPER_ADMIN:
            await message.answer("Этот пользователь SUPER_ADMIN — его нельзя сделать диспетчером.")
            await state.clear()
            return
        await repo.set_user_role(target_tg_id, Role.DISPATCHER)

    await state.clear()
    text, kb = await _render(message)
    await message.answer("✅ Диспетчер добавлен.\n\n" + text, reply_markup=kb)


@router.callback_query(F.data.startswith(CB_DEL_PREFIX))
async def dispatchers_delete(cb: CallbackQuery):
    admin = await _require_super_admin(cb.from_user.id)
    if not admin:
        await cb.answer("Недостаточно прав", show_alert=True)
        return

    try:
        target_tg_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Некорректные данные", show_alert=True)
        return

    if target_tg_id == cb.from_user.id:
        await cb.answer("Нельзя удалить самого себя", show_alert=True)
        return

    target = await repo.get_user_by_tg_id(target_tg_id)
    if not target:
        await cb.answer("Пользователь не найден", show_alert=True)
        return

    if target.role == Role.SUPER_ADMIN:
        await cb.answer("Нельзя удалить SUPER_ADMIN", show_alert=True)
        return

    # снимаем диспетчера: делаем обычным мастером
    await repo.set_user_role(target_tg_id, Role.MASTER)

    text, kb = await _render(cb)
    await cb.message.edit_text("✅ Диспетчер удалён.\n\n" + text, reply_markup=kb)
    await cb.answer()
