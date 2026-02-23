from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from bot.db import get_sessionmaker
from bot.db.models import Offer, Order, OrderFile, OrderStatus, Role, User


# ---------------------------
# Helpers
# ---------------------------

def _order_event_dt_expr():
    """SQL expression: choose the most relevant timestamp for the current status."""
    return case(
        (Order.status == OrderStatus.PAID, func.coalesce(Order.paid_at, Order.updated_at)),
        (Order.status == OrderStatus.NEEDS_PAYOUT, func.coalesce(Order.closed_at, Order.updated_at)),
        (Order.status == OrderStatus.ACCEPTED, func.coalesce(Order.accepted_at, Order.updated_at)),
        else_=Order.updated_at,
    )


def _order_event_dt_obj(o: Order) -> datetime | None:
    """Python-side: choose the most relevant timestamp for the current status."""
    st = getattr(o, "status", None)
    if st == OrderStatus.PAID:
        return getattr(o, "paid_at", None) or getattr(o, "updated_at", None)
    if st == OrderStatus.NEEDS_PAYOUT:
        return getattr(o, "closed_at", None) or getattr(o, "updated_at", None)
    if st == OrderStatus.ACCEPTED:
        return getattr(o, "accepted_at", None) or getattr(o, "updated_at", None)
    return getattr(o, "updated_at", None)


# ---------------------------
# Reports (advanced)
# ---------------------------
async def report_list_orders(
    statuses: list[OrderStatus],
    dt_from=None,
    dt_to=None,
    limit: int = 200,
):
    sm = get_sessionmaker()
    async with sm() as s:
        event_dt = _order_event_dt_expr()
        q = (
            select(Order)
            .where(Order.status.in_(statuses))
            .options(
                selectinload(Order.offer),
                selectinload(Order.assigned_master),
            )
            .order_by(event_dt.desc(), Order.id.desc())
            .limit(limit)
        )
        if dt_from is not None:
            q = q.where(event_dt >= dt_from)
        if dt_to is not None:
            q = q.where(event_dt <= dt_to)

        res = await s.scalars(q)
        return list(res)


async def report_files_counts(order_ids: list[int]) -> dict[tuple[int, str], int]:
    if not order_ids:
        return {}
    sm = get_sessionmaker()
    async with sm() as s:
        q = (
            select(OrderFile.order_id, OrderFile.kind, func.count(OrderFile.id))
            .where(OrderFile.order_id.in_(order_ids))
            .group_by(OrderFile.order_id, OrderFile.kind)
        )
        rows = (await s.execute(q)).all()
        out: dict[tuple[int, str], int] = {}
        for order_id, kind, cnt in rows:
            out[(int(order_id), str(kind))] = int(cnt or 0)
        return out


async def report_masters_payout_summary(dt_from=None, dt_to=None) -> list[dict]:
    """
    Сводка по мастерам за период по выплатным заявкам.

    - NEEDS_PAYOUT учитываем по closed_at (когда закрыли/перевели в ожидание сдачи)
    - PAID учитываем по paid_at

    pending = NEEDS_PAYOUT
    paid = PAID

    master_income_sum = сумма (net_amount - company_amount) по pending+paid
    """
    sm = get_sessionmaker()
    async with sm() as s:
        event_dt = _order_event_dt_expr()
        base = (
            select(
                User.id.label("master_id"),
                User.fio.label("fio"),
                func.sum(case((Order.status == OrderStatus.NEEDS_PAYOUT, 1), else_=0)).label("pending_cnt"),
                func.sum(
                    case(
                        (Order.status == OrderStatus.NEEDS_PAYOUT, func.coalesce(Order.company_amount, 0)),
                        else_=0,
                    )
                ).label("pending_sum"),
                func.sum(case((Order.status == OrderStatus.PAID, 1), else_=0)).label("paid_cnt"),
                func.sum(
                    case(
                        (Order.status == OrderStatus.PAID, func.coalesce(Order.company_amount, 0)),
                        else_=0,
                    )
                ).label("paid_sum"),
                func.sum(
                    case(
                        (
                            Order.status.in_([OrderStatus.NEEDS_PAYOUT, OrderStatus.PAID]),
                            func.coalesce(Order.net_amount, 0) - func.coalesce(Order.company_amount, 0),
                        ),
                        else_=0,
                    )
                ).label("master_income_sum"),
            )
            .select_from(User)
            .join(Order, Order.assigned_master_id == User.id)
            .where(
                User.role == Role.MASTER,
                Order.status.in_([OrderStatus.NEEDS_PAYOUT, OrderStatus.PAID]),
            )
            .group_by(User.id, User.fio)
            .order_by(
                func.sum(
                    case(
                        (Order.status == OrderStatus.NEEDS_PAYOUT, func.coalesce(Order.company_amount, 0)),
                        else_=0,
                    )
                ).desc()
            )
        )

        if dt_from is not None:
            base = base.where(event_dt >= dt_from)
        if dt_to is not None:
            base = base.where(event_dt <= dt_to)

        rows = (await s.execute(base)).mappings().all()
        return [dict(r) for r in rows]


