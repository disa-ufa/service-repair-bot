from __future__ import annotations

import csv
from datetime import datetime, timedelta
from io import StringIO
from typing import Tuple

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from bot.db import repo
from bot.db.models import OrderStatus, Role

router = Router()


def _is_admin_role(role: Role) -> bool:
    return role in (Role.SUPER_ADMIN, Role.DISPATCHER)


async def _get_admin_or_deny(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return None
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован. Обратитесь к администратору.")
        return None
    if not _is_admin_role(u.role):
        await message.answer("Недостаточно прав.")
        return None
    return u


def _parse_period_from_args(args: str) -> Tuple[datetime, datetime, str]:
    """
    Поддержка:
    - "" (пусто) => 30 дней
    - "7" => последние 7 дней
    - "01.02.2026 15.02.2026" => диапазон дат (включительно)
    """
    args = (args or "").strip()
    now = datetime.utcnow()

    if not args:
        dt_from = now - timedelta(days=30)
        return dt_from, now, "30 дн."

    parts = args.split()

    if len(parts) == 1 and parts[0].isdigit():
        days = max(1, int(parts[0]))
        dt_from = now - timedelta(days=days)
        return dt_from, now, f"{days} дн."

    if len(parts) >= 2:
        d1 = datetime.strptime(parts[0], "%d.%m.%Y")
        d2 = datetime.strptime(parts[1], "%d.%m.%Y")
        if d2 < d1:
            d1, d2 = d2, d1
        dt_from = datetime(d1.year, d1.month, d1.day)
        dt_to = datetime(d2.year, d2.month, d2.day, 23, 59, 59)
        return dt_from, dt_to, f"{d1.strftime('%d.%m.%Y')}–{d2.strftime('%d.%m.%Y')}"

    dt_from = now - timedelta(days=30)
    return dt_from, now, "30 дн."


def _order_event_dt(row) -> datetime | None:
    """Выбираем правильный таймстемп для строки отчёта (закрытие/оплата)."""
    st = getattr(row, "status", None)

    # приоритет: paid_at / closed_at, иначе updated_at
    if st == OrderStatus.PAID:
        return getattr(row, "paid_at", None) or getattr(row, "updated_at", None)
    if st == OrderStatus.NEEDS_PAYOUT:
        return getattr(row, "closed_at", None) or getattr(row, "updated_at", None)
    if st == OrderStatus.ACCEPTED:
        return getattr(row, "accepted_at", None) or getattr(row, "updated_at", None)
    return getattr(row, "updated_at", None)


def _payouts_text(period_label: str, pending_rows, paid_rows) -> str:
    pending_cnt = len(pending_rows)
    pending_sum = sum(int(r.company_amount or 0) for r in pending_rows)
    paid_cnt = len(paid_rows)
    paid_sum = sum(int(r.company_amount or 0) for r in paid_rows)

    lines = [f"💰 <b>Выплаты</b>\nПериод: за {period_label}.\n"]

    lines.append(
        f"🟠 Ожидают сдачу (NEEDS_PAYOUT): <b>{pending_cnt}</b> | сумма к сдаче: <b>{pending_sum}</b>"
    )
    if pending_rows:
        for r in pending_rows[:30]:
            dt = _order_event_dt(r)
            upd = dt.strftime("%d.%m %H:%M") if dt else "—"
            lines.append(
                f"#{r.order_id} | {r.master_fio} | {r.city} | {r.offer_title} | к сдаче: {r.company_amount} | "
                f"итого: {r.total_amount} | расх: {r.expense_amount} | чеки: {r.expense_cnt} | дог: {r.contract_cnt} | "
                f"скрин: {'✅' if r.payout_proof else '-'} | upd: {upd}"
            )
    else:
        lines.append("— нет")

    lines.append("")
    lines.append(f"✅ Оплачены (PAID) за {period_label}: <b>{paid_cnt}</b> | сумма: <b>{paid_sum}</b>")
    if paid_rows:
        for r in paid_rows[:30]:
            dt = _order_event_dt(r)
            upd = dt.strftime("%d.%m %H:%M") if dt else "—"
            lines.append(
                f"#{r.order_id} | {r.master_fio} | {r.city} | {r.offer_title} | к сдаче: {r.company_amount} | "
                f"итого: {r.total_amount} | расх: {r.expense_amount} | чеки: {r.expense_cnt} | дог: {r.contract_cnt} | "
                f"скрин: {'✅' if r.payout_proof else '-'} | upd: {upd}"
            )
    else:
        lines.append("— нет")

    lines.append("\nПодсказка: /payouts 7 или /payouts 01.02.2026 15.02.2026")
    return "\n".join(lines)


def _masters_summary_text(period_label: str, summaries) -> str:
    total_pending = sum(s.pending_sum_company for s in summaries)
    total_paid = sum(s.paid_sum_company for s in summaries)

    lines = [
        f"👷 <b>Сводка по мастерам</b> (за {period_label})\n"
        f"🟠 Всего к сдаче (pending): <b>{total_pending}</b>\n"
        f"✅ Всего оплачено (paid): <b>{total_paid}</b>\n"
        "\n<pre>ФИО мастера | pending: cnt / sum | paid: cnt / sum | заработал</pre>",
    ]

    if not summaries:
        lines.append("— нет данных")
    else:
        for s in summaries[:30]:
            lines.append(
                f"<pre>{s.master_fio} | pending: {s.pending_cnt} / {s.pending_sum_company} | "
                f"paid: {s.paid_cnt} / {s.paid_sum_company} | заработал: {s.earned_sum}</pre>"
            )

    lines.append("\nПодсказка: /masters_summary 7 или /masters_summary 01.02.2026 15.02.2026")
    return "\n".join(lines)


def _rows_to_csv_bytes(rows) -> bytes:
    out = StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(
        [
            "order_id",
            "status",
            "master",
            "city",
            "offer",
            "company_amount",
            "total_amount",
            "expense_amount",
            "master_percent",
            "master_earned",
            "expense_cnt",
            "contract_cnt",
            "payout_proof",
            "updated_at",
        ]
    )
    for r in rows:
        dt = _order_event_dt(r)
        w.writerow(
            [
                r.order_id,
                getattr(r.status, "value", str(r.status)),
                r.master_fio,
                r.city,
                r.offer_title,
                r.company_amount,
                r.total_amount,
                r.expense_amount,
                r.percent,
                r.master_earned,
                r.expense_cnt,
                r.contract_cnt,
                1 if r.payout_proof else 0,
                dt.isoformat(sep=" ") if dt else "",
            ]
        )

    # UTF-8 BOM для Excel
    return ("\ufeff" + out.getvalue()).encode("utf-8")


@router.message(Command("payouts"))
async def payouts_cmd(message: Message):
    u = await _get_admin_or_deny(message)
    if not u:
        return
    args = message.text.split(maxsplit=1)
    dt_from, dt_to, label = _parse_period_from_args(args[1] if len(args) > 1 else "")
    pending = await repo.list_payout_rows(dt_from, dt_to, statuses=[OrderStatus.NEEDS_PAYOUT])
    paid = await repo.list_payout_rows(dt_from, dt_to, statuses=[OrderStatus.PAID])
    await message.answer(_payouts_text(label, pending, paid), parse_mode=ParseMode.HTML)


@router.message(Command("masters_summary"))
async def masters_summary_cmd(message: Message):
    u = await _get_admin_or_deny(message)
    if not u:
        return
    args = message.text.split(maxsplit=1)
    dt_from, dt_to, label = _parse_period_from_args(args[1] if len(args) > 1 else "")
    summaries = await repo.list_masters_finance_summary(dt_from, dt_to)
    await message.answer(_masters_summary_text(label, summaries), parse_mode=ParseMode.HTML)


@router.message(Command("payouts_csv"))
async def payouts_csv_cmd(message: Message):
    u = await _get_admin_or_deny(message)
    if not u:
        return
    args = message.text.split(maxsplit=1)
    dt_from, dt_to, label = _parse_period_from_args(args[1] if len(args) > 1 else "")
    rows = await repo.list_payout_rows(dt_from, dt_to, statuses=[OrderStatus.NEEDS_PAYOUT, OrderStatus.PAID])
    data = _rows_to_csv_bytes(rows)
    fname = f"payouts_{label.replace(' ', '').replace('.', '').replace('–', '-')}.csv"
    await message.answer_document(BufferedInputFile(data, filename=fname))


@router.message(Command("masters_summary_csv"))
async def masters_summary_csv_cmd(message: Message):
    u = await _get_admin_or_deny(message)
    if not u:
        return
    args = message.text.split(maxsplit=1)
    dt_from, dt_to, label = _parse_period_from_args(args[1] if len(args) > 1 else "")
    summaries = await repo.list_masters_finance_summary(dt_from, dt_to)

    out = StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(["master_id", "master", "pending_cnt", "pending_sum", "paid_cnt", "paid_sum", "earned_sum"])
    for s in summaries:
        w.writerow(
            [s.master_id, s.master_fio, s.pending_cnt, s.pending_sum_company, s.paid_cnt, s.paid_sum_company, s.earned_sum]
        )

    data = ("\ufeff" + out.getvalue()).encode("utf-8")
    fname = f"masters_summary_{label.replace(' ', '').replace('.', '').replace('–', '-')}.csv"
    await message.answer_document(BufferedInputFile(data, filename=fname))
