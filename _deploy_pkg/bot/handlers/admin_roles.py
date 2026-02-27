from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db import repo
from bot.db.models import Role

router = Router()


def _is_super_admin(role: Role | None) -> bool:
    return role == Role.SUPER_ADMIN


async def _get_user(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return None
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ заблокирован.")
        return None
    return u


async def _require_super_admin(message: Message):
    u = await _get_user(message)
    if not u:
        return None
    if not _is_super_admin(u.role):
        await message.answer("Недостаточно прав. Только SUPER_ADMIN.")
        return None
    return u


def _reply_target_tg_id(message: Message) -> int | None:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return None
    return message.reply_to_message.from_user.id


@router.message(Command("make_dispatcher"))
async def make_dispatcher(message: Message):
    admin = await _require_super_admin(message)
    if not admin:
        return

    target_tg_id = _reply_target_tg_id(message)
    if not target_tg_id:
        await message.answer(
            "Используй команду ответом на сообщение пользователя.\n"
            "Пример: ответь на сообщение и напиши /make_dispatcher"
        )
        return

    target = await repo.get_user_by_tg_id(target_tg_id)
    if not target:
        await message.answer("Пользователь ещё не делал /start. Пусть сначала нажмёт /start.")
        return

    updated = await repo.set_user_role(target_tg_id, Role.DISPATCHER)
    if not updated:
        await message.answer("Не удалось обновить роль (пользователь не найден).")
        return

    await message.answer(
        f"✅ Готово: {getattr(updated, 'fio', 'Пользователь')} теперь <b>DISPATCHER</b>."
    )


@router.message(Command("make_master"))
async def make_master(message: Message):
    admin = await _require_super_admin(message)
    if not admin:
        return

    target_tg_id = _reply_target_tg_id(message)
    if not target_tg_id:
        await message.answer("Ответь на сообщение пользователя и напиши /make_master")
        return

    target = await repo.get_user_by_tg_id(target_tg_id)
    if not target:
        await message.answer("Пользователь ещё не делал /start.")
        return

    updated = await repo.set_user_role(target_tg_id, Role.MASTER)
    if not updated:
        await message.answer("Не удалось обновить роль (пользователь не найден).")
        return

    await message.answer(
        f"✅ Готово: {getattr(updated, 'fio', 'Пользователь')} теперь <b>MASTER</b>."
    )


@router.message(Command("make_fired"))
async def make_fired(message: Message):
    admin = await _require_super_admin(message)
    if not admin:
        return

    target_tg_id = _reply_target_tg_id(message)
    if not target_tg_id:
        await message.answer("Ответь на сообщение пользователя и напиши /make_fired")
        return

    target = await repo.get_user_by_tg_id(target_tg_id)
    if not target:
        await message.answer("Пользователь ещё не делал /start.")
        return

    updated = await repo.set_user_role(target_tg_id, Role.FIRED)
    if not updated:
        await message.answer("Не удалось обновить роль (пользователь не найден).")
        return

    await message.answer(
        f"⛔ {getattr(updated, 'fio', 'Пользователь')} заблокирован (FIRED)."
    )


@router.message(Command("dispatchers"))
async def list_dispatchers(message: Message):
    admin = await _require_super_admin(message)
    if not admin:
        return

    items = await repo.list_users_by_role(Role.DISPATCHER)
    if not items:
        await message.answer("Диспетчеров пока нет.")
        return

    lines = ["🧑‍💼 <b>Диспетчеры</b>:"]
    for u in items[:100]:
        fio = getattr(u, "fio", None) or "—"
        tg = getattr(u, "tg_id", None) or "—"
        lines.append(f"• {fio} (tg_id={tg})")

    await message.answer("\n".join(lines))
