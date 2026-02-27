from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.db import repo
from bot.db.models import Order, OrderStatus, Role
from bot.constants import BTN_PAYOUTS, BTN_MASTERS_SUMMARY

router = Router()


def _is_admin_role(role: Role | None) -> bool:
    return role in (Role.SUPER_ADMIN, Role.DISPATCHER)


async def _get_user_or_ask_start(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return None
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован. Обратитесь к администратору.")
        return None
    return u


async def _safe_answer(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: ParseMode | str | None = ParseMode.HTML,
    attempts: int = 3,
) -> None:
    """
    Защита от временных сетевых сбоев Telegram.
    Если DNS/сеть отвалились - мы хотя бы не роняем обработчик.
    """
    for i in range(attempts):
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramRetryAfter as e:
            # Telegram попросил подождать (flood control)
            await asyncio.sleep(float(getattr(e, "retry_after", 1)) + 0.3)
        except TelegramNetworkError:
            # типично: временный DNS/интернет
            if i == attempts - 1:
                return
            await asyncio.sleep(1.0 + i * 2)


def _kb_open_orders(order_ids: list[int]) -> InlineKeyboardMarkup | None:
    """
    Компактная клавиатура: по 2 кнопки в ряд.
    Кнопка ведёт на существующий callback order_view:<id>
    """
    if not order_ids:
        return None

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


def _fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    try:
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return "—"


def _order_event_dt(o: Order) -> Optional[datetime]:
    """Для выплатных отчётов используем статусные таймстемпы."""
    st = getattr(o, "status", None)
    if st == OrderStatus.PAID:
        return getattr(o, "paid_at", None) or getattr(o, "updated_at", None)
    if st == OrderStatus.NEEDS_PAYOUT:
        return getattr(o, "closed_at", None) or getattr(o, "updated_at", None)
    if st == OrderStatus.ACCEPTED:
        return getattr(o, "accepted_at", None) or getattr(o, "updated_at", None)
    return getattr(o, "updated_at", None)


async def _counts_for_order(order_id: int) -> tuple[int, int, int]:
    """
    (cheques, contract, act)
    """
    c1 = int(await repo.count_order_files(order_id, "expense") or 0)
    c2 = int(await repo.count_order_files(order_id, "contract") or 0)
    c3 = int(await repo.count_order_files(order_id, "act") or 0)
    return c1, c2, c3


async def _render_payout_line(o: Order, master_cache: dict[int, str]) -> str:
    tech = o.offer.title if getattr(o, "offer", None) else "—"
    city = getattr(o, "city", None) or "—"

    master_fio = "—"
    mid = getattr(o, "assigned_master_id", None)
    if mid:
        if mid in master_cache:
            master_fio = master_cache[mid]
        else:
            m = await repo.get_user_by_id(mid)
            master_cache[mid] = (getattr(m, "fio", None) or f"ID:{mid}") if m else f"ID:{mid}"
            master_fio = master_cache[mid]

    to_company = int(getattr(o, "company_amount", 0) or 0)
    total = int(getattr(o, "total_amount", 0) or 0)
    exp = int(getattr(o, "expense_amount", 0) or 0)

    has_payout = "✅" if getattr(o, "payout_screenshot_file_id", None) else "—"

    che, con, act = await _counts_for_order(o.id)
    upd = _fmt_dt(_order_event_dt(o))

    return (
        f"#{o.id} | {master_fio} | {city} | {tech} | "
        f"к сдаче: {to_company} | итого: {total} | "
        f"расх: {exp} | чеки: {che} | дог: {con} | акт: {act} | скрин: {has_payout} | upd: {upd}"
    )


async def _report_orders_by_status(status: OrderStatus, dt_from: datetime, dt_to: datetime, limit: int) -> list[Order]:
    """Предпочтительно берём через report_list_orders (фильтр по статусным датам)."""
    if hasattr(repo, "report_list_orders"):
        try:
            return await repo.report_list_orders([status], dt_from=dt_from, dt_to=dt_to, limit=limit)
        except Exception:
            pass

    # Fallback (на случай старого репозитория): фильтруем в Python.
    all_orders = await repo.list_orders(limit=limit)
    out: list[Order] = []
    for o in all_orders:
        if getattr(o, "status", None) != status:
            continue
        ev = _order_event_dt(o)
        if ev and dt_from <= ev <= dt_to:
            out.append(o)
    out.sort(key=lambda x: (_order_event_dt(x) or datetime.min), reverse=True)
    return out


@router.message(F.text == BTN_PAYOUTS)
async def payouts_report(message: Message, cfg: Config):
    u = await _get_user_or_ask_start(message)
    if not u:
        return
    if not _is_admin_role(u.role):
        await _safe_answer(message, "Раздел «Выплаты» доступен только админам/диспетчерам.")
        return

    dt_to = datetime.utcnow()
    dt_from = dt_to - timedelta(days=30)

    waiting = await _report_orders_by_status(OrderStatus.NEEDS_PAYOUT, dt_from, dt_to, limit=600)
    paid = await _report_orders_by_status(OrderStatus.PAID, dt_from, dt_to, limit=600)

    waiting_sum = sum(int(getattr(o, "company_amount", 0) or 0) for o in waiting)
    # ✅ как на твоих скринах: сумма по PAID — это "к сдаче", а не total_amount
    paid_sum = sum(int(getattr(o, "company_amount", 0) or 0) for o in paid)

    header = (
        "💰 <b>Выплаты</b>\n"
        "Период: <b>за 30 дн.</b>\n\n"
        f"🟠 <b>Ожидают сдачу</b> (NEEDS_PAYOUT): <b>{len(waiting)}</b> | сумма к сдаче: <b>{waiting_sum}</b>\n"
        f"🟢 <b>Оплачены</b> (PAID): <b>{len(paid)}</b> | сумма к сдаче: <b>{paid_sum}</b>\n"
    )
    await _safe_answer(message, header, parse_mode=ParseMode.HTML)

    master_cache: dict[int, str] = {}

    # --- WAITING ---
    if waiting:
        lines_wait = await asyncio.gather(*[_render_payout_line(o, master_cache) for o in waiting[:40]])
        text_wait = "🟠 <b>Ожидают сдачу</b>\n<code>" + "\n".join(lines_wait) + "</code>"
        kb_wait = _kb_open_orders([o.id for o in waiting[:40]])
        await _safe_answer(message, text_wait, parse_mode=ParseMode.HTML, reply_markup=kb_wait)
    else:
        await _safe_answer(message, "🟠 Ожидающих сдачу нет ✅")

    # --- PAID ---
    if paid:
        lines_paid = await asyncio.gather(*[_render_payout_line(o, master_cache) for o in paid[:40]])
        text_paid = "🟢 <b>Оплачены</b>\n<code>" + "\n".join(lines_paid) + "</code>"
        kb_paid = _kb_open_orders([o.id for o in paid[:40]])
        await _safe_answer(message, text_paid, parse_mode=ParseMode.HTML, reply_markup=kb_paid)
    else:
        await _safe_answer(message, "🟢 Оплаченных за период нет.")


@router.message(F.text == BTN_MASTERS_SUMMARY)
async def masters_summary(message: Message):
    u = await _get_user_or_ask_start(message)
    if not u:
        return
    if not _is_admin_role(u.role):
        await _safe_answer(message, "Раздел доступен только админам/диспетчерам.")
        return

    dt_to = datetime.utcnow()
    dt_from = dt_to - timedelta(days=30)

    orders_paid = await _report_orders_by_status(OrderStatus.PAID, dt_from, dt_to, limit=800)

    if not orders_paid:
        await _safe_answer(
            message,
            "👷 <b>Сводка по мастерам</b>\n\nЗа последние 30 дней PAID заявок нет.",
            parse_mode=ParseMode.HTML,
        )
        return

    # master_id -> (count, sum_total, sum_company)
    agg: dict[int, list[int]] = {}
    for o in orders_paid:
        mid = getattr(o, "assigned_master_id", None)
        if not mid:
            continue
        if mid not in agg:
            agg[mid] = [0, 0, 0]
        agg[mid][0] += 1
        agg[mid][1] += int(getattr(o, "total_amount", 0) or 0)
        agg[mid][2] += int(getattr(o, "company_amount", 0) or 0)

    rows = []
    for mid, (cnt, sum_total, sum_company) in agg.items():
        m = await repo.get_user_by_id(mid)
        fio = (getattr(m, "fio", None) or f"ID:{mid}") if m else f"ID:{mid}"
        rows.append((fio, cnt, sum_total, sum_company))

    rows.sort(key=lambda x: (x[1], x[2]), reverse=True)

    lines = [
        "👷 <b>Сводка по мастерам</b>",
        "Период: <b>за 30 дн.</b>",
        "",
        "<code>ФИО | заявок | сумма | к сдаче</code>",
        "<code>" + "\n".join([f"{fio} | {cnt} | {sum_total} | {sum_company}" for fio, cnt, sum_total, sum_company in rows]) + "</code>",
    ]
    await _safe_answer(message, "\n".join(lines), parse_mode=ParseMode.HTML)
