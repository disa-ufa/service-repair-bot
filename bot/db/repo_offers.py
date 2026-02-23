# bot/db/repo_offers.py
from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.db import get_sessionmaker
from bot.db.models import Offer, Role, User


def _norm(s: str) -> str:
    # casefold() корректнее lower() для Unicode/кириллицы
    return (s or "").strip().casefold()


# ---------------------------
# Offers (Каталог техники)
# ---------------------------
async def list_offers(include_inactive: bool = False) -> List[Offer]:
    """Список офферов (техники).
    include_inactive=False -> только активные (is_active=True)
    include_inactive=True  -> все (включая скрытые/удалённые)
    """
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(Offer)
        if not include_inactive:
            q = q.where(Offer.is_active.is_(True))
        q = q.order_by(Offer.id)
        res = await s.scalars(q)
        return list(res)


async def list_active_offers() -> List[Offer]:
    """Только активные офферы (техника), доступные для выбора мастером."""
    return await list_offers(include_inactive=False)


async def deactivate_offer(offer_id: int) -> bool:
    """Скрыть оффер из каталога (мягкое удаление)."""
    sm = get_sessionmaker()
    async with sm() as s:
        off = await s.get(Offer, offer_id)
        if not off:
            return False
        off.is_active = False
        await s.commit()
        return True


async def activate_offer(offer_id: int) -> bool:
    """Вернуть оффер в каталог."""
    sm = get_sessionmaker()
    async with sm() as s:
        off = await s.get(Offer, offer_id)
        if not off:
            return False
        off.is_active = True
        await s.commit()
        return True


async def get_or_create_offer_by_title(title: str) -> Offer:
    t = (title or "").strip()
    if not t:
        raise ValueError("Пустое название оффера")

    sm = get_sessionmaker()
    async with sm() as s:
        # SQLite lower() не дружит с кириллицей — сравниваем в Python
        existing = list(await s.scalars(select(Offer)))
        key = _norm(t)
        for off in existing:
            if _norm(off.title) == key:
                # если ранее "удаляли" — реактивируем
                if not getattr(off, "is_active", True):
                    off.is_active = True
                    await s.commit()
                    await s.refresh(off)
                return off

        off = Offer(title=t, is_active=True)
        s.add(off)
        await s.commit()
        await s.refresh(off)
        return off


async def create_offer(title: str) -> Offer:
    """Создать оффер (если уже есть — вернёт существующий; если был скрыт — реактивирует)."""
    return await get_or_create_offer_by_title(title)


# ---------------------------
# Master ↔ Offers (привязки)
# ---------------------------
async def get_master_offer_ids(user_id: int) -> list[int]:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, user_id, options=[selectinload(User.offers)])
        if not u or not u.offers:
            return []
        return [o.id for o in u.offers]


async def set_master_offers_by_titles(user_id: int, titles: list[str]) -> bool:
    """Полностью перезаписать офферы мастера по списку названий.
    (Этот режим оставлен на всякий случай, но для регистрации лучше IDs.)
    """
    norm_titles = [t.strip() for t in (titles or []) if t and t.strip()]
    if not norm_titles:
        return False

    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, user_id, options=[selectinload(User.offers)])
        if not u:
            return False

        existing = list(await s.scalars(select(Offer)))
        offer_map = {_norm(o.title): o for o in existing}

        offers: list[Offer] = []
        for t in norm_titles:
            k = _norm(t)
            off = offer_map.get(k)
            if not off:
                off = Offer(title=t.strip(), is_active=True)
                s.add(off)
                await s.flush()
                offer_map[k] = off
            else:
                # реактивируем если был скрыт
                if not getattr(off, "is_active", True):
                    off.is_active = True
            offers.append(off)

        u.offers = offers
        await s.commit()
        return True


async def set_master_offers_by_ids(user_id: int, offer_ids: list[int]) -> bool:
    """Полностью перезаписать офферы мастера по списку ID.
    Важно: НИЧЕГО не создаём, мастер выбирает только из существующего каталога.
    """
    ids = [int(x) for x in (offer_ids or []) if str(x).isdigit()]
    ids = list(dict.fromkeys(ids))  # uniq preserve order
    if not ids:
        return False

    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, user_id, options=[selectinload(User.offers)])
        if not u:
            return False

        offs = list(await s.scalars(select(Offer).where(Offer.id.in_(ids))))
        off_map = {o.id: o for o in offs}
        u.offers = [off_map[i] for i in ids if i in off_map]
        await s.commit()
        return True


async def toggle_master_offer(user_id: int, offer_id: int) -> bool:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, user_id, options=[selectinload(User.offers)])
        off = await s.get(Offer, offer_id)
        if not u or not off:
            return False

        if off in u.offers:
            u.offers.remove(off)
            added = False
        else:
            u.offers.append(off)
            added = True

        await s.commit()
        return added


async def list_masters(include_unapproved: bool = False) -> List[User]:
    """Список мастеров."""
    sm = get_sessionmaker()
    async with sm() as s:
        q = (
            select(User)
            .where(User.role == Role.MASTER)
            .options(selectinload(User.offers))
            .order_by(User.id)
        )
        if not include_unapproved:
            q = q.where(User.is_approved.is_(True))
        res = await s.scalars(q)
        return list(res)
