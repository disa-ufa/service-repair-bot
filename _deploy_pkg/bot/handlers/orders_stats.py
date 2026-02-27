from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Dict

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import Message

from sqlalchemy import select, func, case

from bot.db import repo, get_sessionmaker
from bot.db.models import Role, OrderStatus, Order
from bot.constants import BTN_STATS, order_status_label

router = Router()


def _is_admin_role(role: Optional[Role]) -> bool:
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


def _label_status(s: OrderStatus) -> str:
    """
    В проекте order_status_label может быть:
    - функцией: order_status_label(status) -> str
    - или dict: order_status_label.get(status, ...)
    Делаем совместимость.
    """
    try:
        # если это dict
        return order_status_label.get(s, getattr(s, "value", str(s)))  # type: ignore[attr-defined]
    except AttributeError:
        # если это функция
        try:
            return order_status_label(s)  # type: ignore[call-arg]
        except Exception:
            return getattr(s, "value", str(s))


async def _status_counts(dt_from: datetime, dt_to: datetime, master_id: int | None = None) -> Dict[OrderStatus, int]:
    """
    Считаем количество заявок по статусам за период.
    Период считаем по таймстемпам статусов (как в отчётах):
    NEW -> created_at
    ACCEPTED -> accepted_at
    CLOSED/NEEDS_PAYOUT -> closed_at
    PAID -> paid_at
    иначе -> updated_at
    """
    dt_field = case(
        (Order.status == OrderStatus.NEW, func.coalesce(Order.created_at, Order.updated_at)),
        (Order.status == OrderStatus.ACCEPTED, func.coalesce(Order.accepted_at, Order.updated_at, Order.created_at)),
        (Order.status == OrderStatus.CLOSED, func.coalesce(Order.closed_at, Order.updated_at, Order.created_at)),
        (Order.status == OrderStatus.NEEDS_PAYOUT, func.coalesce(Order.closed_at, Order.updated_at, Order.created_at)),
        (Order.status == OrderStatus.PAID, func.coalesce(Order.paid_at, Order.updated_at, Order.created_at)),
        else_=func.coalesce(Order.updated_at, Order.created_at),
    )

    stmt = (
        select(Order.status, func.count(Order.id))
        .where(dt_field >= dt_from, dt_field <= dt_to)
        .group_by(Order.status)
    )
    if master_id is not None:
        stmt = stmt.where(Order.assigned_master_id == master_id)

    sm = get_sessionmaker()
    async with sm() as session:
        rows = (await session.execute(stmt)).all()

    out: Dict[OrderStatus, int] = {}
    for st, cnt in rows:
        try:
            out[st] = int(cnt or 0)
        except Exception:
            out[st] = 0
    return out


def _render_section(title: str, counts: Dict[OrderStatus, int]) -> str:
    # порядок статусов (можете поменять как вам удобнее)
    order = [
        OrderStatus.NEW,
        OrderStatus.ACCEPTED,
        OrderStatus.IN_WORK,
        OrderStatus.MODERNIZATION,
        OrderStatus.CLOSED,
        OrderStatus.NEEDS_PAYOUT,
        OrderStatus.PAID,
    ]

    total = sum(counts.values())
    lines = [f"📊 <b>{title}</b> | всего: <b>{total}</b>"]
    for st in order:
        cnt = int(counts.get(st, 0))
        if cnt:
            lines.append(f"• {_label_status(st)}: <b>{cnt}</b>")
    if len(lines) == 1:
        lines.append("• нет данных")
    return "\n".join(lines) + "\n\n"


@router.message(F.text == BTN_STATS)
async def stats_button(message: Message):
    u = await _get_user_or_ask_start(message)
    if not u:
        return

    now = datetime.now()
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # --- “За сегодня” и “За 30 дней” ---
    if _is_admin_role(u.role):
        # админ/диспетчер: общая статистика по всем
        counts_today = await _status_counts(start_today, now, master_id=None)
        counts_month = await _status_counts(start_month, now, master_id=None)

        # финансовый блок (PAID) за период
        fin_today = await repo.get_admin_month_finance(start_today, now)
        fin_month = await repo.get_admin_month_finance(start_month, now)

        text = (
            "📈 <b>Статистика (админ)</b>\n\n"
            + _render_section("За сегодня", counts_today)
            + (
                "💵 <b>PAID за сегодня</b>: "
                f"заявок <b>{fin_today.get('count_paid', 0)}</b>, "
                f"выручка <b>{fin_today.get('gross', 0)} ₽</b>, "
                f"расходы <b>{fin_today.get('expenses', 0)} ₽</b>, "
                f"мастерам <b>{fin_today.get('masters', 0)} ₽</b>, "
                f"к сдаче <b>{fin_today.get('company', 0)} ₽</b>, "
                f"средний чек <b>{fin_today.get('avg_check', 0)} ₽</b>\n\n"
            )
            + _render_section("За месяц", counts_month)
            + (
                "💵 <b>PAID за месяц</b>: "
                f"заявок <b>{fin_month.get('count_paid', 0)}</b>, "
                f"выручка <b>{fin_month.get('gross', 0)} ₽</b>, "
                f"расходы <b>{fin_month.get('expenses', 0)} ₽</b>, "
                f"мастерам <b>{fin_month.get('masters', 0)} ₽</b>, "
                f"к сдаче <b>{fin_month.get('company', 0)} ₽</b>, "
                f"средний чек <b>{fin_month.get('avg_check', 0)} ₽</b>\n"
            )
        )

        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    # мастер: только по своим заявкам
    master_id = u.id
    counts_today = await _status_counts(start_today, now, master_id=master_id)
    counts_month = await _status_counts(start_month, now, master_id=master_id)

    fin_today = await repo.get_master_month_finance(master_id, start_today, now)
    fin_month = await repo.get_master_month_finance(master_id, start_month, now)

    text = (
        "📈 <b>Статистика (мастер)</b>\n\n"
        + _render_section("За сегодня", counts_today)
        + (
            "💵 <b>PAID за сегодня</b>: "
            f"заказов <b>{fin_today.get('count_paid', 0)}</b>, "
            f"выручка <b>{fin_today.get('gross', 0)} ₽</b>, "
            f"расходы <b>{fin_today.get('expenses', 0)} ₽</b>, "
            f"чистыми <b>{fin_today.get('net', 0)} ₽</b>, "
            f"заработал <b>{fin_today.get('master_take', 0)} ₽</b>, "
            f"доля компании <b>{fin_today.get('company', 0)} ₽</b>, "
            f"средний чек <b>{fin_today.get('avg_check', 0)} ₽</b>\n\n"
        )
        + _render_section("За месяц", counts_month)
        + (
            "💵 <b>PAID за месяц</b>: "
            f"заказов <b>{fin_month.get('count_paid', 0)}</b>, "
            f"выручка <b>{fin_month.get('gross', 0)} ₽</b>, "
            f"расходы <b>{fin_month.get('expenses', 0)} ₽</b>, "
            f"чистыми <b>{fin_month.get('net', 0)} ₽</b>, "
            f"заработал <b>{fin_month.get('master_take', 0)} ₽</b>, "
            f"доля компании <b>{fin_month.get('company', 0)} ₽</b>, "
            f"средний чек <b>{fin_month.get('avg_check', 0)} ₽</b>\n"
        )
    )

    await message.answer(text, parse_mode=ParseMode.HTML)
