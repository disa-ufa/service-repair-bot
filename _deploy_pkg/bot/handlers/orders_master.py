from __future__ import annotations

from typing import Optional, Tuple
from html import escape as html_escape

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.db import repo
from bot.db.models import OrderStatus, OrderType, Role, Order
from bot.states import CloseOrder, PayoutProof, ModernizationComment

# ------------------------
# Buttons + labels (fallback)
# ------------------------
try:
    from bot.constants import (
        BTN_AVAILABLE_ORDERS,
        BTN_MY_ORDERS,
        BTN_CASH,
        order_status_label,
        order_type_label,
    )
except Exception:  # pragma: no cover
    BTN_AVAILABLE_ORDERS = "📋 Доступные заказы"
    BTN_MY_ORDERS = "🧰 Мои заказы"
    BTN_CASH = "💵 Касса"

    def order_status_label(s: OrderStatus) -> str:
        return getattr(s, "value", str(s))

    def order_type_label(t: OrderType) -> str:
        return getattr(t, "value", str(t))


router = Router()


def _kb_inline(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)



def _order_hide_only_kb(order_id: int | None = None) -> InlineKeyboardMarkup:
    cb = f"order_hide:{order_id}" if order_id is not None else "order_hide"
    return _kb_inline([[InlineKeyboardButton(text="🗑 Убрать", callback_data=cb)]])


@router.callback_query(F.data.startswith("order_hide"))
async def order_hide(cb: CallbackQuery, bot: Bot):
    """Скрыть уведомление о заявке только у мастера, который нажал кнопку."""

    order_id: int | None = None
    try:
        parts = (cb.data or "").split(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            order_id = int(parts[1])
    except Exception:
        order_id = None

    # Удаляем только текущее сообщение в этом чате.
    try:
        if cb.message:
            await bot.delete_message(chat_id=cb.message.chat.id, message_id=cb.message.message_id)
    except Exception:
        pass

    # Если в БД есть записи о рассылке сообщений — чистим запись только для этого мастера,
    # чтобы при принятии заявки другим мастером не пытаться удалить уже удалённое сообщение.
    if order_id is not None:
        try:
            u = await repo.get_user_by_tg_id(cb.from_user.id)
            if u and getattr(u, "id", None) is not None:
                await repo.delete_order_dispatch_messages(order_id=order_id, user_id=u.id)
        except Exception:
            pass

    await cb.answer("Убрано")

async def _get_user_or_ask_start(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return None
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован. Обратитесь к администратору.")
        return None

    # Мастер должен быть подтвержден супер-админом
    if u.role == Role.MASTER and not getattr(u, "is_approved", False):
        await message.answer(
            "⏳ Ваш аккаунт мастера еще не подтвержден диспетчером.\n"
            "Дождитесь активации и нажмите /start."
        )
        return None

    return u


async def _notify_admins(bot: Bot, cfg: Config, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    """
    Уведомления админской стороне.

    Отправляем:
    - всем tg_id из config.yml (cfg.admins)
    - всем пользователям в БД с ролью SUPER_ADMIN и DISPATCHER

    Это нужно, чтобы диспетчер и супер-админ всегда получали одинаковые уведомления
    (закрытие, сдача денег, подтверждения и т.д.), даже если их tg_id не внесён в cfg.admins.
    """
    tg_ids: set[int] = set()

    # 1) Из конфига
    try:
        for x in (cfg.admins or []):
            try:
                tg_ids.add(int(x))
            except Exception:
                continue
    except Exception:
        pass

    # 2) Из БД по ролям
    try:
        for role in (Role.SUPER_ADMIN, Role.DISPATCHER):
            users = await repo.list_users_by_role(role)
            for u in users:
                tid = getattr(u, "tg_id", None)
                if tid:
                    try:
                        tg_ids.add(int(tid))
                    except Exception:
                        continue
    except Exception:
        pass

    for admin_tg_id in sorted(tg_ids):
        try:
            await bot.send_message(admin_tg_id, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception:
            continue


def _order_send_payout_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline([[InlineKeyboardButton(text="💸 Отправить скрин перевода", callback_data=f"order_payout:{order_id}")]])


def _order_master_actions_kb(order_id: int, status: OrderStatus) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if status == OrderStatus.ACCEPTED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚗 В работе",
                    callback_data=f"order_status:{order_id}:{OrderStatus.IN_WORK.value}",
                )
            ]
        )

    if status in (OrderStatus.IN_WORK, OrderStatus.ACCEPTED):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧰 Модернизация (ДР)",
                    callback_data=f"order_status:{order_id}:{OrderStatus.MODERNIZATION.value}",
                )
            ]
        )

    if status in (OrderStatus.IN_WORK, OrderStatus.MODERNIZATION, OrderStatus.ACCEPTED):
        rows.append([InlineKeyboardButton(text="✅ Закрыть", callback_data=f"order_close:{order_id}")])

    # Важно: дать мастеру кнопку отправки скрина из карточки, если он вернулся позже
    if status == OrderStatus.NEEDS_PAYOUT:
        rows.append([InlineKeyboardButton(text="💸 Отправить скрин перевода", callback_data=f"order_payout:{order_id}")])

    # IMPORTANT: мастерский просмотр карточки -> order_viewm (чтобы не утекали контакты через общий order_view)
    rows.append([InlineKeyboardButton(text="🔄 Обновить карточку", callback_data=f"order_viewm:{order_id}")])
    return _kb_inline(rows)


