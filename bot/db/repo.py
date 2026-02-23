from __future__ import annotations

# Facade module: re-exports repo functions split across smaller files.

from .repo_users import *  # noqa
from .repo_offers import *  # noqa
from .repo_orders import *  # noqa
from .repo_files import *  # noqa
from .repo_stats import *  # noqa
from .repo_reports import *  # noqa
from .repo_settings import *  # noqa

from sqlalchemy import select

from bot.db import get_sessionmaker
from bot.db.models import User, Role


async def list_users_by_role(role: Role) -> list[User]:
    """
    Возвращает пользователей по роли.
    Важно: НЕ переопределяем set_user_role — он уже корректно реализован в repo_users.py
    (работает по tg_id и возвращает обновлённого пользователя).
    """
    sm = get_sessionmaker()
    async with sm() as session:
        res = await session.scalars(
            select(User).where(User.role == role).order_by(User.id.desc())
        )
        return list(res)
