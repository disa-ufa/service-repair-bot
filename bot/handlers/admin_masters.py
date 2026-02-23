from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.db import repo
from bot.db.models import Role

# Кнопки из constants.py (если есть). Если нет — fallback.
try:
    from bot.constants import BTN_MASTERS
except Exception:  # pragma: no cover
    BTN_MASTERS = "👥 Мастера"

router = Router()


def _is_admin(role: Role) -> bool:
    return role in (Role.SUPER_ADMIN, Role.DISPATCHER)


def _is_super_admin(role: Role) -> bool:
    return role == Role.SUPER_ADMIN


def _masters_kb(masters, can_approve: bool) -> InlineKeyboardMarkup:
    rows = []
    for m in masters:
        name = (m.fio or f"tg:{m.tg_id}").strip()
        status = "✅" if getattr(m, "is_approved", False) else "⏳"
        # 1-я кнопка — открыть мастера (офферы)
        row = [InlineKeyboardButton(text=f"{status} 👤 {name}"[:64], callback_data=f"am:pick:{m.id}")]
        # 2-я кнопка — подтвердить (ТОЛЬКО супер-админ), только если не подтвержден
        if can_approve and not getattr(m, "is_approved", False):
            row.append(InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"am:approve:{m.id}"))
        # 3-я кнопка — уволить/заблокировать мастера
        if can_approve:
            row.append(InlineKeyboardButton(text="🚫 Уволить", callback_data=f"am:fire:{m.id}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="am:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _offers_kb(master_id: int, offers, selected_ids: set[int]) -> InlineKeyboardMarkup:
    rows = []
    for o in offers:
        mark = "✅" if o.id in selected_ids else "➖"
        rows.append([InlineKeyboardButton(text=f"{mark} {o.title}", callback_data=f"am:toggle:{master_id}:{o.id}")])

    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="am:back"),
            InlineKeyboardButton(text="✅ Готово", callback_data=f"am:done:{master_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _deny_if_not_admin_message(message: Message) -> bool:
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return True
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован. Обратитесь к администратору.")
        return True
    if not _is_admin(u.role):
        await message.answer("⛔ Доступно только администратору/диспетчеру.")
        return True
    return False


async def _deny_if_not_admin_cb(cb: CallbackQuery) -> bool:
    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.answer("Сначала /start", show_alert=True)
        return True
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return True
    if not _is_admin(u.role):
        await cb.answer("Недостаточно прав", show_alert=True)
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
    if not _is_super_admin(u.role):
        await cb.answer("Только супер-администратор", show_alert=True)
        return True
    return False



@router.message(F.text.in_({BTN_MASTERS, "👥 Мастера"}))
async def admin_open_masters(message: Message):
    if await _deny_if_not_admin_message(message):
        return

    admin_u = await repo.get_user_by_tg_id(message.from_user.id)
    can_approve = bool(admin_u and _is_super_admin(admin_u.role))

    masters = await repo.list_masters(include_unapproved=True)
    if not masters:
        await message.answer(
            "Пока нет зарегистрированных мастеров.\n"
            "Пусть мастер зайдёт в бота и пройдет регистрацию."
        )
        return

    await message.answer(
        "Выберите мастера:",
        reply_markup=_masters_kb(masters, can_approve),
    )


@router.callback_query(F.data == "am:close")
async def admin_masters_close(cb: CallbackQuery):
    if await _deny_if_not_super_admin_cb(cb):
        return
    await cb.message.edit_text("Ок, закрыл список мастеров.")
    await cb.answer()


@router.callback_query(F.data == "am:back")
async def admin_masters_back(cb: CallbackQuery):
    if await _deny_if_not_admin_cb(cb):
        return

    admin_u = await repo.get_user_by_tg_id(cb.from_user.id)
    can_approve = bool(admin_u and _is_super_admin(admin_u.role))

    masters = await repo.list_masters(include_unapproved=True)
    if not masters:
        await cb.message.edit_text("Пока нет зарегистрированных мастеров.")
        await cb.answer()
        return

    await cb.message.edit_text("Выберите мастера:", reply_markup=_masters_kb(masters, can_approve))
    await cb.answer()


@router.callback_query(F.data.startswith("am:pick:"))
async def admin_pick_master(cb: CallbackQuery):
    if await _deny_if_not_admin_cb(cb):
        return

    try:
        master_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Ошибка master_id", show_alert=True)
        return

    master = await repo.get_user_by_id(master_id)
    if not master or master.role != Role.MASTER:
        await cb.answer("Мастер не найден", show_alert=True)
        return

    offers = await repo.list_offers()
    selected = await repo.get_master_offer_ids(master_id)

    await cb.message.edit_text(
        f"Мастер: <b>{master.fio}</b>\n"
        f"Выберите офферы (техника). Нажатие включает/выключает.\n"
        f"Выбрано: <b>{len(selected)}</b>",
        reply_markup=_offers_kb(master_id, offers, selected),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("am:toggle:"))
async def admin_toggle_offer(cb: CallbackQuery):
    if await _deny_if_not_admin_cb(cb):
        return

    # am:toggle:<master_id>:<offer_id>
    try:
        _p = cb.data.split(":")
        master_id = int(_p[2])
        offer_id = int(_p[3])
    except Exception:
        await cb.answer("Ошибка параметров", show_alert=True)
        return

    await repo.toggle_master_offer(master_id, offer_id)

    master = await repo.get_user_by_id(master_id)
    offers = await repo.list_offers()
    selected = await repo.get_master_offer_ids(master_id)

    title = master.fio if master else f"ID {master_id}"
    await cb.message.edit_text(
        f"Мастер: <b>{title}</b>\n"
        f"Выберите офферы (техника). Нажатие включает/выключает.\n"
        f"Выбрано: <b>{len(selected)}</b>",
        reply_markup=_offers_kb(master_id, offers, selected),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Обновлено")


@router.callback_query(F.data.startswith("am:done:"))
async def admin_done(cb: CallbackQuery):
    if await _deny_if_not_admin_cb(cb):
        return

    try:
        master_id = int(cb.data.split(":")[-1])
    except Exception:
        master_id = 0

    master = await repo.get_user_by_id(master_id) if master_id else None
    name = master.fio if master else "мастера"

    await cb.message.edit_text(f"✅ Готово! Настройки офферов сохранены для <b>{name}</b>.", parse_mode=ParseMode.HTML)
    await cb.answer()



@router.callback_query(F.data.startswith("am:approve:"))
async def admin_approve_master(cb: CallbackQuery):
    # Подтверждать (активировать) мастера может ТОЛЬКО супер-админ
    if await _deny_if_not_super_admin_cb(cb):
        return

    try:
        master_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Ошибка master_id", show_alert=True)
        return

    master = await repo.get_user_by_id(master_id)
    if not master or master.role != Role.MASTER:
        await cb.answer("Мастер не найден", show_alert=True)
        return

    if getattr(master, "is_approved", False):
        await cb.answer("Уже подтвержден")
        return

    await repo.set_user_approved(master_id, True)

    # Уведомляем мастера
    try:
        await cb.bot.send_message(
            master.tg_id,
            "✅ Ваш аккаунт мастера подтвержден.\nНажмите /start, чтобы открыть меню.",
        )
    except Exception:
        pass

    # Обновляем список
    admin_u = await repo.get_user_by_tg_id(cb.from_user.id)
    can_approve = bool(admin_u and _is_super_admin(admin_u.role))

    masters = await repo.list_masters(include_unapproved=True)
    await cb.message.edit_text("Выберите мастера:", reply_markup=_masters_kb(masters, can_approve))
    await cb.answer("✅ Подтвержден")


@router.callback_query(F.data.startswith("am:fire:"))
async def admin_fire_master(cb: CallbackQuery):
    # Увольнять/блокировать мастера может только SUPER_ADMIN
    if await _deny_if_not_super_admin_cb(cb):
        return

    try:
        master_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Ошибка master_id", show_alert=True)
        return

    master = await repo.get_user_by_id(master_id)
    if not master or master.role != Role.MASTER:
        await cb.answer("Мастер не найден", show_alert=True)
        return

    # Блокируем: роль FIRED + снимаем подтверждение
    await repo.set_user_role(master.tg_id, Role.FIRED)
    await repo.set_user_approved(master_id, False)

    # Уведомляем мастера
    try:
        await cb.bot.send_message(
            master.tg_id,
            "🚫 Ваш доступ к боту заблокирован (уволен). Если это ошибка — обратитесь к администратору.",
        )
    except Exception:
        pass

    # Обновляем список
    admin_u = await repo.get_user_by_tg_id(cb.from_user.id)
    can_approve = bool(admin_u and _is_super_admin(admin_u.role))

    masters = await repo.list_masters(include_unapproved=True)
    await cb.message.edit_text("Выберите мастера:", reply_markup=_masters_kb(masters, can_approve))
    await cb.answer("🚫 Уволен")


# ------------------------
# Команды для управления офферами (админ)
# ------------------------
@router.message(Command("offers"))
async def cmd_offers(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u or not _is_admin(u.role):
        return

    offers = await repo.list_offers()
    if not offers:
        await message.answer("Офферов пока нет. Добавьте: /offer_add <название>")
        return

    txt = "<b>Офферы:</b>\n" + "\n".join([f"• {o.title} (id={o.id})" for o in offers])
    await message.answer(txt, parse_mode=ParseMode.HTML)


@router.message(Command("offer_add"))
async def cmd_offer_add(message: Message, command: CommandObject):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u or not _is_admin(u.role):
        return

    title = (command.args or "").strip()
    if not title:
        await message.answer("Использование: /offer_add <название>")
        return

    off = await repo.create_offer(title)
    await message.answer(f"✅ Оффер создан/обновлён: <b>{off.title}</b> (id={off.id})", parse_mode=ParseMode.HTML)