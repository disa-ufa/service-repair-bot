from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from sqlalchemy import text

from bot.db.models import Base, Offer

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


DEFAULT_OFFERS = [
    "Телевизор",
    "Кофемашина",
    "Компьютер",
    "Холодильник",
    "Стиральная машина",
    "Посудомоечная машина",
    "Духовой шкаф",
    "Варочная панель",
    "Встроенные СВЧ",
    "Кондиционер",
]


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    assert _sessionmaker is not None, "DB is not initialized. Call init_db() first."
    return _sessionmaker


async def _ensure_schema(conn) -> None:
    """
    Мягкие миграции для SQLite: добавляем недостающие колонки.
    """
    # orders.*
    res = await conn.exec_driver_sql("PRAGMA table_info(orders)")
    order_cols = {row[1] for row in res.fetchall()}  # row[1] = name

    if "modernization_comment" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN modernization_comment TEXT")

    if "warranty_days" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN warranty_days INTEGER")

    if "close_comment" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN close_comment TEXT")

    if "source" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN source TEXT")

    if "percent_master_snapshot" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN percent_master_snapshot INTEGER")

    if "alerted_no_accept" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN alerted_no_accept INTEGER DEFAULT 0")

    if "payout_screenshot_file_id" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN payout_screenshot_file_id VARCHAR(256)")

    if "accepted_at" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN accepted_at DATETIME")
    if "closed_at" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN closed_at DATETIME")
    if "paid_at" not in order_cols:
        await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN paid_at DATETIME")

    # users.*
    res_u = await conn.exec_driver_sql("PRAGMA table_info(users)")
    user_cols = {row[1] for row in res_u.fetchall()}

    if "city" not in user_cols:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN city TEXT")

    # подтверждение мастера супер-админом
    if "is_approved" not in user_cols:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN is_approved INTEGER DEFAULT 0")
        # ВАЖНО: режим «Все заново» — после добавления колонки все мастера становятся неподтверждёнными
        await conn.exec_driver_sql("UPDATE users SET is_approved = 0 WHERE role IN ('master','MASTER')")


async def init_db(db_url: str):
    global _engine, _sessionmaker

    if _engine is None:
        _engine = create_async_engine(db_url, echo=False)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_schema(conn)

    sm = get_sessionmaker()
    async with sm() as s:
        for title in DEFAULT_OFFERS:
            exists = await s.scalar(select(Offer).where(Offer.title == title))
            if not exists:
                s.add(Offer(title=title, is_active=True))
        await s.commit()
