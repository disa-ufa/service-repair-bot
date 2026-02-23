# bot/db/repo_orders.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Iterable

from sqlalchemy import case, func, or_, select, delete
from sqlalchemy.orm import selectinload

from bot.db import get_sessionmaker
from bot.db.models import (
    Offer,
    Order,
    OrderFile,
    OrderStatus,
    OrderType,
    Role,
    User,
    OrderDispatchMessage,
)


# ---------------------------
# Orders
# ---------------------------
async def create_order(
    order_type: OrderType,
    client_name: str,
    client_phone: str,
    city: str,
    address: str,
    offer_id: Optional[int],
    problem: str,
    created_by_user_id: int,
    percent_master: Optional[int] = None,
    source: str | None = None,
    *,
    apartment: str | None = None,
) -> Order:
    sm = get_sessionmaker()
    async with sm() as s:
        o = Order(
            order_type=order_type,
            status=OrderStatus.NEW,
            created_by_user_id=created_by_user_id,
            assigned_master_id=None,
            client_name=(client_name or "").strip(),
            client_phone=(client_phone or "").strip(),
            city=(city or "").strip(),
            address=(address or "").strip(),
            apartment=((apartment or "").strip() or None),
            source=((source or "").strip() or None),
            offer_id=offer_id,
            problem=(problem or "").strip(),
            percent_master_snapshot=(int(percent_master) if percent_master is not None else None),
            alerted_no_accept=False,
        )
        s.add(o)
        await s.commit()
        await s.refresh(o)
        return o