async def get_masters_payout_summary(days: int = 30) -> List[Dict[str, Any]]:
    """
    Сводка по мастерам за период:
    needs_cnt/needs_sum (NEEDS_PAYOUT), paid_cnt/paid_sum (PAID), total_sum.

    Период считаем по статусным таймстемпам (closed_at/paid_at), а не по updated_at.
    """
    cutoff = datetime.utcnow() - timedelta(days=int(days or 30))
    event_dt = _order_event_dt_expr()

    needs_cnt = func.sum(case((Order.status == OrderStatus.NEEDS_PAYOUT, 1), else_=0)).label("needs_cnt")
    paid_cnt = func.sum(case((Order.status == OrderStatus.PAID, 1), else_=0)).label("paid_cnt")

    needs_sum = func.coalesce(
        func.sum(
            case(
                (Order.status == OrderStatus.NEEDS_PAYOUT, func.coalesce(Order.company_amount, 0)),
                else_=0,
            )
        ),
        0,
    ).label("needs_sum")

    paid_sum = func.coalesce(
        func.sum(
            case(
                (Order.status == OrderStatus.PAID, func.coalesce(Order.company_amount, 0)),
                else_=0,
            )
        ),
        0,
    ).label("paid_sum")

    total_sum = func.coalesce(func.sum(func.coalesce(Order.company_amount, 0)), 0).label("total_sum")

    sm = get_sessionmaker()
    async with sm() as s:
        q = (
            select(
                User.id.label("master_id"),
                User.fio.label("fio"),
                needs_cnt,
                needs_sum,
                paid_cnt,
                paid_sum,
                total_sum,
            )
            .join(Order, Order.assigned_master_id == User.id)
            .where(
                Order.assigned_master_id.is_not(None),
                Order.status.in_([OrderStatus.NEEDS_PAYOUT, OrderStatus.PAID]),
                event_dt >= cutoff,
            )
            .group_by(User.id, User.fio)
            .order_by(needs_sum.desc(), paid_sum.desc(), User.fio.asc())
        )

        res = await s.execute(q)
        out: List[Dict[str, Any]] = []
        for r in res:
            m = r._mapping
            out.append(
                {
                    "master_id": int(m["master_id"]),
                    "fio": m["fio"],
                    "needs_cnt": int(m["needs_cnt"] or 0),
                    "needs_sum": int(m["needs_sum"] or 0),
                    "paid_cnt": int(m["paid_cnt"] or 0),
                    "paid_sum": int(m["paid_sum"] or 0),
                    "total_sum": int(m["total_sum"] or 0),
                }
            )
        return out


async def list_master_payout_orders(master_id: int, days: int = 30, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Детализация выплатных заявок мастера (NEEDS_PAYOUT + PAID) за период.
    Возвращает уже “подготовленные” поля для вывода.

    Период считаем по статусным таймстемпам (closed_at/paid_at).
    """
    cutoff = datetime.utcnow() - timedelta(days=int(days or 30))
    limit = int(limit or 30)

    exp_sq = (
        select(OrderFile.order_id.label("oid"), func.count(OrderFile.id).label("expense_cnt"))
        .where(OrderFile.kind == "expense")
        .group_by(OrderFile.order_id)
        .subquery()
    )
    con_sq = (
        select(OrderFile.order_id.label("oid"), func.count(OrderFile.id).label("contract_cnt"))
        .where(OrderFile.kind == "contract")
        .group_by(OrderFile.order_id)
        .subquery()
    )

    sm = get_sessionmaker()
    async with sm() as s:
        event_dt = _order_event_dt_expr()
        q = (
            select(
                Order,
                Offer.title.label("offer_title"),
                func.coalesce(exp_sq.c.expense_cnt, 0).label("expense_cnt"),
                func.coalesce(con_sq.c.contract_cnt, 0).label("contract_cnt"),
            )
            .join(Offer, Offer.id == Order.offer_id, isouter=True)
            .outerjoin(exp_sq, exp_sq.c.oid == Order.id)
            .outerjoin(con_sq, con_sq.c.oid == Order.id)
            .where(
                Order.assigned_master_id == int(master_id),
                Order.status.in_([OrderStatus.NEEDS_PAYOUT, OrderStatus.PAID]),
                event_dt >= cutoff,
            )
            .order_by(event_dt.desc(), Order.id.desc())
            .limit(limit)
        )

        res = await s.execute(q)
        out: List[Dict[str, Any]] = []
        for row in res:
            o: Order = row[0]
            offer_title = row[1]
            expense_cnt = int(row[2] or 0)
            contract_cnt = int(row[3] or 0)

            out.append(
                {
                    "id": int(o.id),
                    "status": o.status,
                    "city": o.city,
                    "offer_title": offer_title,
                    "company_amount": int(o.company_amount or 0),
                    "expense_cnt": expense_cnt,
                    "contract_cnt": contract_cnt,
                    "has_payout_proof": bool(getattr(o, "payout_screenshot_file_id", None)),
                    # сохраняем ключ "updated_at" для обратной совместимости UI,
                    # но фактически кладём "событийное" время (closed_at/paid_at).
                    "updated_at": _order_event_dt_obj(o),
                }
            )
        return out
