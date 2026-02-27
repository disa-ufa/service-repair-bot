from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from bot.db import get_sessionmaker
from bot.db.models import Offer, Order, OrderFile, OrderStatus, OrderType, Role, User


# ---------------------------
# Order files
# ---------------------------
async def add_order_files(order_id: int, kind: str, file_ids: List[str]) -> int:
    kind = (kind or "").strip().lower()
    if kind not in {"expense", "contract", "act"}:
        return 0
    if not file_ids:
        return 0

    sm = get_sessionmaker()
    async with sm() as s:
        for fid in file_ids:
            s.add(OrderFile(order_id=order_id, kind=kind, file_id=str(fid)))
        await s.commit()
        return len(file_ids)


async def list_order_files(order_id: int, kind: str) -> List[str]:
    kind = (kind or "").strip().lower()
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(
            select(OrderFile.file_id)
            .where(OrderFile.order_id == order_id, OrderFile.kind == kind)
            .order_by(OrderFile.id.asc())
        )
        return list(res)


async def count_order_files(order_id: int, kind: str) -> int:
    kind = (kind or "").strip().lower()
    sm = get_sessionmaker()
    async with sm() as s:
        cnt = await s.scalar(
            select(func.count(OrderFile.id)).where(OrderFile.order_id == order_id, OrderFile.kind == kind)
        )
        return int(cnt or 0)