async def get_order(order_id: int) -> Optional[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        return await s.get(Order, order_id)


async def list_orders(limit: int = 50, offset: int = 0) -> List[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(
            select(Order)
            .options(selectinload(Order.offer))
            .order_by(Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(res)


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _normalize_phone_expr(expr):
    """SQLite-совместимая нормализация телефона: убираем частые разделители."""
    for ch in [" ", "-", "(", ")", "+", ".", "\t", "\n", "\r", "\u00a0"]:
        expr = func.replace(expr, ch, "")
    return expr


async def count_orders_by_phone_digits(digits: str) -> int:
    dig = _digits_only(digits)
    if not dig:
        return 0

    sm = get_sessionmaker()
    async with sm() as s:
        phone_norm = _normalize_phone_expr(Order.client_phone)
        cnt = await s.scalar(select(func.count(Order.id)).where(phone_norm.like(f"%{dig}%")))
        return int(cnt or 0)


async def search_orders_by_phone_digits(digits: str, *, limit: int = 50, offset: int = 0) -> List[Order]:
    dig = _digits_only(digits)
    if not dig:
        return []

    sm = get_sessionmaker()
    async with sm() as s:
        phone_norm = _normalize_phone_expr(Order.client_phone)
        res = await s.scalars(
            select(Order)
            .where(phone_norm.like(f"%{dig}%"))
            .options(selectinload(Order.offer))
            .order_by(Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(res)



async def list_orders_filtered(
    *,
    statuses: Optional[Iterable[OrderStatus]] = None,
    assigned_master_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Order]:
    """Список заявок с фильтрами (для админских экранов).

    - statuses: набор статусов для фильтрации
    - assigned_master_id: фильтр по назначенному мастеру
    """
    sm = get_sessionmaker()
    async with sm() as s:
        q = (
            select(Order)
            .options(selectinload(Order.offer))
            .order_by(Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if statuses:
            q = q.where(Order.status.in_(list(statuses)))
        if assigned_master_id:
            q = q.where(Order.assigned_master_id == int(assigned_master_id))

        res = await s.scalars(q)
        return list(res)

async def get_order_with_users(order_id: int) -> Tuple[Optional[Order], Optional[User], Optional[User]]:
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.offer),
                selectinload(Order.created_by),
                selectinload(Order.assigned_master),
            )
        )
        if not o:
            return None, None, None
        return o, o.created_by, o.assigned_master


async def admin_update_order_fields(order_id: int, **fields) -> Optional[Order]:
    """
    Редактирование заявки админом/диспетчером.

    Разрешённые поля:
      - client_name (str, обязательно)
      - client_phone (str, обязательно)
      - city (str | None)  -> None очищает город
      - address (str, обязательно)
      - source (str | None) -> None очищает источник
      - offer_id (int | None)
      - problem (str, обязательно)
      - percent_master_snapshot (int 1..100)

    Возвращает обновлённую Order или None (если не найдено/нельзя редактировать/валидация не прошла).
    """
    allowed = {
        "client_name",
        "client_phone",
        "city",
        "address",
        "source",
        "offer_id",
        "problem",
        "percent_master_snapshot",
    }

    updates = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not updates:
        return await get_order(order_id)

    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, int(order_id))
        if not o:
            return None

        # Безопасно: не правим уже рассчитанные (PAID)
        if o.status == OrderStatus.PAID:
            return None

        if "client_name" in updates:
            v = (str(updates.get("client_name") or "").strip())
            if not v:
                return None
            o.client_name = v

        if "client_phone" in updates:
            v = (str(updates.get("client_phone") or "").strip())
            if not v:
                return None
            o.client_phone = v

        if "address" in updates:
            v = (str(updates.get("address") or "").strip())
            if not v:
                return None
            o.address = v

        if "city" in updates:
            v = updates.get("city")
            if v is None:
                o.city = None
            else:
                o.city = (str(v).strip() or None)

        if "source" in updates:
            v = updates.get("source")
            if v is None:
                o.source = None
            else:
                vv = (str(v).strip() or "")
                o.source = (vv or None)

        if "offer_id" in updates:
            v = updates.get("offer_id")
            if v is None:
                o.offer_id = None
            else:
                try:
                    oid = int(v)
                except Exception:
                    return None
                off = await s.get(Offer, oid)
                if not off:
                    return None
                o.offer_id = off.id

        if "problem" in updates:
            v = (str(updates.get("problem") or "").strip())
            if not v:
                return None
            o.problem = v

        if "percent_master_snapshot" in updates:
            try:
                p = int(updates.get("percent_master_snapshot"))
            except Exception:
                return None
            if p < 1 or p > 100:
                return None
            o.percent_master_snapshot = p

        await s.commit()
        await s.refresh(o)
        return o


async def list_orders_for_master(
    master: User,
    *,
    statuses: Optional[Iterable[OrderStatus]] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        q = (
            select(Order)
            .where(Order.assigned_master_id == master.id)
            .options(selectinload(Order.offer))
            .order_by(Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if statuses:
            q = q.where(Order.status.in_(list(statuses)))

        res = await s.scalars(q)
        return list(res)


async def count_orders_for_master(
    master: User,
    *,
    statuses: Optional[Iterable[OrderStatus]] = None,
) -> int:
    """Количество заявок мастера (опционально по статусам)."""
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(func.count(Order.id)).where(Order.assigned_master_id == master.id)
        if statuses:
            q = q.where(Order.status.in_(list(statuses)))
        cnt = await s.scalar(q)
        return int(cnt or 0)


async def list_available_orders_for_master(master: User) -> List[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        m = await s.get(User, master.id, options=[selectinload(User.offers)])
        if not m:
            return []

        # -----------------------------
        # 1) Приоритет: "доступные" = то, что реально было разослано мастеру
        #    (и не было скрыто кнопкой «Убрать», т.к. она удаляет запись рассылки).
        # -----------------------------
        try:
            subq = select(OrderDispatchMessage.order_id).where(OrderDispatchMessage.user_id == m.id)
            q_disp = (
                select(Order)
                .where(
                    Order.id.in_(subq),
                    Order.status == OrderStatus.NEW,
                    Order.assigned_master_id.is_(None),
                )
                .options(selectinload(Order.offer))
                .order_by(Order.id.desc())
            )
            dispatched = list(await s.scalars(q_disp))
            if dispatched:
                return dispatched
        except Exception:
            # Если таблица рассылок недоступна/сломана — используем фильтрацию по офферам/городу.
            pass

        offer_ids = [o.id for o in (m.offers or [])]

        # если офферы не настроены — не показываем заявки "всем подряд"
        if not offer_ids:
            return []

        m_city = (m.city or "").strip().lower()

        q = (
            select(Order)
            .where(Order.status == OrderStatus.NEW, Order.assigned_master_id.is_(None))
            .options(selectinload(Order.offer))
            .order_by(Order.id.desc())
        )

        q = q.where(Order.offer_id.in_(offer_ids))

        if m_city:
            q = q.where(or_(Order.city.is_(None), func.lower(Order.city) == m_city))

        res = await s.scalars(q)
        return list(res)


async def find_masters_for_order(offer_id: Optional[int], city: Optional[str]) -> List[User]:
    sm = get_sessionmaker()
    async with sm() as s:
        masters = await s.scalars(
            select(User)
            .where(User.role == Role.MASTER)
            .where(User.is_approved == True)  # noqa: E712
            .options(selectinload(User.offers))
            .order_by(User.id)
        )
        masters_list = list(masters)

        city_l = (city or "").strip().lower()

        out: List[User] = []
        for m in masters_list:
            # фильтр по городу (если город у заявки указан)
            if city_l:
                m_city = (m.city or "").strip().lower()
                if m_city and m_city != city_l:
                    continue

            # фильтр по офферу/технике
            if offer_id is not None:
                if not m.offers:
                    # если у мастера не настроены офферы — не шлём ему заявки
                    continue
                if not any(o.id == offer_id for o in m.offers):
                    continue

            out.append(m)

        return out


async def accept_order(order_id: int, master: User) -> Optional[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o:
            return None

        # Идемпотентность:
        # Если заявка уже закреплена за ЭТИМ мастером — считаем это успешным.
        if o.assigned_master_id == master.id and o.status in {
            OrderStatus.ACCEPTED,
            OrderStatus.IN_WORK,
            OrderStatus.MODERNIZATION,
            OrderStatus.NEEDS_PAYOUT,
            OrderStatus.PAID,
        }:
            await s.refresh(o)
            setattr(o, "_accepted_now", False)
            return o

        # Нельзя принять, если уже занят другим мастером или статус не NEW
        if o.status != OrderStatus.NEW or o.assigned_master_id is not None:
            return None

        # Обычное принятие
        o.status = OrderStatus.ACCEPTED
        o.assigned_master_id = master.id
        if o.percent_master_snapshot is None:
            o.percent_master_snapshot = master.percent_master

        # ✅ timestamp: момент принятия
        o.accepted_at = datetime.utcnow()
        o.alerted_no_accept = False
        o.updated_at = datetime.utcnow()

        await s.commit()
        await s.refresh(o)
        setattr(o, "_accepted_now", True)
        return o


async def set_order_status(order_id: int, master: User, new_status: OrderStatus) -> Optional[Order]:
    allowed = {OrderStatus.IN_WORK}
    if new_status not in allowed:
        return None

    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o or o.assigned_master_id != master.id:
            return None

        if o.status not in {OrderStatus.ACCEPTED, OrderStatus.IN_WORK, OrderStatus.MODERNIZATION}:
            return None

        o.status = new_status
        await s.commit()
        await s.refresh(o)
        return o


async def set_order_modernization(order_id: int, master: User, comment: str) -> Optional[Order]:
    c = (comment or "").strip()
    if not c:
        return None

    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o or o.assigned_master_id != master.id:
            return None

        if o.status not in {OrderStatus.ACCEPTED, OrderStatus.IN_WORK, OrderStatus.MODERNIZATION}:
            return None

        o.status = OrderStatus.MODERNIZATION
        o.modernization_comment = c

        await s.commit()
        await s.refresh(o)
        return o


async def close_order(
    order_id: int,
    master: User,
    sum_total: int,
    sum_expenses: int,
    warranty_days: Optional[int] = None,
    close_comment: str = "",
) -> Optional[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o or o.assigned_master_id != master.id:
            return None
        if o.status not in {OrderStatus.ACCEPTED, OrderStatus.IN_WORK, OrderStatus.MODERNIZATION}:
            return None

        total = int(sum_total)
        exp = int(sum_expenses)
        if total < 0 or exp < 0 or exp > total:
            return None

        net = total - exp
        percent = int(o.percent_master_snapshot or master.percent_master or 50)
        master_take = int(round(net * percent / 100))
        to_company = net - master_take

        o.total_amount = total
        o.expense_amount = exp
        o.net_amount = net
        o.company_amount = to_company
        o.percent_master_snapshot = percent

        if warranty_days is not None:
            wd = int(warranty_days)
            if wd < 0 or wd > 3650:
                return None
            o.warranty_days = wd
        if close_comment and close_comment.strip():
            o.close_comment = close_comment.strip()

        if exp > 0:
            receipts_cnt = await s.scalar(
                select(func.count(OrderFile.id)).where(OrderFile.order_id == o.id, OrderFile.kind == "expense")
            )
            if int(receipts_cnt or 0) < 1:
                return None

        o.status = OrderStatus.NEEDS_PAYOUT

        # ✅ timestamp: момент закрытия
        if o.closed_at is None:
            o.closed_at = datetime.utcnow()

        o.updated_at = datetime.utcnow()
        await s.commit()
        await s.refresh(o)
        return o


async def attach_order_payout_proof(order_id: int, master: User, file_id: str) -> Optional[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o or o.assigned_master_id != master.id:
            return None
        if o.status != OrderStatus.NEEDS_PAYOUT:
            return None

        o.payout_screenshot_file_id = file_id
        await s.commit()
        await s.refresh(o)
        return o


async def mark_order_paid(order_id: int) -> Optional[Order]:
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o:
            return None
        if o.status != OrderStatus.NEEDS_PAYOUT:
            return None

        o.status = OrderStatus.PAID

        # ✅ timestamp: момент подтверждения сдачи
        if o.paid_at is None:
            o.paid_at = datetime.utcnow()
        o.updated_at = datetime.utcnow()

        await s.commit()
        await s.refresh(o)
        return o


async def assign_order_to_master(order_id: int, master_id: int) -> Optional[Order]:
    """Принудительное назначение мастера админом (NEW -> ACCEPTED)."""
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        m = await s.get(User, master_id)
        if not o or not m or m.role != Role.MASTER:
            return None
        if o.status != OrderStatus.NEW or o.assigned_master_id is not None:
            return None

        o.status = OrderStatus.ACCEPTED
        o.assigned_master_id = m.id
        if o.percent_master_snapshot is None:
            o.percent_master_snapshot = m.percent_master

        # ✅ timestamp: момент назначения/принятия
        o.accepted_at = datetime.utcnow()
        o.alerted_no_accept = False
        o.updated_at = datetime.utcnow()

        await s.commit()
        await s.refresh(o)
        return o


async def unassign_order(order_id: int) -> Optional[Order]:
    """Снять мастера (только для статуса ACCEPTED)."""
    sm = get_sessionmaker()
    async with sm() as s:
        o = await s.get(Order, order_id)
        if not o:
            return None
        if o.status != OrderStatus.ACCEPTED:
            return None

        o.status = OrderStatus.NEW
        o.assigned_master_id = None

        # ✅ сбрасываем accepted_at, чтобы следующее принятие было с новым временем
        o.accepted_at = None
        o.alerted_no_accept = False
        o.updated_at = datetime.utcnow()
        await s.commit()
        await s.refresh(o)
        return o


async def list_master_cash_orders(master: User, limit: int = 50) -> list[Order]:
    """Долги мастера: заявки в NEEDS_PAYOUT."""
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(
            select(Order)
            .where(Order.assigned_master_id == master.id, Order.status == OrderStatus.NEEDS_PAYOUT)
            .options(selectinload(Order.offer))
            .order_by(Order.updated_at.desc())
            .limit(limit)
        )
        return list(res)


# -------------------------
# Messages delivered to masters (for auto-delete / "Убрать")
# -------------------------


async def upsert_order_dispatch_message(
    order_id: int,
    user_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    """Сохранить (или обновить) информацию о сообщении, отправленном мастеру."""

    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.scalar(
            select(OrderDispatchMessage).where(
                OrderDispatchMessage.order_id == order_id,
                OrderDispatchMessage.user_id == user_id,
            )
        )
        if row is None:
            s.add(
                OrderDispatchMessage(
                    order_id=order_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                )
            )
        else:
            row.chat_id = chat_id
            row.message_id = message_id
        await s.commit()


async def list_order_dispatch_messages(order_id: int) -> list[OrderDispatchMessage]:
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.scalars(
            select(OrderDispatchMessage).where(OrderDispatchMessage.order_id == order_id)
        )
        return list(res)


async def delete_order_dispatch_messages(order_id: int, user_id: int | None = None) -> int:
    """Удалить записи по заявке (опционально — только для одного мастера).

    Возвращает кол-во удалённых строк.
    """

    sm = get_sessionmaker()
    async with sm() as s:
        stmt = delete(OrderDispatchMessage).where(OrderDispatchMessage.order_id == order_id)
        if user_id is not None:
            stmt = stmt.where(OrderDispatchMessage.user_id == user_id)
        result = await s.execute(stmt)
        await s.commit()
        return int(result.rowcount or 0)