def _order_admin_view_kb(
    order_id: int,
    status: OrderStatus,
    expense_cnt: int = 0,
    contract_cnt: int = 0,
    act_cnt: int = 0,
    has_payout: bool = False,
    assigned_master_id: int | None = None,
    allow_paid_confirm: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    rows.append([InlineKeyboardButton(text="📄 Открыть", callback_data=f"order_view:{order_id}")])

    if status == OrderStatus.NEW:
        rows.append([InlineKeyboardButton(text="👷 Назначить мастера", callback_data=f"order_assign:{order_id}")])
    if status == OrderStatus.ACCEPTED and assigned_master_id:
        rows.append([InlineKeyboardButton(text="↩️ Снять мастера", callback_data=f"order_unassign:{order_id}")])

    frows: list[InlineKeyboardButton] = []
    if expense_cnt:
        frows.append(InlineKeyboardButton(text=f"🧾 Чеки ({expense_cnt})", callback_data=f"order_files:expense:{order_id}"))
    if contract_cnt:
        frows.append(InlineKeyboardButton(text=f"📄 Договор ({contract_cnt})", callback_data=f"order_files:contract:{order_id}"))
    if act_cnt:
        frows.append(InlineKeyboardButton(text=f"📝 Акт ({act_cnt})", callback_data=f"order_files:act:{order_id}"))
    if frows:
        rows.append(frows)

    if has_payout:
        rows.append([InlineKeyboardButton(text="💸 Скрин сдачи", callback_data=f"order_payout_file:{order_id}")])

    # ✅ ВАЖНО: подтверждение оплаты только при наличии скрина
    if allow_paid_confirm and status == OrderStatus.NEEDS_PAYOUT and has_payout:
        rows.append([InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"order_admin_paid:{order_id}")])

    return _kb_inline(rows)


def _close_receipts_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline([[InlineKeyboardButton(text="✅ Готово", callback_data=f"close_receipts_done:{order_id}")]])


def _close_contract_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline(
        [
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"close_contract_skip:{order_id}")],
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"close_contract_done:{order_id}")],
        ]
    )


def _close_warranty_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline(
        [
            [
                InlineKeyboardButton(text="0", callback_data=f"close_warranty:{order_id}:0"),
                InlineKeyboardButton(text="14", callback_data=f"close_warranty:{order_id}:14"),
                InlineKeyboardButton(text="30", callback_data=f"close_warranty:{order_id}:30"),
                InlineKeyboardButton(text="90", callback_data=f"close_warranty:{order_id}:90"),
            ],
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"close_warranty_skip:{order_id}")],
        ]
    )


def _close_act_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline(
        [
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"close_act_skip:{order_id}")],
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"close_act_done:{order_id}")],
        ]
    )


def _is_cancel_text(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"отмена", "cancel", "стоп"}


def _split_problem(problem_full: str) -> Tuple[str, Optional[str]]:
    s = (problem_full or "").strip()
    marker = "Когда удобно:"
    if marker not in s:
        return s, None
    left, right = s.split(marker, 1)
    p = left.strip()
    w = right.strip()
    return p, (w if w else None)


def _public_order_text(order: Order) -> str:
    """
    Публичная карточка для мастера ДО принятия:
    - скрываем телефон/адрес (и не показываем детали, которые не нужны до принятия)
    """
    tech = order.offer.title if getattr(order, "offer", None) else "—"
    p, w = _split_problem(getattr(order, "problem", "") or "")

    txt = (
        f"<b>Заявка #{order.id}</b>\n"
        f"Статус: <b>{order_status_label(order.status)}</b>\n"
        f"Тип: <b>{order_type_label(order.order_type)}</b>\n\n"
        f"Город: <b>{html_escape(getattr(order, 'city', None) or '—')}</b>\n"
        f"Источник: <b>{html_escape(getattr(order, 'source', None) or '—')}</b>\n"
        f"Техника: <b>{html_escape(tech)}</b>\n"
        f"Проблема: {html_escape(p)}\n"
        f"Когда: {html_escape(w or '—')}\n\n"
        f"Клиент: <b>скрыто до принятия</b>\n"
        f"Телефон: <b>скрыто до принятия</b>\n"
        f"Адрес: <b>скрыто до принятия</b>"
    )

    percent = getattr(order, "percent_master_snapshot", None)
    if percent:
        txt += f"\nДоля мастера: <b>{percent}%</b> (от чистых)"

    return txt


