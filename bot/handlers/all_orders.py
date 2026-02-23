from __future__ import annotations

from math import ceil
from typing import Optional, Iterable

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.db import repo
from bot.db.models import Role, OrderStatus
from bot.constants import order_status_label, BTN_ALL_ORDERS


router = Router()

# ✅ проектный стандарт: 10 заявок на страницу
PAGE_SIZE = 10

# сколько максимум тянуть из БД за раз (для MVP нормально)
MAX_LOAD = 1500


class AllOrdersStates(StatesGroup):
    wait_number = State()
    wait_phone = State()
    phone_view = State()


MODE_ALL = "all"
MODE_WORK = "work"
MODE_MOD = "mod"
MODE_NEEDS = "np"


def _is_admin_role(role: Role | None) -> bool:
    return role in (Role.SUPER_ADMIN, Role.DISPATCHER)


async def _get_user_or_none(tg_id: int):
    u = await repo.get_user_by_tg_id(tg_id)
    if not u:
        return None
    if u.role == Role.FIRED:
        return None
    return u


def _label_status(st) -> str:
    try:
        if callable(order_status_label):
            return str(order_status_label(st))
    except Exception:
        pass
    return getattr(st, "value", str(st))


def _mode_label(mode: str, master_name: Optional[str] = None) -> str:
    if mode == MODE_WORK:
        return "🚗 В работе"
    if mode == MODE_MOD:
        return "🧰 Модернизация (ДР)"
    if mode == MODE_NEEDS:
        if master_name:
            return f"💰 Нерасчитанные (мастер: {master_name})"
        return "💰 Нерасчитанные"
    return "📚 Все"


def _mode_statuses(mode: str) -> Optional[list[OrderStatus]]:
    if mode == MODE_WORK:
        return [OrderStatus.IN_WORK]
    if mode == MODE_MOD:
        return [OrderStatus.MODERNIZATION]
    if mode == MODE_NEEDS:
        return [OrderStatus.NEEDS_PAYOUT]
    return None


def _cb_page(page: int, mode: str, master_id: int) -> str:
    return f"all:page:{page}:{mode}:{master_id}"


