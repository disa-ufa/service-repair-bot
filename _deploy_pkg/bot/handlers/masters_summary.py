from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    BufferedInputFile,
)

from bot.db import repo
from bot.db.models import Role, OrderStatus

router = Router()


def _is_admin_role(role: Role) -> bool:
    return role in (Role.SUPER_ADMIN, Role.DISPATCHER)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _period_kb(days: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                InlineKeyboardButton(text=("✅ 7 дн." if days == 7 else "7 дн."), callback_data="ms:period:7"),
                InlineKeyboardButton(text=("✅ 30 дн." if days == 30 else "30 дн."), callback_data="ms:period:30"),
                InlineKeyboardButton(text=("✅ 90 дн." if days == 90 else "90 дн."), callback_data="ms:period:90"),
            ],
            [
                InlineKeyboardButton(text="📄 CSV", callback_data=f"ms:csv:{days}"),
            ],
        ]
    )


def _masters_kb(days: int, masters: list[dict]) -> Optional[InlineKeyboardMarkup]:
    if not masters:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for m in masters[:40]:
        fio = (m.get("fio") or "—").strip()
        if len(fio) > 22:
            fio = fio[:22] + "…"
        btn = InlineKeyboardButton(text=fio, callback_data=f"ms:master:{days}:{m['master_id']}")
        row.append(btn)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="⬅️ Назад к периоду", callback_data=f"ms:back:{days}")])
    return _kb(rows)


def _fmt_money(x) -> int:
    try:
        return int(x or 0)
    except Exception:
        return 0


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return "—"


def _status_label(s: OrderStatus) -> str:
    try:
        return s.value
    except Exception:
        return str(s)


async def _get_user(tg_id: int):
    u = await repo.get_user_by_tg_id(tg_id)
    if not u:
        return None
    if u.role == Role.FIRED:
        return None
    return u


async def _send_summary_to_message(msg: Message, days: int):
    summary = await repo.get_masters_payout_summary(days=days)

    total_needs_cnt = sum(int(x.get("needs_cnt") or 0) for x in summary)
    total_paid_cnt = sum(int(x.get("paid_cnt") or 0) for x in summary)
    total_needs_sum = sum(_fmt_money(x.get("needs_sum")) for x in summary)
    total_paid_sum = sum(_fmt_money(x.get("paid_sum")) for x in summary)

    text_lines = [
        "👷 <b>Сводка по мастерам</b>",
        f"Период: <b>{days} дн.</b>",
        "",
        f"🟠 NEEDS_PAYOUT: <b>{total_needs_cnt}</b> | сумма к сдаче: <b>{total_needs_sum}</b>",
        f"✅ PAID: <b>{total_paid_cnt}</b> | сумма: <b>{total_paid_sum}</b>",
        "",
    ]

    if not summary:
        text_lines.append("Нет данных за период.")
        await msg.answer("\n".join(text_lines), reply_markup=_period_kb(days), parse_mode=ParseMode.HTML)
        return

    for m in summary[:30]:
        fio = (m.get("fio") or "—").strip()
        needs_cnt = int(m.get("needs_cnt") or 0)
        needs_sum = _fmt_money(m.get("needs_sum"))
        paid_cnt = int(m.get("paid_cnt") or 0)
        paid_sum = _fmt_money(m.get("paid_sum"))
        total_sum = _fmt_money(m.get("total_sum"))
        text_lines.append(
            f"• <b>{fio}</b> | needs: {needs_cnt}/{needs_sum} | paid: {paid_cnt}/{paid_sum} | итого: {total_sum}"
        )

    await msg.answer("\n".join(text_lines), reply_markup=_period_kb(days), parse_mode=ParseMode.HTML)

    mkb = _masters_kb(days, summary)
    if mkb:
        await msg.answer("Выберите мастера для детализации:", reply_markup=mkb)