def _private_order_text(order: Order) -> str:
    """
    Полная карточка для мастера ПОСЛЕ принятия (или когда заявка уже закреплена за мастером).
    """
    tech = order.offer.title if getattr(order, "offer", None) else "—"
    p, w = _split_problem(getattr(order, "problem", "") or "")

    txt = (
        f"<b>Заявка #{order.id}</b>\n"
        f"Статус: <b>{order_status_label(order.status)}</b>\n"
        f"Тип: <b>{order_type_label(order.order_type)}</b>\n\n"
        f"Клиент: <b>{html_escape(getattr(order, 'client_name', None) or '—')}</b>\n"
        f"Телефон: <b>{html_escape(getattr(order, 'client_phone', None) or '—')}</b>\n"
        f"Город: <b>{html_escape(getattr(order, 'city', None) or '—')}</b>\n"
        f"Адрес: <b>{html_escape((getattr(order, 'address', None) or '—') + (', кв. ' + str(getattr(order, 'apartment', None)) if getattr(order, 'apartment', None) else ''))}</b>\n"
        f"Источник: <b>{html_escape(getattr(order, 'source', None) or '—')}</b>\n"
        f"Техника: <b>{html_escape(tech)}</b>\n"
        f"Проблема: {html_escape(p)}\n"
        f"Когда: {html_escape(w or '—')}"
    )

    percent = getattr(order, "percent_master_snapshot", None)
    if percent:
        txt += f"\nДоля мастера: <b>{percent}%</b> (от чистых)"

    wd = getattr(order, "warranty_days", None)
    if wd is not None:
        txt += f"\nГарантия: <b>{wd} дн.</b>"

    cc = (getattr(order, "close_comment", None) or "").strip()
    if cc:
        txt += f"\nКомментарий мастера: {html_escape(cc)}"

    dr_comment = getattr(order, "modernization_comment", None)
    if dr_comment:
        txt += f"\n\n🧰 <b>Комментарий ДР:</b> {html_escape(dr_comment)}"

    return txt


# ------------------------
# MASTER: кнопки списка (Доступные / Мои / Касса)
# ------------------------
@router.message(F.text == BTN_AVAILABLE_ORDERS)
async def master_available_orders(message: Message):
    u = await _get_user_or_ask_start(message)
    if not u:
        return
    if u.role != Role.MASTER:
        await message.answer("Эта кнопка доступна только мастерам.")
        return

    orders = await repo.list_available_orders_for_master(u)
    if not orders:
        await message.answer("Нет доступных заявок.")
        return

    kb_rows: list[list[InlineKeyboardButton]] = []
    lines = []
    for o in orders[:15]:
        tech = o.offer.title if o.offer else "—"
        p, _w = _split_problem(o.problem)
        lines.append(f"#{o.id} | {o.city or '—'} | {tech} | {p[:40]}")
        kb_rows.append([InlineKeyboardButton(text=f"✅ Принять #{o.id}", callback_data=f"order_accept:{o.id}")])

    await message.answer("\n".join(lines), reply_markup=_kb_inline(kb_rows))


@router.message(F.text == BTN_MY_ORDERS)
async def master_my_orders(message: Message):
    u = await _get_user_or_ask_start(message)
    if not u:
        return
    if u.role != Role.MASTER:
        await message.answer("Эта кнопка доступна только мастерам.")
        return

    active_statuses = (OrderStatus.ACCEPTED, OrderStatus.IN_WORK, OrderStatus.MODERNIZATION)
    orders = await repo.list_orders_for_master(u, statuses=active_statuses, limit=50)
    if not orders:
        await message.answer("Активных заявок нет ✅\n\nЗаявки <b>на сдачу</b> смотрите в разделе «Касса».", parse_mode=ParseMode.HTML)
        return

    kb_rows: list[list[InlineKeyboardButton]] = []
    lines = ["<b>🧰 Активные заявки</b>:"]
    for o in orders[:20]:
        tech = o.offer.title if o.offer else "—"
        lines.append(f"#{o.id} | {order_status_label(o.status)} | {o.city or '—'} | {tech}")
        # IMPORTANT: мастерский просмотр карточки -> order_viewm
        kb_rows.append([InlineKeyboardButton(text=f"Открыть #{o.id}", callback_data=f"order_viewm:{o.id}")])

    await message.answer("\n".join(lines), reply_markup=_kb_inline(kb_rows), parse_mode=ParseMode.HTML)


