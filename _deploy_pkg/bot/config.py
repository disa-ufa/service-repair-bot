from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import yaml


@dataclass
class LoggingCfg:
    level: str = "INFO"


@dataclass
class BusinessCfg:
    no_accept_alert_minutes: int = 50
    second_message_text: str = "Нет на месте"
    payout_requisites_text: str = "Банк: —\nТелефон: —"


@dataclass
class Config:
    bot_token: str
    admins: List[int]
    db_url: str
    masters_chat_id: Optional[int] = None

    # IMPORTANT: use default_factory for nested dataclasses (mutable defaults)
    business: BusinessCfg = field(default_factory=BusinessCfg)
    logging: LoggingCfg = field(default_factory=LoggingCfg)


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    business_raw = raw.get("business", {}) or {}
    logging_raw = raw.get("logging", {}) or {}

    cfg = Config(
        bot_token=str(raw.get("bot_token", "")).strip(),
        admins=[int(x) for x in (raw.get("admins") or [])],
        db_url=str(raw.get("db_url", "")).strip(),
        masters_chat_id=(int(raw["masters_chat_id"]) if raw.get("masters_chat_id") not in (None, "", "null") else None),
        business=BusinessCfg(
            no_accept_alert_minutes=int(business_raw.get("no_accept_alert_minutes", 50)),
            second_message_text=str(business_raw.get("second_message_text", "Нет на месте")),
            payout_requisites_text=str(business_raw.get("payout_requisites_text", "Банк: —\nТелефон: —")),
        ),
        logging=LoggingCfg(level=str(logging_raw.get("level", "INFO"))),
    )

    if not cfg.bot_token or "PUT_YOUR_TELEGRAM_BOT_TOKEN" in cfg.bot_token:
        raise ValueError("В config.yml нужно указать bot_token (токен от BotFather).")
    if not cfg.admins:
        raise ValueError("В config.yml нужно указать хотя бы одного администратора (admins: [tg_id]).")
    if not cfg.db_url:
        raise ValueError("В config.yml нужно указать db_url.")

    return cfg
