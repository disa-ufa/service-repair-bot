from __future__ import annotations

import enum
from typing import Optional, List
from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    Enum as SAEnum,
    DateTime,
    ForeignKey,
    Text,
    Table,
    Column,
    func,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(enum.Enum):
    SUPER_ADMIN = "super_admin"
    DISPATCHER = "dispatcher"
    MASTER = "master"
    FIRED = "fired"


class OrderType(enum.Enum):
    NEW = "new"
    REPEAT = "repeat"
    WARRANTY = "warranty"


class OrderStatus(enum.Enum):
    NEW = "new"  # создана, ожидает принятия
    ACCEPTED = "accepted"  # мастер принял
    IN_WORK = "in_work"  # мастер приступил
    MODERNIZATION = "modernization"  # забрал технику/длительный ремонт
    CLOSED = "closed"  # мастер закрыл (не используем, но оставлено)
    NEEDS_PAYOUT = "needs_payout"  # требуется сдача + скрин
    PAID = "paid"  # админ подтвердил оплату


# many-to-many: мастер <-> офферы/техника
user_offers = Table(
    "user_offers",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("offer_id", ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fio: Mapped[str] = mapped_column(String(256), default="")

    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.MASTER)
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    percent_master: Mapped[int] = mapped_column(Integer, default=50)

    # 👇 безопасно: если в коде используется подтверждение мастера
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    offers: Mapped[List["Offer"]] = relationship(
        "Offer",
        secondary=user_offers,
        back_populates="users",
        lazy="selectin",
    )


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[List[User]] = relationship(
        "User",
        secondary=user_offers,
        back_populates="offers",
        lazy="selectin",
    )

    # если где-то грузят Offer.orders
    orders: Mapped[List["Order"]] = relationship("Order", back_populates="offer", lazy="selectin")


class AppSetting(Base):
    __tablename__ = "app_settings"

    # key-value хранилище настроек (для редактирования из бота)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    order_type: Mapped[OrderType] = mapped_column(SAEnum(OrderType), default=OrderType.NEW)
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.NEW)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_master_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    client_name: Mapped[str] = mapped_column(String(256), default="")
    client_phone: Mapped[str] = mapped_column(String(64), default="")
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    address: Mapped[str] = mapped_column(String(256), default="")
    apartment: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    source: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    offer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("offers.id"), nullable=True, index=True)
    problem: Mapped[str] = mapped_column(Text, default="")

    modernization_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ✅ деньги при закрытии (ИМЕННО ИХ ЖДЁТ orders_admin.py)
    total_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expense_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    net_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    company_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    percent_master_snapshot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    warranty_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    close_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    payout_screenshot_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    alerted_no_accept: Mapped[bool] = mapped_column(Boolean, default=False)

    # relationships (их ждут репозитории/handlers)
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_user_id], lazy="selectin")
    assigned_master: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assigned_master_id], lazy="selectin")
    offer: Mapped[Optional["Offer"]] = relationship("Offer", back_populates="orders", lazy="selectin")

    files: Mapped[List["OrderFile"]] = relationship(
        "OrderFile",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OrderFile(Base):
    __tablename__ = "order_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # "expense" | "contract" | "act"
    file_id: Mapped[str] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["Order"] = relationship("Order", back_populates="files", lazy="selectin")


class OrderDispatchMessage(Base):
    """Сообщения о новой заявке, отправленные мастерам.

    Храним chat_id/message_id (личные чаты мастеров), чтобы:
    1) при принятии заявки одним мастером автоматически удалить уведомления у остальных;
    2) по кнопке «🗑 Убрать» скрыть уведомление только у мастера, который нажал кнопку.
    """

    __tablename__ = "order_dispatch_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("order_id", "user_id", name="uq_order_dispatch_order_user"),
    )
