# bot/constants.py
from __future__ import annotations

from bot.db.models import OrderStatus, OrderType, Role

# -------------------------
# Тексты кнопок (единый источник)
# -------------------------
BTN_AVAILABLE_ORDERS = "📋 Доступные заказы"
BTN_MY_ORDERS = "🧰 Мои заказы"
BTN_STATS = "📊 Статистика"
BTN_RESET = "↩️ Сброс"

BTN_CREATE_ORDER = "➕ Создать заявку"
BTN_ALL_ORDERS = "📚 Все заявки"
BTN_MASTERS = "👥 Мастера"

# ✅ НОВОЕ: управление диспетчерами (только супер-админ)
BTN_DISPATCHERS = "🧑‍💼 Диспетчеры"

# ✅ НОВОЕ: справочник/каталог техники (офферы) — только супер-админ
BTN_TECH_CATALOG = "🛠️ Каталог техники"

# ✅ НОВОЕ: реквизиты для сдачи денег (только супер-админ)
BTN_PAYOUT_REQUISITES = "🏦 Реквизиты для сдачи"

# MASTER: задолженность/касса
BTN_CASH = "💵 Касса"

# REPORTS (admin)
BTN_PAYOUTS = "💰 Выплаты"
BTN_MASTERS_SUMMARY = "👷 Сводка по мастерам"

BTN_BACK = "↩️ Назад"


# -------------------------
# Callback constants
# -------------------------
CB_MASTER_MY_ORDERS = "master_my_orders"

# -------------------------
# Человекочитаемые лейблы
# -------------------------
ROLE_LABEL = {
    Role.SUPER_ADMIN: "Супер-администратор",
    Role.DISPATCHER: "Диспетчер",
    Role.MASTER: "Мастер",
    Role.FIRED: "Уволен",
}

ORDER_TYPE_LABEL = {
    OrderType.NEW: "Новая",
    OrderType.REPEAT: "Повтор",
    OrderType.WARRANTY: "Гарантия",
}

ORDER_STATUS_LABEL = {
    OrderStatus.NEW: "Новый",
    OrderStatus.ACCEPTED: "Принял",
    OrderStatus.IN_WORK: "В работе",
    OrderStatus.MODERNIZATION: "Модернизация (ДР)",
    OrderStatus.CLOSED: "Закрыт",
    OrderStatus.NEEDS_PAYOUT: "Требуется сдача",
    OrderStatus.PAID: "Рассчитан",
}


def role_label(role: Role) -> str:
    return ROLE_LABEL.get(role, role.value)


def order_type_label(t: OrderType) -> str:
    return ORDER_TYPE_LABEL.get(t, t.value)


def order_status_label(s: OrderStatus) -> str:
    return ORDER_STATUS_LABEL.get(s, s.value)
