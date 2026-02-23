from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from bot.db import get_sessionmaker
from bot.db.models import Offer, Order, OrderFile, OrderStatus, OrderType, Role, User


# ---------------------------
# Stats
# ---------------------------
async def get_admin_stats() -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        total = await s.scalar(select(func.count(Order.id)))
        new_ = await s.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.NEW))
        in_work = await s.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.IN_WORK))
        needs_payout = await s.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.NEEDS_PAYOUT))
        paid = await s.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.PAID))
        masters = await s.scalar(select(func.count(User.id)).where(User.role == Role.MASTER))

        return {
            "total": int(total or 0),
            "new": int(new_ or 0),
            "in_work": int(in_work or 0),
            "needs_payout": int(needs_payout or 0),
            "paid": int(paid or 0),
            "masters": int(masters or 0),
        }


async def get_master_stats(master: User) -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        total = await s.scalar(select(func.count(Order.id)).where(Order.assigned_master_id == master.id))
        in_work = await s.scalar(
            select(func.count(Order.id)).where(
                Order.assigned_master_id == master.id,
                Order.status.in_([OrderStatus.ACCEPTED, OrderStatus.IN_WORK, OrderStatus.MODERNIZATION]),
            )
        )
        needs_payout = await s.scalar(
            select(func.count(Order.id)).where(
                Order.assigned_master_id == master.id,
                Order.status == OrderStatus.NEEDS_PAYOUT,
            )
        )
        sum_to_company = await s.scalar(
            select(func.coalesce(func.sum(Order.company_amount), 0)).where(
                Order.assigned_master_id == master.id,
                Order.status.in_([OrderStatus.NEEDS_PAYOUT, OrderStatus.PAID]),
            )
        )

        return {
            "total": int(total or 0),
            "in_work": int(in_work or 0),
            "needs_payout": int(needs_payout or 0),
            "sum_to_company": int(sum_to_company or 0),
        }


async def get_master_month_finance(master_id: int, dt_from: datetime, dt_to: datetime) -> dict:
    """Финансы мастера за период. Считаем по статусу PAID ("Рассчитан")."""
    # Для PAID период должен считаться по paid_at (fallback: updated_at),
    # иначе если запись редактировали позже, цифры “гуляют”.
    paid_ts = func.coalesce(Order.paid_at, Order.updated_at)
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), 0),
            func.coalesce(func.sum(Order.expense_amount), 0),
            func.coalesce(func.sum(Order.net_amount), 0),
            func.coalesce(func.sum(Order.company_amount), 0),
            func.coalesce(func.avg(Order.percent_master_snapshot), 0),
        ).where(
            Order.assigned_master_id == master_id,
            Order.status == OrderStatus.PAID,
            paid_ts >= dt_from,
            paid_ts < dt_to,
        )
        cnt, gross, expenses, net, company, avg_percent = (await s.execute(q)).one()

        cnt = int(cnt or 0)
        gross = int(gross or 0)
        expenses = int(expenses or 0)
        net = int(net or 0)
        company = int(company or 0)
        try:
            avg_percent = int(round(float(avg_percent or 0)))
        except Exception:
            avg_percent = 0
        master_take = max(net - company, 0)
        avg_check = int(round(gross / cnt)) if cnt else 0

        return {
            "count_paid": cnt,
            "gross": gross,
            "expenses": expenses,
            "net": net,
            "company": company,
            "master_take": master_take,
            "avg_check": avg_check,
            "avg_percent": avg_percent,
        }


async def get_admin_month_finance(dt_from: datetime, dt_to: datetime) -> dict:
    """Финансы компании за период (по PAID)."""
    # Для PAID период должен считаться по paid_at (fallback: updated_at).
    paid_ts = func.coalesce(Order.paid_at, Order.updated_at)
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), 0),
            func.coalesce(func.sum(Order.expense_amount), 0),
            func.coalesce(func.sum(Order.net_amount), 0),
            func.coalesce(func.sum(Order.company_amount), 0),
            func.coalesce(func.avg(Order.percent_master_snapshot), 0),
        ).where(
            Order.status == OrderStatus.PAID,
            paid_ts >= dt_from,
            paid_ts < dt_to,
        )
        cnt, gross, expenses, net, company, avg_percent = (await s.execute(q)).one()

        cnt = int(cnt or 0)
        gross = int(gross or 0)
        expenses = int(expenses or 0)
        net = int(net or 0)
        company = int(company or 0)
        try:
            avg_percent = int(round(float(avg_percent or 0)))
        except Exception:
            avg_percent = 0
        masters = max(net - company, 0)
        avg_check = int(round(gross / cnt)) if cnt else 0

        # по офферам (сколько заявок/вал)
        res = await s.execute(
            select(
                Offer.title,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
            )
            .join(Offer, Offer.id == Order.offer_id)
            .where(
                Order.status == OrderStatus.PAID,
                paid_ts >= dt_from,
                paid_ts < dt_to,
            )
            .group_by(Offer.title)
            .order_by(func.count(Order.id).desc())
        )
        by_offer = [
            {"offer": t or "(без оффера)", "count": int(c or 0), "gross": int(g or 0)}
            for (t, c, g) in res.all()
        ]

        # по городам (средний чек)
        res2 = await s.execute(
            select(
                Order.city,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
            )
            .where(
                Order.status == OrderStatus.PAID,
                paid_ts >= dt_from,
                paid_ts < dt_to,
            )
            .group_by(Order.city)
            .order_by(func.count(Order.id).desc())
        )
        by_city = []
        for (city, c, g) in res2.all():
            c = int(c or 0)
            g = int(g or 0)
            by_city.append(
                {
                    "city": (city or "(не указан)"),
                    "count": c,
                    "gross": g,
                    "avg_check": int(round(g / c)) if c else 0,
                }
            )

        return {
            "count_paid": cnt,
            "gross": gross,
            "expenses": expenses,
            "masters": masters,
            "company": company,
            "avg_check": avg_check,
            "avg_percent": avg_percent,
            "by_offer": by_offer,
            "by_city": by_city,
        }


async def list_unaccepted_orders_older_than(cutoff_dt: datetime, limit: int = 50) -> List[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(
            select(Order)
            .where(
                Order.status == OrderStatus.NEW,
                Order.assigned_master_id.is_(None),
                Order.alerted_no_accept.is_(False),
                Order.created_at <= cutoff_dt,
            )
            .options(selectinload(Order.offer))
            .order_by(Order.created_at.asc())
            .limit(limit)
        )
        return list(res)


async def mark_order_no_accept_alerted(order_id: int) -> bool:
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o:
            return False
        o.alerted_no_accept = True
        await s.commit()
        return True


