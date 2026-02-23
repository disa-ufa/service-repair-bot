from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from bot.db import get_sessionmaker
from bot.db.models import Offer, Order, OrderFile, OrderStatus, OrderType, Role, User


# ---------------------------
# Users
# ---------------------------
async def get_user_by_id(user_id: int) -> Optional[User]:
    sm = get_sessionmaker()
    async with sm() as s:
        return await s.get(User, user_id)


async def get_user_by_tg_id(tg_id: int) -> Optional[User]:
    sm = get_sessionmaker()
    async with sm() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def count_users(role: Optional[Role] = None) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(func.count(User.id))
        if role is not None:
            q = q.where(User.role == role)
        v = await s.scalar(q)
        return int(v or 0)


async def set_user_role(tg_id: int, role: Role) -> Optional[User]:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.scalar(select(User).where(User.tg_id == tg_id))
        if not u:
            return None
        u.role = role
        await s.commit()
        await s.refresh(u)
        return u


async def list_users(limit: int = 50, offset: int = 0) -> List[User]:
    """Список пользователей (для супер-админа)."""
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(select(User).order_by(User.id.desc()).limit(limit).offset(offset))
        return list(res)


async def upsert_user(
    tg_id: int,
    username: Optional[str],
    fio: str,
    role: Role,
    city: Optional[str] = None,
    percent_master: Optional[int] = None,
    is_approved: Optional[bool] = None,
) -> User:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.scalar(select(User).where(User.tg_id == tg_id))
        if u:
            u.username = username
            u.fio = fio
            u.role = role
            if city is not None:
                u.city = (city or "").strip() or None
            if percent_master is not None:
                u.percent_master = int(percent_master)
            if is_approved is not None:
                u.is_approved = bool(is_approved)
        else:
            u = User(
                tg_id=tg_id,
                username=username,
                fio=fio,
                role=role,
                city=(city or "").strip() or None,
                percent_master=int(percent_master or 50),
                is_approved=bool(is_approved) if is_approved is not None else (role != Role.MASTER),
            )
            s.add(u)

        await s.commit()
        await s.refresh(u)
        return u




async def set_user_approved(user_id: int, approved: bool = True) -> Optional[User]:
    """Подтвердить/снять подтверждение пользователя (используем для мастеров)."""
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, user_id)
        if not u:
            return None
        u.is_approved = bool(approved)
        await s.commit()
        await s.refresh(u)
        return u


async def list_masters_pending_approval() -> List[User]:
    """Мастера, ожидающие подтверждения супер-админом."""
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(
            select(User)
            .where(User.role == Role.MASTER)
            .where(or_(User.is_approved == False, User.is_approved.is_(None)))  # noqa: E712
            .order_by(User.id.desc())
        )
        return list(res)
