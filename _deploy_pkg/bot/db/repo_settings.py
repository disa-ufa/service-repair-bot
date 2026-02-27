from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select

from bot.db import get_sessionmaker
from bot.db.models import AppSetting


KEY_PAYOUT_BANK = "payout_bank"
KEY_PAYOUT_PHONE = "payout_phone"


async def get_setting(key: str) -> Optional[str]:
    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(AppSetting, key)
        if not row:
            return None
        return (row.value or "").strip()


async def set_setting(key: str, value: str) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(AppSetting, key)
        if row:
            row.value = value or ""
        else:
            row = AppSetting(key=key, value=value or "")
            s.add(row)
        await s.commit()


async def get_payout_requisites() -> Tuple[str, str]:
    bank = (await get_setting(KEY_PAYOUT_BANK)) or ""
    phone = (await get_setting(KEY_PAYOUT_PHONE)) or ""
    return bank, phone


async def set_payout_requisites(bank: str, phone: str) -> None:
    await set_setting(KEY_PAYOUT_BANK, bank or "")
    await set_setting(KEY_PAYOUT_PHONE, phone or "")


async def get_payout_requisites_text(default_text: str = "") -> str:
    bank, phone = await get_payout_requisites()
    bank = (bank or "").strip()
    phone = (phone or "").strip()

    if bank or phone:
        b = bank if bank else "—"
        p = phone if phone else "—"
        return f"Банк: {b}\nТелефон: {p}"

    return (default_text or "").strip()
