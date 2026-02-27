from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db import repo
from bot.db.models import Role

router = Router()


def _is_super_admin(role: Role) -> bool:
    return role == Role.SUPER_ADMIN


ROLE_ALIASES = {
    "super": Role.SUPER_ADMIN,
    "super_admin": Role.SUPER_ADMIN,
    "admin": Role.SUPER_ADMIN,
    "dispatcher": Role.DISPATCHER,
    "disp": Role.DISPATCHER,
    "master": Role.MASTER,
    "worker": Role.MASTER,
    "fired": Role.FIRED,
    "ban": Role.FIRED,
    "blocked": Role.FIRED,
}


async def _deny_if_not_super_admin(message: Message) -> bool:
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return True
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован.")
        return True
    if not _is_super_admin(u.role):
        await message.answer("⛔ Доступно только супер-администратору.")
        return True
    return False


@router.message(Command("users"))
async def cmd_users(message: Message, command: CommandObject):
    if await _deny_if_not_super_admin(message):
        return

    args = (command.args or "").strip()
    limit = 30
    offset = 0
    if args:
        # /users 50 0
        parts = args.split()
        try:
            if len(parts) >= 1:
                limit = max(1, min(100, int(parts[0])))
            if len(parts) >= 2:
                offset = max(0, int(parts[1]))
        except Exception:
            pass

    users = await repo.list_users(limit=limit, offset=offset)
    if not users:
        await message.answer("Пользователей пока нет.")
        return

    lines = ["<b>Пользователи</b> (последние сверху):"]
    for u in users:
        fio = (u.fio or "").strip() or "—"
        uname = f"@{u.username}" if u.username else ""
        lines.append(f"• tg:<code>{u.tg_id}</code> | {fio} {uname} | <b>{u.role.value}</b>")

    lines.append("")
    lines.append("<b>Смена роли:</b> /setrole <tg_id> <role>")
    lines.append("role: super_admin | dispatcher | master | fired")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("setrole"))
async def cmd_setrole(message: Message, command: CommandObject):
    if await _deny_if_not_super_admin(message):
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Использование: /setrole <tg_id> <role>\n"
            "Пример: /setrole 123456789 dispatcher",
        )
        return

    parts = args.split()
    if len(parts) < 2:
        await message.answer("Нужно 2 параметра: tg_id и role")
        return

    try:
        tg_id = int(parts[0])
    except Exception:
        await message.answer("tg_id должен быть числом")
        return

    role_raw = parts[1].lower().strip()
    role = ROLE_ALIASES.get(role_raw)
    if role is None:
        await message.answer("Неизвестная роль. Используйте: super_admin / dispatcher / master / fired")
        return

    updated = await repo.set_user_role(tg_id, role)
    if not updated:
        await message.answer("Пользователь не найден. Он должен сначала зайти в бота (/start).")
        return

    await message.answer(
        f"✅ Роль обновлена: tg:<code>{updated.tg_id}</code> — <b>{updated.role.value}</b>",
        parse_mode=ParseMode.HTML,
    )