def _kb_controls(page: int, pages: int, mode: str, master_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    # фильтры
    rows.append(
        [
            InlineKeyboardButton(text="📚 Все", callback_data=_cb_page(0, MODE_ALL, 0)),
            InlineKeyboardButton(text="🚗 В работе", callback_data=_cb_page(0, MODE_WORK, 0)),
            InlineKeyboardButton(text="🧰 ДР", callback_data=_cb_page(0, MODE_MOD, 0)),
            InlineKeyboardButton(text="💰 Нерасч.", callback_data=_cb_page(0, MODE_NEEDS, master_id if mode == MODE_NEEDS else 0)),
        ]
    )
    # фильтр по мастеру для нерасчитанных
    row2: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="👤 Нерасчитанные по мастеру", callback_data="all:pickmaster:0")
    ]
    if mode == MODE_NEEDS and master_id:
        row2.append(InlineKeyboardButton(text="❌ Убрать мастера", callback_data=_cb_page(0, MODE_NEEDS, 0)))
    rows.append(row2)

    # навигация страницы
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb_page(page - 1, mode, master_id)))
    nav_row.append(InlineKeyboardButton(text="🔎 Открыть по №", callback_data=f"all:ask:{mode}:{master_id}"))
    nav_row.append(InlineKeyboardButton(text="📱 Найти по телефону", callback_data=f"all:ask_phone:{mode}:{master_id}"))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️ Далее", callback_data=_cb_page(page + 1, mode, master_id)))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=_cb_page(page, mode, master_id))])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_open_buttons(order_ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for oid in order_ids:
        row.append(InlineKeyboardButton(text=f"Открыть #{oid}", callback_data=f"order_view:{oid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_order_id(text: str) -> Optional[int]:
    t = (text or "").strip()
    if not t:
        return None
    for pref in ("#", "№", "n", "N"):
        if t.startswith(pref):
            t = t[1:].strip()

    digits = "".join(ch for ch in t if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


async def _render_page_text(page: int, mode: str, master_id: int) -> tuple[str, list[int], int]:
    """
    Возвращает: (text, order_ids_on_page, total_pages)
    """
    statuses = _mode_statuses(mode)
    mname: Optional[str] = None
    if master_id:
        mu = await repo.get_user_by_id(int(master_id))
        if mu:
            mname = (mu.fio or f"id {master_id}")

    orders = await repo.list_orders_filtered(
        statuses=statuses,
        assigned_master_id=(int(master_id) if master_id else None),
        limit=MAX_LOAD,
    )

    total = len(orders)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))

    start = page * PAGE_SIZE
    chunk = orders[start : start + PAGE_SIZE]

    lines = [
        f"<b>{_mode_label(mode, mname)}</b>",
        f"Страница: <b>{page+1}/{pages}</b> | показаны: <b>{len(chunk)}</b> из <b>{total}</b>",
        "",
        "<code>#id | статус | город | техника</code>",
    ]

    ids: list[int] = []
    for o in chunk:
        oid = int(getattr(o, "id", 0) or 0)
        ids.append(oid)
        city = getattr(o, "city", None) or "—"
        offer_title = "—"
        off = getattr(o, "offer", None)
        if off and getattr(off, "title", None):
            offer_title = off.title
        st = getattr(o, "status", None)
        st_lbl = _label_status(st) if st is not None else "—"

        lines.append(f"<code>#{oid} | {st_lbl} | {city} | {offer_title}</code>")

    lines.append("")
    lines.append("Подсказка: можно нажать «🔎 Открыть по №» и ввести номер заявки.")

    return "\n".join(lines), ids, pages


async def _render_phone_page_text(digits: str, page: int) -> tuple[str, list[int], int, int]:
    """Возвращает: (text, ids_on_page, total_pages, total_found)"""
    dig = "".join(ch for ch in (digits or "") if ch.isdigit())
    total = await repo.count_orders_by_phone_digits(dig)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    offset = page * PAGE_SIZE

    orders = await repo.search_orders_by_phone_digits(dig, limit=PAGE_SIZE, offset=offset)

    lines = [
        "📱 <b>Поиск по телефону</b>",
        f"Ищу цифры: <b>{dig or '—'}</b>",
        f"Страница: <b>{page+1}/{pages}</b> | найдено: <b>{total}</b>",
        "",
        "<code>#id | статус | город | техника | телефон</code>",
    ]

    ids: list[int] = []
    for o in orders:
        oid = int(getattr(o, "id", 0) or 0)
        ids.append(oid)
        city = getattr(o, "city", None) or "—"
        offer_title = "—"
        off = getattr(o, "offer", None)
        if off and getattr(off, "title", None):
            offer_title = off.title
        st = getattr(o, "status", None)
        st_lbl = _label_status(st) if st is not None else "—"
        phone = getattr(o, "client_phone", None) or "—"

        lines.append(f"<code>#{oid} | {st_lbl} | {city} | {offer_title} | {phone}</code>")

    if total == 0:
        lines.append("")
        lines.append("Ничего не найдено. Нажмите «📱 Новый поиск» и попробуйте другие цифры.")

    return "\n".join(lines), ids, pages, total


def _kb_phone_page(page: int, pages: int, mode: str, master_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"all:phone_page:{page-1}:{mode}:{master_id}"))
    nav_row.append(InlineKeyboardButton(text="📱 Новый поиск", callback_data=f"all:ask_phone:{mode}:{master_id}"))
    nav_row.append(InlineKeyboardButton(text="📚 Назад к списку", callback_data=_cb_page(0, mode, master_id)))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"all:phone_page:{page+1}:{mode}:{master_id}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"all:phone_page:{page}:{mode}:{master_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_or_edit(message: Message, text: str, markup: InlineKeyboardMarkup):
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@router.message(F.text == BTN_ALL_ORDERS)
async def all_orders_entry(message: Message, state: FSMContext):
    u = await _get_user_or_none(message.from_user.id)
    if not u or not _is_admin_role(u.role):
        await message.answer("Раздел «Все заявки» доступен только админам/диспетчерам.")
        return

    await state.clear()
    mode, master_id = MODE_ALL, 0
    text, ids, pages = await _render_page_text(page=0, mode=mode, master_id=master_id)

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=_kb_controls(0, pages, mode, master_id))
    if ids:
        await message.answer("Открыть заявку:", reply_markup=_kb_open_buttons(ids))


