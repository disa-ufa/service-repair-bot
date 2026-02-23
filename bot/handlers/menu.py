from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from bot.db.models import Role

# Берём тексты кнопок из constants, если есть
try:
    from bot.constants import (
        BTN_CREATE_ORDER,
        BTN_ALL_ORDERS,
        BTN_AVAILABLE_ORDERS,
        BTN_MY_ORDERS,
        BTN_MASTERS,
        BTN_DISPATCHERS,  # ✅
        BTN_TECH_CATALOG,  # ✅
        BTN_PAYOUT_REQUISITES,  # ✅
        BTN_STATS,
        BTN_RESET,
        BTN_CASH,
        BTN_PAYOUTS,
        BTN_MASTERS_SUMMARY,
    )
except Exception:  # pragma: no cover
    BTN_CREATE_ORDER = "➕ Создать заявку"
    BTN_ALL_ORDERS = "📚 Все заявки"
    BTN_AVAILABLE_ORDERS = "📋 Доступные заказы"
    BTN_MY_ORDERS = "🧰 Мои заказы"
    BTN_MASTERS = "👥 Мастера"
    BTN_DISPATCHERS = "🧑‍💼 Диспетчеры"
    BTN_TECH_CATALOG = "🛠️ Каталог техники"
    BTN_PAYOUT_REQUISITES = "🏦 Реквизиты для сдачи"
    BTN_STATS = "📊 Статистика"
    BTN_RESET = "🔄 Сбросить"
    BTN_CASH = "💵 Касса"
    BTN_PAYOUTS = "💰 Выплаты"
    BTN_MASTERS_SUMMARY = "👷 Сводка по мастерам"


def _kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        selective=True,
    )


def menu_for_role(role: Role) -> ReplyKeyboardMarkup:
    """
    Главное меню в зависимости от роли.
    """
    if role == Role.SUPER_ADMIN:
        return _kb(
            [
                [BTN_CREATE_ORDER],
                [BTN_ALL_ORDERS],
                [BTN_MASTERS],
                [BTN_TECH_CATALOG],
                [BTN_PAYOUT_REQUISITES],
                [BTN_DISPATCHERS],  # ✅ НОВОЕ
                [BTN_PAYOUTS],
                [BTN_MASTERS_SUMMARY],
                [BTN_STATS],
                [BTN_RESET],
            ]
        )

    if role == Role.DISPATCHER:
        return _kb(
            [
                [BTN_CREATE_ORDER],
                [BTN_ALL_ORDERS],
                [BTN_MASTERS],
                [BTN_PAYOUTS],
                [BTN_MASTERS_SUMMARY],
                [BTN_STATS],
                [BTN_RESET],
            ]
        )

    if role == Role.MASTER:
        return _kb(
            [
                [BTN_AVAILABLE_ORDERS, BTN_MY_ORDERS],
                [BTN_CASH, BTN_STATS],
                [BTN_RESET],
            ]
        )

    # FIRED или неизвестная роль
    return _kb([[BTN_RESET]])


def build_main_menu(role: Role) -> ReplyKeyboardMarkup:
    return menu_for_role(role)
