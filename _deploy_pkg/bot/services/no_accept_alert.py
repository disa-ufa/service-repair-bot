# bot/services/no_accept_alert.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.config import Config
from bot.db import repo


async def no_accept_watchdog(bot: Bot, cfg: Config, interval_seconds: int = 60) -> None:
    """
    Каждые interval_seconds проверяет заявки:
    - status=NEW
    - assigned_master_id IS NULL
    - alerted_no_accept = False
    - created_at <= now - cfg.business.no_accept_alert_minutes
    И отправляет уведомление админам. Затем помечает alerted_no_accept=True.
    """
    try:
        while True:
            cutoff = datetime.utcnow() - timedelta(minutes=int(cfg.business.no_accept_alert_minutes or 50))

            orders = await repo.list_unaccepted_orders_older_than(cutoff_dt=cutoff, limit=50)
            for o in orders:
                tech = o.offer.title if getattr(o, "offer", None) else "—"
                mins = int(cfg.business.no_accept_alert_minutes or 50)

                text = (
                    f"⚠️ <b>Заказ #{o.id}</b> без мастера уже <b>{mins} минут</b>\n"
                    f"Город: <b>{o.city or '—'}</b>\n"
                    f"Техника: <b>{tech}</b>\n\n"
                    f"Решение: назначьте мастера вручную."
                )

                # уведомляем всех tg_id из cfg.admins
                for admin_id in (cfg.admins or []):
                    try:
                        await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass

                # помечаем, чтобы больше не спамить по этой заявке
                await repo.mark_order_no_accept_alerted(o.id)

            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        # корректное завершение при остановке бота
        return