@router.callback_query(F.data.startswith("all:page:"))
async def all_orders_page(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await _get_user_or_none(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    parts = (cb.data or "").split(":")
    # all:page:{page}:{mode}:{master_id}
    try:
        page = int(parts[2])
    except Exception:
        page = 0
    mode = parts[3] if len(parts) > 3 else MODE_ALL
    if mode not in (MODE_ALL, MODE_WORK, MODE_MOD, MODE_NEEDS):
        mode = MODE_ALL
    try:
        master_id = int(parts[4]) if len(parts) > 4 else 0
    except Exception:
        master_id = 0

    await state.clear()
    text, ids, pages = await _render_page_text(page=page, mode=mode, master_id=master_id)

    await cb.answer("Ок")
    await _send_or_edit(cb.message, text, _kb_controls(page, pages, mode, master_id))

    if ids:
        await cb.message.answer("Открыть заявку:", reply_markup=_kb_open_buttons(ids))


@router.callback_query(F.data.startswith("all:ask:"))
async def all_orders_ask_number(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await _get_user_or_none(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    # all:ask:{mode}:{master_id}
    parts = (cb.data or "").split(":")
    mode = parts[2] if len(parts) > 2 else MODE_ALL
    if mode not in (MODE_ALL, MODE_WORK, MODE_MOD, MODE_NEEDS):
        mode = MODE_ALL
    try:
        master_id = int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        master_id = 0

    await state.set_state(AllOrdersStates.wait_number)
    await state.update_data(mode=mode, master_id=master_id)

    await cb.answer()
    await cb.message.answer(
        "Введите номер заявки (например: <code>123</code> или <code>#123</code>)",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("all:ask_phone:"))
async def all_orders_ask_phone(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await _get_user_or_none(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    # all:ask_phone:{mode}:{master_id}
    parts = (cb.data or "").split(":")
    mode = parts[2] if len(parts) > 2 else MODE_ALL
    if mode not in (MODE_ALL, MODE_WORK, MODE_MOD, MODE_NEEDS):
        mode = MODE_ALL
    try:
        master_id = int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        master_id = 0

    await state.set_state(AllOrdersStates.wait_phone)
    await state.update_data(back_mode=mode, back_master_id=master_id)

    await cb.answer()
    await cb.message.answer(
        "Введите цифры телефона (обычно последние 4). Например: <code>7713</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(AllOrdersStates.wait_number)
async def all_orders_open_by_number(message: Message, state: FSMContext):
    u = await _get_user_or_none(message.from_user.id)
    if not u or not _is_admin_role(u.role):
        await state.clear()
        await message.answer("Недостаточно прав.")
        return

    oid = _parse_order_id(message.text or "")
    if not oid:
        await message.answer(
            "Не понял номер. Введите, например: <code>123</code> или <code>#123</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Открыть #{oid}", callback_data=f"order_view:{oid}")]
        ]
    )
    await message.answer(
        f"Готово. Нажмите кнопку, чтобы открыть заявку <b>#{oid}</b>:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


@router.message(AllOrdersStates.wait_phone)
async def all_orders_search_by_phone(message: Message, state: FSMContext):
    u = await _get_user_or_none(message.from_user.id)
    if not u or not _is_admin_role(u.role):
        await state.clear()
        await message.answer("Недостаточно прав.")
        return

    dig = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if len(dig) < 4:
        await message.answer("Нужно минимум <b>4 цифры</b>. Например: <code>7713</code>", parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    mode = (data or {}).get("back_mode") or MODE_ALL
    try:
        master_id = int((data or {}).get("back_master_id") or 0)
    except Exception:
        master_id = 0

    await state.set_state(AllOrdersStates.phone_view)
    await state.update_data(phone_digits=dig, back_mode=mode, back_master_id=master_id)

    text, ids, pages, _total = await _render_phone_page_text(dig, page=0)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=_kb_phone_page(0, pages, mode, master_id))
    if ids:
        await message.answer("Открыть заявку:", reply_markup=_kb_open_buttons(ids))


@router.callback_query(F.data.startswith("all:phone_page:"))
async def all_orders_phone_page(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await _get_user_or_none(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    data = await state.get_data()
    dig = (data or {}).get("phone_digits")
    if not dig:
        await cb.answer("Сначала запустите поиск по телефону", show_alert=True)
        return

    # all:phone_page:{page}:{mode}:{master_id}
    parts = (cb.data or "").split(":")
    try:
        page = int(parts[2])
    except Exception:
        page = 0
    mode = parts[3] if len(parts) > 3 else ((data or {}).get("back_mode") or MODE_ALL)
    if mode not in (MODE_ALL, MODE_WORK, MODE_MOD, MODE_NEEDS):
        mode = MODE_ALL
    try:
        master_id = int(parts[4]) if len(parts) > 4 else int((data or {}).get("back_master_id") or 0)
    except Exception:
        master_id = int((data or {}).get("back_master_id") or 0)

    text, ids, pages, _total = await _render_phone_page_text(str(dig), page=page)
    await cb.answer("Ок")
    await _send_or_edit(cb.message, text, _kb_phone_page(page, pages, mode, master_id))
    if ids:
        await cb.message.answer("Открыть заявку:", reply_markup=_kb_open_buttons(ids))


# ------------------------
# Выбор мастера для фильтра «Нерасчитанные по мастеру»
# ------------------------
def _kb_pick_master(masters: list, page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    # по 2 мастера в ряд
    row: list[InlineKeyboardButton] = []
    for u in masters:
        title = (u.fio or f"id {u.id}").strip()
        city = (getattr(u, "city", None) or "").strip()
        if city:
            title = f"{title} ({city})"
        if len(title) > 32:
            title = title[:29] + "…"
        row.append(InlineKeyboardButton(text=title, callback_data=f"all:setmaster:{u.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"all:pickmaster:{page-1}"))
    nav.append(InlineKeyboardButton(text="📚 К списку", callback_data=_cb_page(0, MODE_NEEDS, 0)))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"all:pickmaster:{page+1}"))
    rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("all:pickmaster:"))
async def all_orders_pick_master(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await _get_user_or_none(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    parts = (cb.data or "").split(":")
    try:
        page = int(parts[2])
    except Exception:
        page = 0

    all_masters = await repo.list_masters(include_unapproved=True)
    # уберём уволенных/не-мастеров на всякий случай
    all_masters = [m for m in all_masters if getattr(m, "role", None) == Role.MASTER]

    total = len(all_masters)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    chunk = all_masters[start : start + PAGE_SIZE]

    text = (
        "<b>👤 Нерасчитанные по мастеру</b>\n"
        "Выберите мастера — покажу заявки со статусом <b>NEEDS_PAYOUT</b> только по нему."
    )

    await cb.answer()
    try:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_kb_pick_master(chunk, page, pages))
    except Exception:
        await cb.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=_kb_pick_master(chunk, page, pages))


@router.callback_query(F.data.startswith("all:setmaster:"))
async def all_orders_set_master(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await _get_user_or_none(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    parts = (cb.data or "").split(":")
    try:
        master_id = int(parts[2])
    except Exception:
        master_id = 0

    await state.clear()
    text, ids, pages = await _render_page_text(page=0, mode=MODE_NEEDS, master_id=master_id)

    await cb.answer("Ок")
    await _send_or_edit(cb.message, text, _kb_controls(0, pages, MODE_NEEDS, master_id))

    if ids:
        await cb.message.answer("Открыть заявку:", reply_markup=_kb_open_buttons(ids))