# ✅ Запуск ИНТЕРАКТИВНОЙ сводки — ТОЛЬКО КОМАНДОЙ (чтобы не конфликтовать с кнопкой меню)
@router.message(Command("masters_ui"))
@router.message(Command("ms"))
async def masters_summary_ui_entry(message: Message):
    u = await _get_user(message.from_user.id)
    if not u or not _is_admin_role(u.role):
        await message.answer("Только для админов/диспетчеров.")
        return
    await _send_summary_to_message(message, days=30)


@router.callback_query(F.data.startswith("ms:period:"))
async def masters_summary_period(cb: CallbackQuery):
    if not cb.message:
        return

    u = await _get_user(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        days = int(cb.data.split(":")[2])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    await cb.answer("Ок")
    await _send_summary_to_message(cb.message, days=days)


@router.callback_query(F.data.startswith("ms:csv:"))
async def masters_summary_csv(cb: CallbackQuery):
    if not cb.message:
        return

    u = await _get_user(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        days = int(cb.data.split(":")[2])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    summary = await repo.get_masters_payout_summary(days=days)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["master_id", "fio", "needs_cnt", "needs_sum", "paid_cnt", "paid_sum", "total_sum"])

    for m in summary:
        writer.writerow(
            [
                m.get("master_id"),
                (m.get("fio") or "").strip(),
                int(m.get("needs_cnt") or 0),
                _fmt_money(m.get("needs_sum")),
                int(m.get("paid_cnt") or 0),
                _fmt_money(m.get("paid_sum")),
                _fmt_money(m.get("total_sum")),
            ]
        )

    data_bytes = ("\ufeff" + output.getvalue()).encode("utf-8")
    filename = f"masters_summary_{days}d_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    await cb.message.answer_document(
        BufferedInputFile(data_bytes, filename=filename),
        caption=f"Сводка по мастерам за {days} дн.",
    )
    await cb.answer("Готово")


@router.callback_query(F.data.startswith("ms:master:"))
async def masters_summary_master(cb: CallbackQuery):
    if not cb.message:
        return

    u = await _get_user(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer("Ошибка", show_alert=True)
        return

    days = int(parts[2])
    master_id = int(parts[3])

    master = await repo.get_user_by_id(master_id)
    if not master:
        await cb.answer("Мастер не найден", show_alert=True)
        return

    orders = await repo.list_master_payout_orders(master_id=master_id, days=days, limit=30)

    needs_sum = sum(_fmt_money(o.get("company_amount")) for o in orders if o.get("status") == OrderStatus.NEEDS_PAYOUT)
    paid_sum = sum(_fmt_money(o.get("company_amount")) for o in orders if o.get("status") == OrderStatus.PAID)

    lines = [
        f"👷 <b>{master.fio}</b>",
        f"Период: <b>{days} дн.</b>",
        f"🟠 NEEDS_PAYOUT сумма: <b>{needs_sum}</b>",
        f"✅ PAID сумма: <b>{paid_sum}</b>",
        "",
    ]

    if not orders:
        lines.append("Нет выплатных заявок за период.")
    else:
        for o in orders:
            status = o.get("status")
            lines.append(
                f"#{o['id']} | {_status_label(status)} | {o.get('city') or '—'} | {o.get('offer_title') or '—'} | "
                f"к сдаче: {_fmt_money(o.get('company_amount'))} | чеки:{int(o.get('expense_cnt') or 0)} | "
                f"дог:{int(o.get('contract_cnt') or 0)} | скрин:{'✅' if o.get('has_payout_proof') else '—'} | "
                f"upd:{_fmt_dt(o.get('updated_at'))}"
            )

    kb_rows: list[list[InlineKeyboardButton]] = []
    for o in orders[:10]:
        kb_rows.append([InlineKeyboardButton(text=f"Открыть #{o['id']}", callback_data=f"order_view:{o['id']}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад к сводке", callback_data=f"ms:period:{days}")])

    await cb.message.answer("\n".join(lines), reply_markup=_kb(kb_rows), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data.startswith("ms:back:"))
async def masters_summary_back(cb: CallbackQuery):
    if not cb.message:
        return
    await cb.answer("Ок")
    try:
        days = int(cb.data.split(":")[2])
    except Exception:
        days = 30
    await _send_summary_to_message(cb.message, days=days)