@router.message(F.text == BTN_CASH)
async def master_cash(message: Message):
    u = await _get_user_or_ask_start(message)
    if not u:
        return
    if u.role != Role.MASTER:
        await message.answer("Эта кнопка доступна только мастерам.")
        return

    rows = await repo.list_master_cash_orders(u, limit=30)
    if not rows:
        await message.answer("Задолженности нет ✅")
        return

    total = sum(int(o.company_amount or 0) for o in rows)
    lines = [f"<b>💰 К сдаче всего:</b> {total} ₽\n"]
    kb_rows: list[list[InlineKeyboardButton]] = []

    for o in rows:
        tech = o.offer.title if o.offer else "—"
        proof = "✅" if getattr(o, "payout_screenshot_file_id", None) else "—"
        lines.append(
            f"• #{o.id} | {tech} | к сдаче: <b>{int(o.company_amount or 0)} ₽</b> | скрин: {proof}"
        )
        kb_rows.append([InlineKeyboardButton(text=f"Открыть #{o.id}", callback_data=f"order_viewm:{o.id}")])

    await message.answer("\n".join(lines), reply_markup=_kb_inline(kb_rows), parse_mode=ParseMode.HTML)


# ------------------------
# MASTER: просмотр карточки (без утечки контактов до принятия)
# ------------------------
@router.callback_query(F.data.startswith("order_viewm:"))
async def order_view_master(cb: CallbackQuery):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or u.role != Role.MASTER:
        await cb.answer("Только для мастеров", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return

    order, _created_by, _assigned_master = await repo.get_order_with_users(order_id)
    if not order:
        await cb.answer("Заявка не найдена", show_alert=True)
        return

    # Если заявка закреплена за другим мастером — не показываем
    if order.assigned_master_id is not None and order.assigned_master_id != u.id:
        await cb.answer("Это не ваша заявка", show_alert=True)
        return

    # До принятия (NEW и нет мастера) — показываем публичную карточку (без контактов)
    if order.status == OrderStatus.NEW and order.assigned_master_id is None:
        text = _public_order_text(order)
        kb = _kb_inline(
            [
                [InlineKeyboardButton(text=f"✅ Принять #{order.id}", callback_data=f"order_accept:{order.id}")],
            ]
        )
        await cb.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await cb.answer()
        return

    # После принятия / когда заявка уже закреплена за мастером — полная карточка
    text = _private_order_text(order)
    await cb.message.answer(
        text,
        reply_markup=_order_master_actions_kb(order.id, order.status),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


# ------------------------
# MASTER: принять / сменить статус / модернизация / закрыть / скрин
# ------------------------
@router.callback_query(F.data.startswith("order_accept:"))
async def order_accept(cb: CallbackQuery, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or u.role != Role.MASTER:
        await cb.answer("Только для мастеров", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return

    order = await repo.accept_order(order_id, u)
    if not order:
        cur, _cb, cur_master = await repo.get_order_with_users(order_id)
        if not cur:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        if cur.assigned_master_id is not None and cur_master:
            await cb.answer(f"Заявка уже принята: {cur_master.fio}", show_alert=True)
            return
        await cb.answer(f"Нельзя принять. Текущий статус: {order_status_label(cur.status)}", show_alert=True)
        return

    # -------------------------
    # Авто-удаление уведомлений о заявке у остальных мастеров
    # -------------------------
    try:
        delivered = await repo.list_order_dispatch_messages(order.id)
        for dm in delivered:
            # Оставляем сообщение у принявшего мастера (у него мы уберём кнопки ниже)
            if getattr(dm, "user_id", None) == getattr(u, "id", None):
                continue
            try:
                await bot.delete_message(chat_id=dm.chat_id, message_id=dm.message_id)
            except Exception:
                pass
        # После принятия эти записи больше не нужны
        await repo.delete_order_dispatch_messages(order_id=order.id)
    except Exception:
        # Не валим логику принятия, если удаление не удалось
        pass

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    full_order, _created_by, _assigned_master = await repo.get_order_with_users(order.id)
    if full_order:
        text = "✅ Вы приняли заявку. Контакты и адрес доступны:\n\n" + _private_order_text(full_order)
    else:
        text = f"✅ Вы приняли заявку <b>#{order.id}</b>."

    await cb.message.answer(
        text,
        reply_markup=_order_master_actions_kb(order.id, order.status),
        parse_mode=ParseMode.HTML,
    )

    await cb.answer("Принято")

    if getattr(order, "_accepted_now", False):
        await _notify_admins(bot, cfg, f"👷 Мастер <b>{html_escape(u.fio)}</b> принял заявку <b>#{order.id}</b>.")
        if getattr(cfg, "masters_chat_id", None):
            try:
                await bot.send_message(
                    cfg.masters_chat_id,
                    f"✅ Заявка <b>#{order.id}</b> принята мастером <b>{html_escape(u.fio)}</b>.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("order_status:"))
async def order_change_status(cb: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or u.role != Role.MASTER:
        await cb.answer("Только для мастеров", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return

    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer()
        return

    order_id = int(parts[1])
    new_status_value = parts[2]

    try:
        new_status = OrderStatus(new_status_value)
    except Exception:
        await cb.answer("Неверный статус", show_alert=True)
        return

    if new_status == OrderStatus.MODERNIZATION:
        await state.clear()
        await state.update_data(order_id=order_id)
        await cb.message.answer(
            "🧰 <b>Модернизация (ДР)</b>\n"
            "Напишите комментарий (почему забрали технику / что требуется сделать).\n"
            "Для отмены — напишите <b>отмена</b>.",
            parse_mode=ParseMode.HTML,
        )
        await state.set_state(ModernizationComment.comment)
        await cb.answer()
        return

    order = await repo.set_order_status(order_id, u, new_status)
    if not order:
        await cb.answer("Не удалось", show_alert=True)
        return

    await cb.message.answer(
        f"✅ Статус заявки <b>#{order.id}</b> обновлён: <b>{order_status_label(order.status)}</b>",
        reply_markup=_order_master_actions_kb(order.id, order.status),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Ок")
    await _notify_admins(bot, cfg, f"ℹ️ По заявке <b>#{order.id}</b> статус: <b>{order_status_label(order.status)}</b> (мастер: {html_escape(u.fio)})")


@router.message(ModernizationComment.comment, F.text, ~F.text.startswith("/"))
async def modernization_comment_save(message: Message, state: FSMContext, bot: Bot, cfg: Config):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u or u.role != Role.MASTER:
        await state.clear()
        await message.answer("Только для мастеров.")
        return
    if u.role == Role.FIRED:
        await state.clear()
        await message.answer("Ваш доступ к боту заблокирован.")
        return

    txt = (message.text or "").strip()
    if _is_cancel_text(txt):
        await state.clear()
        await message.answer("Ок, отменил перевод в ДР.")
        return

    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)

    order = await repo.set_order_modernization(order_id, u, txt)
    await state.clear()

    if not order:
        await message.answer("Не удалось перевести в ДР (проверьте статус заявки и комментарий).")
        return

    await message.answer(
        f"✅ Перевёл заявку <b>#{order.id}</b> в <b>Модернизацию (ДР)</b>\n"
        f"Комментарий: {html_escape(txt)}",
        reply_markup=_order_master_actions_kb(order.id, order.status),
        parse_mode=ParseMode.HTML,
    )

    await _notify_admins(
        bot,
        cfg,
        f"🧰 Заявка <b>#{order.id}</b> переведена в <b>Модернизацию (ДР)</b>\n"
        f"Мастер: <b>{html_escape(u.fio)}</b>\n"
        f"Комментарий: {html_escape(txt)}",
    )


# ------------------------
# MASTER: закрытие
# ------------------------
@router.callback_query(F.data.startswith("order_close:"))
async def order_close_start(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or u.role != Role.MASTER:
        await cb.answer("Только для мастеров", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return

    order_id = int(cb.data.split(":", 1)[1])
    await state.clear()
    await state.update_data(
        order_id=order_id,
        receipts=[],
        contract_file_id=None,
        act_photos=[],
        warranty_days=None,
        close_comment="",
    )
    await cb.message.answer("Введите <b>сумму по заявке</b> (числом, руб.):", parse_mode=ParseMode.HTML)
    await state.set_state(CloseOrder.sum_total)
    await cb.answer()


@router.message(CloseOrder.sum_total, F.text, ~F.text.startswith("/"))
async def order_close_sum_total(message: Message, state: FSMContext):
    if _is_cancel_text(message.text):
        await state.clear()
        await message.answer("Ок, отменил.")
        return
    try:
        v = int(message.text.strip().replace(" ", ""))
    except Exception:
        await message.answer("Нужно число. Например: 1500")
        return
    await state.update_data(sum_total=v)
    await message.answer("Введите <b>расходы</b> (числом, руб.). Если 0 — напишите 0:", parse_mode=ParseMode.HTML)
    await state.set_state(CloseOrder.sum_expenses)


@router.message(CloseOrder.sum_expenses, F.text, ~F.text.startswith("/"))
async def order_close_sum_expenses(message: Message, state: FSMContext):
    if _is_cancel_text(message.text):
        await state.clear()
        await message.answer("Ок, отменил.")
        return

    try:
        v = int(message.text.strip().replace(" ", ""))
    except Exception:
        await message.answer("Нужно число. Например: 0")
        return

    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)

    await state.update_data(sum_expenses=v)

    if v <= 0:
        await message.answer(
            "📄 Фото договора с клиентом (не обязательно).\n"
            "Отправьте фото или нажмите <b>Пропустить</b> / <b>Готово</b>.",
            reply_markup=_close_contract_kb(order_id),
            parse_mode=ParseMode.HTML,
        )
        await state.set_state(CloseOrder.contract_photo)
        return

    await message.answer(
        "🧾 <b>Расходы указаны.</b>\n"
        "Отправьте <b>минимум 1 фото чека</b> (можно несколько сообщений).\n"
        "Когда закончите — нажмите <b>✅ Готово</b>.",
        reply_markup=_close_receipts_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.receipts)


@router.message(CloseOrder.receipts, F.photo)
async def close_receipts_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    receipts = list(data.get("receipts") or [])

    file_id = message.photo[-1].file_id
    receipts.append(file_id)

    await state.update_data(receipts=receipts)

    order_id = int(data.get("order_id") or 0)
    await message.answer(
        f"✅ Чек принят ({len(receipts)}).\n"
        "Ещё фото? Или нажмите <b>✅ Готово</b>.",
        reply_markup=_close_receipts_kb(order_id),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("close_receipts_done:"))
async def close_receipts_done(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    data = await state.get_data()
    receipts = list(data.get("receipts") or [])
    order_id = int(data.get("order_id") or 0)

    if not receipts:
        await cb.answer("Нужен минимум 1 чек", show_alert=True)
        return

    await cb.message.answer(
        "📄 Фото договора с клиентом (не обязательно).\n"
        "Отправьте фото или нажмите <b>Пропустить</b> / <b>Готово</b>.",
        reply_markup=_close_contract_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.contract_photo)
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("close_contract_skip:"))
async def close_contract_skip(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)
    await cb.message.answer(
        "⏱ Укажите <b>гарантию</b> (в днях). Если гарантии нет — 0 или ⏭ Пропустить.",
        reply_markup=_close_warranty_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.warranty_days)
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("close_contract_done:"))
async def close_contract_done(cb: CallbackQuery, state: FSMContext):
    await close_contract_skip(cb, state)


@router.message(CloseOrder.contract_photo, F.photo)
async def close_contract_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(contract_file_id=file_id)
    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)
    await message.answer(
        "✅ Договор принят.\n\n"
        "⏱ Укажите <b>гарантию</b> (в днях). Если гарантии нет — 0 или ⏭ Пропустить.",
        reply_markup=_close_warranty_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.warranty_days)


@router.callback_query(F.data.startswith("close_warranty_skip:"))
async def close_warranty_skip(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    await state.update_data(warranty_days=None)
    await cb.message.answer(
        "💬 Комментарий по заявке (не обязательно).\n"
        "Напишите текст или отправьте <b>пропустить</b>.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.close_comment)
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("close_warranty:"))
async def close_warranty_quick(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    try:
        _, order_id_str, days_str = (cb.data or "").split(":")
        order_id = int(order_id_str)
        days = int(days_str)
    except Exception:
        await cb.answer("Ошибка данных кнопки", show_alert=False)
        return

    warranty_days = None if days <= 0 else days

    data = await state.get_data()
    current_order_id = int(data.get("order_id") or 0)
    if current_order_id and current_order_id != order_id:
        await cb.answer("Эта кнопка уже не актуальна", show_alert=False)
        return

    await state.update_data(warranty_days=warranty_days)

    await cb.message.answer(
        "💬 Комментарий по заявке (не обязательно).\n"
        "Напишите текст или отправьте <b>пропустить</b>.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.close_comment)
    await cb.answer("Ок")


@router.message(CloseOrder.warranty_days, F.text, ~F.text.startswith("/"))
async def close_warranty_days(message: Message, state: FSMContext):
    txt = (message.text or "").strip().lower()
    if _is_cancel_text(txt):
        await state.clear()
        await message.answer("Ок, отменил закрытие.")
        return
    if txt in {"пропустить", "skip", "нет"}:
        await state.update_data(warranty_days=None)
    else:
        try:
            v = int(txt.replace(" ", ""))
        except Exception:
            data = await state.get_data()
            order_id = int(data.get("order_id") or 0)
            await message.answer(
                "Нужно число (в днях). Если нет гарантии — 0 или Пропустить.",
                reply_markup=_close_warranty_kb(order_id),
            )
            return

        if v <= 0:
            await state.update_data(warranty_days=None)
        elif v > 3650:
            await message.answer("Слишком много дней. Укажите значение до 3650.")
            return
        else:
            await state.update_data(warranty_days=v)

    await message.answer(
        "💬 Комментарий по заявке (не обязательно).\n"
        "Напишите текст или отправьте <b>пропустить</b>.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.close_comment)


@router.message(CloseOrder.close_comment, F.text, ~F.text.startswith("/"))
async def close_comment(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if _is_cancel_text(txt):
        await state.clear()
        await message.answer("Ок, отменил закрытие.")
        return

    if txt.strip().lower() in {"пропустить", "skip", "нет"}:
        txt = ""
    await state.update_data(close_comment=txt)

    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)
    await message.answer(
        "📷 Фото <b>акта работ</b> (не обязательно).\n"
        "Можно отправить <b>несколько</b> фото сообщениями.\n"
        "Когда закончите — нажмите <b>✅ Готово</b> или <b>Пропустить</b>.",
        reply_markup=_close_act_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CloseOrder.act_photo)


@router.message(CloseOrder.act_photo, F.photo)
async def close_act_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    act_photos = list(data.get("act_photos") or [])
    act_photos.append(message.photo[-1].file_id)
    await state.update_data(act_photos=act_photos)

    order_id = int(data.get("order_id") or 0)
    await message.answer(
        f"✅ Фото акта принято ({len(act_photos)}).\n"
        "Ещё фото? Или нажмите <b>✅ Готово</b>.",
        reply_markup=_close_act_kb(order_id),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("close_act_skip:"))
async def close_act_skip(cb: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config):
    if not cb.message:
        return
    await state.update_data(act_photos=[])
    await _finalize_close(cb.message, state, bot, cfg, tg_id=cb.from_user.id)
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("close_act_done:"))
async def close_act_done(cb: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config):
    if not cb.message:
        return
    await _finalize_close(cb.message, state, bot, cfg, tg_id=cb.from_user.id)
    await cb.answer("Ок")


async def _finalize_close(message: Message, state: FSMContext, bot: Bot, cfg: Config, tg_id: Optional[int] = None):
    uid = tg_id or (message.from_user.id if message.from_user else None)
    if not uid:
        await state.clear()
        await message.answer("Ошибка: не удалось определить пользователя.")
        return

    u = await repo.get_user_by_tg_id(uid)
    if not u or u.role != Role.MASTER:
        await state.clear()
        await message.answer("Только для мастеров.")
        return
    if u.role == Role.FIRED:
        await state.clear()
        await message.answer("Ваш доступ к боту заблокирован.")
        return

    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)
    sum_total = int(data.get("sum_total") or 0)
    sum_expenses = int(data.get("sum_expenses") or 0)

    receipts = list(data.get("receipts") or [])
    contract_file_id = data.get("contract_file_id")
    act_photos = list(data.get("act_photos") or [])
    warranty_days = data.get("warranty_days")
    close_comment = (data.get("close_comment") or "").strip()

    if sum_expenses > 0 and not receipts:
        await message.answer("Нельзя закрыть: расходы > 0, но чеки не прикреплены. Отправьте минимум 1 фото чека.")
        await state.set_state(CloseOrder.receipts)
        return

    try:
        if receipts:
            await repo.add_order_files(order_id, "expense", receipts)
        if contract_file_id:
            await repo.add_order_files(order_id, "contract", [contract_file_id])
        if act_photos:
            await repo.add_order_files(order_id, "act", act_photos)
    except Exception:
        if sum_expenses > 0:
            await message.answer("Не удалось сохранить чеки. Попробуйте отправить фото ещё раз.")
            await state.set_state(CloseOrder.receipts)
            return

    order = await repo.close_order(
        order_id,
        u,
        sum_total=sum_total,
        sum_expenses=sum_expenses,
        warranty_days=(int(warranty_days) if warranty_days is not None else None),
        close_comment=close_comment,
    )
    if not order:
        await state.clear()
        await message.answer("Не удалось закрыть (возможно заявка не ваша или уже закрыта).")
        return

    expense_cnt = int(await repo.count_order_files(order.id, "expense") or 0)
    contract_cnt = int(await repo.count_order_files(order.id, "contract") or 0)
    act_cnt = int(await repo.count_order_files(order.id, "act") or 0)

    await state.clear()

    await message.answer(
        "✅ Заявка закрыта.\n"
        f"Итого: <b>{order.total_amount}</b>, расходы: <b>{order.expense_amount or 0}</b>\n"
        f"Чистыми: <b>{order.net_amount or 0}</b>\n"
        f"К сдаче в компанию: <b>{order.company_amount or 0}</b>\n",
        parse_mode=ParseMode.HTML,
    )

    req = ""
    # Реквизиты лучше хранить в БД (меняются из меню супер-админа),
    # а в config.yml использовать только как fallback.
    default_req = ""
    if getattr(cfg, "business", None):
        default_req = (getattr(cfg.business, "payout_requisites_text", "") or "").strip()

    try:
        req_text = await repo.get_payout_requisites_text(default_text=default_req)
    except Exception:
        req_text = default_req

    req_text = (req_text or "").strip()
    if req_text:
        req = "

<b>Реквизиты для сдачи:</b>
" + html_escape(req_text)

    await message.answer(
        "Теперь отправьте скрин перевода:" + req,
        reply_markup=_order_send_payout_kb(order.id),
        parse_mode=ParseMode.HTML,
    )

    admin_kb = _order_admin_view_kb(
        order.id,
        order.status,
        expense_cnt,
        contract_cnt,
        act_cnt,
        has_payout=False,
        assigned_master_id=order.assigned_master_id,
        allow_paid_confirm=True,
    )

    await _notify_admins(
        bot,
        cfg,
        (
            f"✅ Заявка <b>#{order.id}</b> закрыта мастером <b>{html_escape(u.fio)}</b>.\n"
            f"Итого: <b>{order.total_amount}</b>, расходы: <b>{order.expense_amount or 0}</b>, "
            f"к сдаче: <b>{order.company_amount or 0}</b>.\n"
            f"🧾 Чеки: <b>{expense_cnt}</b> | 📄 Договор: <b>{contract_cnt}</b> | 📷 Акт: <b>{act_cnt}</b>"
        ),
        reply_markup=admin_kb,
    )


# ------------------------
# MASTER: скрин перевода
# ------------------------
@router.callback_query(F.data.startswith("order_payout:"))
async def order_payout_start(cb: CallbackQuery, state: FSMContext, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or u.role != Role.MASTER:
        await cb.answer("Только для мастеров", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return
    if not getattr(u, "is_approved", False):
        await cb.answer("Мастер не подтвержден админом", show_alert=True)
        return

    order_id = int(cb.data.split(":", 1)[1])
    await state.clear()
    await state.update_data(order_id=order_id)

    order = await repo.get_order(order_id)
    amount = getattr(order, "company_amount", None)

    req = ""
    # Реквизиты лучше хранить в БД (меняются из меню супер-админа),
    # а в config.yml использовать только как fallback.
    default_req = ""
    if getattr(cfg, "business", None):
        default_req = (getattr(cfg.business, "payout_requisites_text", "") or "").strip()

    try:
        req_text = await repo.get_payout_requisites_text(default_text=default_req)
    except Exception:
        req_text = default_req

    req_text = (req_text or "").strip()
    if req_text:
        req = "

<b>Реквизиты для сдачи:</b>
" + html_escape(req_text)

    hint = ""
    if amount is not None:
        hint = f"\nК сдаче: <b>{amount}</b>"

    await cb.message.answer(
        "Отправьте <b>фото</b> со скрином перевода (одним сообщением)." + hint + req,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PayoutProof.photo)
    await cb.answer()


@router.message(PayoutProof.photo, F.photo)
async def order_payout_save(message: Message, state: FSMContext, bot: Bot, cfg: Config):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u or u.role != Role.MASTER:
        await state.clear()
        await message.answer("Только для мастеров.")
        return
    if u.role == Role.FIRED:
        await state.clear()
        await message.answer("Ваш доступ к боту заблокирован.")
        return

    data = await state.get_data()
    order_id = int(data.get("order_id"))
    file_id = message.photo[-1].file_id

    order = await repo.attach_order_payout_proof(order_id, u, file_id=file_id)
    await state.clear()
    if not order:
        await message.answer("Не удалось сохранить (возможно заявка не ваша или статус не 'Требуется сдача').")
        return

    await message.answer(
        f"✅ Скрин принят по заявке <b>#{order.id}</b>. Ожидаем подтверждение администратора.",
        parse_mode=ParseMode.HTML,
    )

    expense_cnt = int(await repo.count_order_files(order.id, "expense") or 0)
    contract_cnt = int(await repo.count_order_files(order.id, "contract") or 0)
    act_cnt = int(await repo.count_order_files(order.id, "act") or 0)

    await _notify_admins(
        bot,
        cfg,
        f"💸 Мастер <b>{html_escape(u.fio)}</b> отправил скрин по заявке <b>#{order.id}</b> (к сдаче: <b>{order.company_amount or 0}</b>).",
        reply_markup=_order_admin_view_kb(
            order.id,
            order.status,
            expense_cnt,
            contract_cnt,
            act_cnt,
            has_payout=True,
            assigned_master_id=order.assigned_master_id,
            allow_paid_confirm=True,
        ),
    )
