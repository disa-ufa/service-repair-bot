from __future__ import annotations

from typing import Optional, Tuple
from html import escape as html_escape

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    InputMediaPhoto,
)

from bot.config import Config
from bot.db import repo
from bot.db.models import OrderStatus, OrderType, Role, Offer, Order
from bot.states import CreateOrder, EditOrder

from bot.constants import BTN_ALL_ORDERS

# ------------------------
# Fallbacks for button texts/labels
# ------------------------
try:
    from bot.constants import (
        BTN_CREATE_ORDER,
        order_status_label,
        order_type_label,
    )
except Exception:  # pragma: no cover
    BTN_CREATE_ORDER = "➕ Создать заявку"

    def order_status_label(s: OrderStatus) -> str:
        return getattr(s, "value", str(s))

    def order_type_label(t: OrderType) -> str:
        return getattr(t, "value", str(t))


router = Router()


# ------------------------
# helpers
# ------------------------
def _is_admin_role(role: Role | None) -> bool:
    return role in (Role.SUPER_ADMIN, Role.DISPATCHER)


def _kb_inline(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _refresh_dispatch_for_order_if_needed(
    order_id: int,
    *,
    bot: Bot,
    cfg: Config,
) -> int:
    """Обновить рассылку по заявке, если она ещё NEW и не назначена мастеру.

    Логика:
    - удаляем старые уведомления мастерам (по сохранённым chat_id/message_id)
    - очищаем записи рассылки в БД
    - отправляем уведомления новой группе мастеров (по текущим offer_id/city)
      и сохраняем новые chat_id/message_id

    Возвращает количество успешно отправленных уведомлений.
    """

    try:
        order, _created_by, _assigned = await repo.get_order_with_users(order_id)
    except Exception:
        return 0

    if not order:
        return 0

    # Если заявка уже не в NEW или уже назначена — рассылка не требуется.
    if getattr(order, "status", None) != OrderStatus.NEW or getattr(order, "assigned_master_id", None) is not None:
        return 0

    # 1) удалить старую рассылку (по сохранённым сообщениям)
    try:
        delivered = await repo.list_order_dispatch_messages(order.id)
        for dm in delivered:
            try:
                await bot.delete_message(chat_id=dm.chat_id, message_id=dm.message_id)
            except Exception:
                pass
        await repo.delete_order_dispatch_messages(order_id=order.id)
    except Exception:
        # даже если часть не удалится — всё равно попробуем разослать заново
        try:
            await repo.delete_order_dispatch_messages(order_id=order.id)
        except Exception:
            pass

    # 2) разослать новой группе мастеров
    try:
        masters = await repo.find_masters_for_order(offer_id=order.offer_id, city=order.city)
    except Exception:
        masters = []

    tech_title = order.offer.title if getattr(order, "offer", None) else "—"
    percent_master = int(getattr(order, "percent_master_snapshot", None) or 50)

    public_text = _public_order_text(
        order.id,
        order.order_type,
        order.city,
        tech_title,
        order.address,
        getattr(order, "problem", "") or "",
        percent_master,
    )

    sent = 0
    for m in masters:
        try:
            msg = await bot.send_message(
                m.tg_id,
                public_text,
                reply_markup=_order_accept_kb(order.id),
                parse_mode=ParseMode.HTML,
            )
            if m.id is not None:
                await repo.upsert_order_dispatch_message(
                    order_id=order.id,
                    user_id=m.id,
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                )
            sent += 1
        except Exception:
            continue

    # В чат мастеров при редактировании НЕ дублируем, чтобы не спамить общий канал.
    # (На создании заявки рассылка в общий чат делается отдельно.)
    _ = cfg  # cfg оставлен на будущее (например, если захотите включить пересылку в общий чат)
    return sent


async def _get_user_or_ask_start(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return None
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован. Обратитесь к администратору.")
        return None
    return u


async def _notify_admins(bot: Bot, cfg: Config, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    for admin_tg_id in cfg.admins:
        try:
            await bot.send_message(admin_tg_id, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception:
            continue


def _client_type_kb() -> InlineKeyboardMarkup:
    return _kb_inline(
        [
            [InlineKeyboardButton(text="Новая", callback_data="order_create:type:new")],
            [InlineKeyboardButton(text="Повторная", callback_data="order_create:type:repeat")],
            [InlineKeyboardButton(text="Гарантия", callback_data="order_create:type:warranty")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="order_create:cancel")],
        ]
    )


def _confirm_create_kb() -> InlineKeyboardMarkup:
    return _kb_inline(
        [
            [InlineKeyboardButton(text="✅ Создать", callback_data="order_create:confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="order_create:cancel")],
        ]
    )


def _order_accept_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline(
        [
            [InlineKeyboardButton(text=f"✅ Принять #{order_id}", callback_data=f"order_accept:{order_id}")],
            [InlineKeyboardButton(text="🗑 Убрать", callback_data=f"order_hide:{order_id}")],
        ]
    )


def _order_send_payout_kb(order_id: int) -> InlineKeyboardMarkup:
    return _kb_inline([[InlineKeyboardButton(text="💸 Отправить скрин перевода", callback_data=f"order_payout:{order_id}")]])


def _order_master_actions_kb(order_id: int, status: OrderStatus) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if status == OrderStatus.ACCEPTED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚗 В работе",
                    callback_data=f"order_status:{order_id}:{OrderStatus.IN_WORK.value}",
                )
            ]
        )

    if status in (OrderStatus.IN_WORK, OrderStatus.ACCEPTED):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧰 Модернизация (ДР)",
                    callback_data=f"order_status:{order_id}:{OrderStatus.MODERNIZATION.value}",
                )
            ]
        )

    if status in (OrderStatus.IN_WORK, OrderStatus.MODERNIZATION, OrderStatus.ACCEPTED):
        rows.append([InlineKeyboardButton(text="✅ Закрыть", callback_data=f"order_close:{order_id}")])

    rows.append([InlineKeyboardButton(text="🔄 Обновить карточку", callback_data=f"order_view:{order_id}")])
    return _kb_inline(rows)


def _order_admin_view_kb(
    order_id: int,
    status: OrderStatus,
    expense_cnt: int,
    contract_cnt: int,
    act_cnt: int,
    has_payout: bool,
    assigned_master_id: Optional[int],
    *,
    allow_paid_confirm: bool = True,
) -> Optional[InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []

    if expense_cnt > 0:
        rows.append([InlineKeyboardButton(text=f"🧾 Показать чеки ({expense_cnt})", callback_data=f"order_files:expense:{order_id}")])

    if contract_cnt > 0:
        rows.append([InlineKeyboardButton(text=f"📄 Показать договор ({contract_cnt})", callback_data=f"order_files:contract:{order_id}")])

    if act_cnt > 0:
        rows.append([InlineKeyboardButton(text=f"📝 Показать акт ({act_cnt})", callback_data=f"order_files:act:{order_id}")])

    if has_payout:
        rows.append([InlineKeyboardButton(text="💸 Показать скрин сдачи", callback_data=f"order_payout_file:{order_id}")])

    if status == OrderStatus.NEEDS_PAYOUT and allow_paid_confirm and has_payout:
        rows.append([InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"order_admin_paid:{order_id}")])

    if status == OrderStatus.NEW and not assigned_master_id:
        rows.append([InlineKeyboardButton(text="👷 Назначить мастера", callback_data=f"order_assign:{order_id}")])

    if status == OrderStatus.ACCEPTED and assigned_master_id:
        rows.append([InlineKeyboardButton(text="↩️ Снять мастера", callback_data=f"order_unassign:{order_id}")])

    if status != OrderStatus.PAID:
        rows.append([InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"oe_open:{order_id}")])

    return _kb_inline(rows) if rows else None


# ---------------------------
# Admin edit order
# ---------------------------
def _order_edit_fields_kb(order_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✏️ Клиент", callback_data=f"oe_field:{order_id}:client_name"),
            InlineKeyboardButton(text="📞 Телефон", callback_data=f"oe_field:{order_id}:client_phone"),
        ],
        [
            InlineKeyboardButton(text="🏙 Город", callback_data=f"oe_field:{order_id}:city"),
            InlineKeyboardButton(text="📍 Адрес", callback_data=f"oe_field:{order_id}:address"),
        ],
        [
            InlineKeyboardButton(text="🧰 Техника", callback_data=f"oe_field:{order_id}:device"),
            InlineKeyboardButton(text="📊 % мастера", callback_data=f"oe_field:{order_id}:percent"),
        ],
        [
            InlineKeyboardButton(text="📝 Проблема", callback_data=f"oe_field:{order_id}:problem"),
            InlineKeyboardButton(text="⏱ Когда", callback_data=f"oe_field:{order_id}:when"),
        ],
        [
            InlineKeyboardButton(text="📌 Источник", callback_data=f"oe_field:{order_id}:source"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"oe_cancel:{order_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)



def _create_order_device_kb(offers: list[Offer]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for off in offers:
        rows.append([InlineKeyboardButton(text=off.title, callback_data=f"co_device:{off.id}")])

    rows.append([InlineKeyboardButton(text="✍️ Другое…", callback_data="co_device_other")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="co_device_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def _create_order_city_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🏙 Екатеринбург", callback_data="order_create:city:Екатеринбург")],
        [InlineKeyboardButton(text="✍️ Другой город", callback_data="order_create:city:manual")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="order_create:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _order_edit_percent_kb(order_id: int, current: int | None = None) -> InlineKeyboardMarkup:
    opts = [40, 50, 60]
    rows = [
        [
            InlineKeyboardButton(
                text=(f"✅ {p}%" if current == p else f"{p}%"),
                callback_data=f"oe_percent:{order_id}:{p}",
            )
            for p in opts
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"oe_back:{order_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"oe_cancel:{order_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _order_edit_offers_kb(order_id: int, offers: list[Offer]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for off in offers:
        rows.append([InlineKeyboardButton(text=off.title, callback_data=f"oe_offer:{order_id}:{off.id}")])

    rows.append([InlineKeyboardButton(text="Другое…", callback_data=f"oe_offer_other:{order_id}")])
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"oe_back:{order_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"oe_cancel:{order_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_clear_text(s: str) -> bool:
    t = (s or "").strip().lower()
    return t in {"-", "—", "нет", "не", "пусто", "очистить", "удалить", "пропустить", "skip"}


def _pack_problem(problem: str, when: str) -> str:
    problem = (problem or "").strip()
    when = (when or "").strip()
    if not when:
        return problem
    return f"{problem}\n\nКогда удобно: {when}"


def _split_problem(problem_full: str) -> Tuple[str, Optional[str]]:
    s = (problem_full or "").strip()
    marker = "Когда удобно:"
    if marker not in s:
        return s, None
    left, right = s.split(marker, 1)
    p = left.strip()
    w = right.strip()
    return p, (w if w else None)


def _public_order_text(
    order_id: int,
    order_type: OrderType,
    city: Optional[str],
    tech_title: str,
    address: Optional[str],
    problem_full: str,
    percent_master: int,
) -> str:
    # Для мастеров ДО принятия: показываем город + улицу/дом, но НЕ показываем телефон и квартиру
    lines = [
        f"📌 <b>Новая заявка #{order_id}</b>",
        f"Тип: <b>{order_type_label(order_type)}</b>",
        f"Город: <b>{html_escape(city or '—')}</b>",
        f"Адрес: <b>{html_escape(address or '—')}</b>",
        f"Техника: <b>{html_escape(tech_title or '—')}</b>",
        f"Доля мастера: <b>{percent_master}% (от чистых)</b>",
        "",
        f"Проблема: {html_escape(problem_full or '—')}",
        "",
        "☎️ Телефон и квартира будут доступны после принятия заявки.",
    ]
    return "\n".join(lines)

def _private_order_text(order) -> str:
    tech = order.offer.title if getattr(order, "offer", None) else "—"
    p, w = _split_problem(getattr(order, "problem", "") or "")

    txt = (
        f"<b>Заявка #{order.id}</b>\n"
        f"Статус: <b>{order_status_label(order.status)}</b>\n"
        f"Тип: <b>{order_type_label(order.order_type)}</b>\n\n"
        f"Клиент: <b>{html_escape(getattr(order, 'client_name', None) or '—')}</b>\n"
        f"Телефон: <b>{html_escape(getattr(order, 'client_phone', None) or '—')}</b>\n"
        f"Город: <b>{html_escape(getattr(order, 'city', None) or '—')}</b>\n"
        f"Адрес: <b>{html_escape((getattr(order, 'address', None) or '—') + (', кв. ' + str(getattr(order, 'apartment', None)) if getattr(order, 'apartment', None) else ''))}</b>\n"
        f"Источник: <b>{html_escape(getattr(order, 'source', None) or '—')}</b>\n"
        f"Техника: <b>{html_escape(tech)}</b>\n"
        f"Проблема: {html_escape(p)}\n"
        f"Когда: {html_escape(w or '—')}"
    )

    percent = getattr(order, "percent_master_snapshot", None)
    if percent:
        txt += f"\nДоля мастера: <b>{percent}%</b> (от чистых)"

    wd = getattr(order, "warranty_days", None)
    if wd is not None:
        txt += f"\nГарантия: <b>{wd} дн.</b>"

    cc = (getattr(order, "close_comment", None) or "").strip()
    if cc:
        txt += f"\nКомментарий мастера: {html_escape(cc)}"

    dr_comment = getattr(order, "modernization_comment", None)
    if dr_comment:
        txt += f"\n\n🧰 <b>Комментарий ДР:</b> {html_escape(dr_comment)}"

    return txt


# ------------------------
# ADMIN: редактирование заявки (Inline: oe_*)
# ------------------------

async def _admin_render_order_card(order_id: int, admin_role: Role) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собрать текст карточки заявки и клавиатуру для админа."""
    order, _created_by, assigned_master = await repo.get_order_with_users(order_id)
    if not order:
        return "Заявка не найдена.", None

    text = _private_order_text(order)

    if assigned_master:
        text += f"\n\nМастер: <b>{html_escape(assigned_master.fio)}</b>"

    expense_cnt = int(await repo.count_order_files(order.id, "expense") or 0)
    contract_cnt = int(await repo.count_order_files(order.id, "contract") or 0)
    act_cnt = int(await repo.count_order_files(order.id, "act") or 0)
    has_payout = bool(getattr(order, "payout_screenshot_file_id", None))

    text += f"\n\n🧾 Чеки: <b>{expense_cnt}</b>\n📄 Договор: <b>{contract_cnt}</b>\n📝 Акт: <b>{act_cnt}</b>"
    if has_payout:
        text += "\n💸 Скрин сдачи: <b>есть</b>"

    if getattr(order, "total_amount", None) is not None:
        text += (
            f"\n\nИтого: <b>{order.total_amount}</b>"
            f"\nРасходы: <b>{order.expense_amount or 0}</b>"
            f"\nЧистыми: <b>{order.net_amount or 0}</b>"
            f"\nК сдаче: <b>{order.company_amount or 0}</b>"
        )

    kb = _order_admin_view_kb(
        order.id,
        order.status,
        expense_cnt,
        contract_cnt,
        act_cnt,
        has_payout,
        order.assigned_master_id,
        allow_paid_confirm=(admin_role in (Role.SUPER_ADMIN, Role.DISPATCHER)),
    )
    return text, kb


@router.callback_query(F.data.startswith("oe_open:"))
async def admin_edit_open(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.message.answer("Вы не зарегистрированы. Нажмите /start")
        await cb.answer()
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return

    order = await repo.get_order(order_id)
    if not order:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if order.status == OrderStatus.PAID:
        await cb.answer("Нельзя редактировать оплаченные заявки", show_alert=True)
        return

    await state.clear()
    await state.update_data(order_id=order_id)

    await cb.message.answer(
        f"✏️ <b>Редактирование заявки #{order_id}</b>\nВыберите поле:",
        reply_markup=_order_edit_fields_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("oe_back:"))
async def admin_edit_back(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return

    await state.update_data(order_id=order_id)
    await cb.message.answer(
        f"✏️ <b>Редактирование заявки #{order_id}</b>\nВыберите поле:",
        reply_markup=_order_edit_fields_kb(order_id),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("oe_cancel:"))
async def admin_edit_cancel(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    await state.clear()
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith("oe_field:"))
async def admin_edit_choose_field(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.message.answer("Вы не зарегистрированы. Нажмите /start")
        await cb.answer()
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        _p = cb.data.split(":")
        order_id = int(_p[1])
        field = _p[2]
    except Exception:
        await cb.answer()
        return

    order = await repo.get_order(order_id)
    if not order:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if order.status == OrderStatus.PAID:
        await cb.answer("Нельзя редактировать оплаченные заявки", show_alert=True)
        return

    await state.clear()
    await state.update_data(order_id=order_id, field=field)

    # percent -> отдельные кнопки
    if field == "percent":
        current = getattr(order, "percent_master_snapshot", None)
        await cb.message.answer(
            f"📊 Выберите долю мастера для заявки <b>#{order_id}</b>:",
            reply_markup=_order_edit_percent_kb(order_id, current=current),
            parse_mode=ParseMode.HTML,
        )
        await cb.answer("Ок")
        return

    # device -> список офферов
    if field == "device":
        offers = await repo.list_offers()
        await cb.message.answer(
            f"🧰 Выберите технику для заявки <b>#{order_id}</b>:",
            reply_markup=_order_edit_offers_kb(order_id, offers),
            parse_mode=ParseMode.HTML,
        )
        await cb.answer("Ок")
        return

    # остальные поля -> ждём текст
    if field == "client_name":
        prompt = "Введите новое <b>имя клиента</b> (ФИО):"
    elif field == "client_phone":
        prompt = "Введите новый <b>телефон</b>:"
    elif field == "city":
        prompt = "Введите новый <b>город</b> или напишите <b>очистить</b>:"
    elif field == "address":
        prompt = "Введите новый <b>адрес</b>:"
    elif field == "source":
        prompt = "Введите новый <b>источник</b> или напишите <b>очистить</b>:"
    elif field == "problem":
        prompt = "Введите новый текст <b>проблемы</b>:"
    elif field == "when":
        prompt = "Введите новый текст поля <b>Когда удобно</b> или напишите <b>очистить</b>:"
    else:
        await cb.answer("Неизвестное поле", show_alert=True)
        return

    await cb.message.answer(prompt, parse_mode=ParseMode.HTML)
    await state.set_state(EditOrder.value)
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("oe_percent:"))
async def admin_edit_set_percent(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        _p = cb.data.split(":")
        order_id = int(_p[1])
        percent = int(_p[2])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    if percent not in (40, 50, 60):
        await cb.answer("Неверный процент", show_alert=True)
        return

    order = await repo.admin_update_order_fields(order_id, percent_master_snapshot=percent)
    if not order:
        await cb.answer("Не удалось сохранить (проверьте статус)", show_alert=True)
        return

    await state.clear()
    await cb.message.answer(
        f"✅ Доля мастера по заявке <b>#{order_id}</b> обновлена: <b>{percent}%</b>",
        parse_mode=ParseMode.HTML,
    )

    # Покажем актуальную карточку (меню редактирования не открываем автоматически)
    try:
        text, kb = await _admin_render_order_card(order_id, u.role)
        await cb.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("oe_offer:"))
async def admin_edit_set_offer(cb: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        _p = cb.data.split(":")
        order_id = int(_p[1])
        offer_id = int(_p[2])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    # Сравним со старым значением, чтобы не делать лишних пересылок.
    old = await repo.get_order(order_id)
    old_offer_id = getattr(old, "offer_id", None) if old else None

    order = await repo.admin_update_order_fields(order_id, offer_id=offer_id)
    if not order:
        await cb.answer("Не удалось сохранить (проверьте статус)", show_alert=True)
        return

    await state.clear()
    await cb.message.answer(
        f"✅ Техника по заявке <b>#{order_id}</b> обновлена.",
        parse_mode=ParseMode.HTML,
    )

    # Если заявка ещё NEW и не принята — удаляем старую рассылку и пересылаем новой группе мастеров.
    try:
        if old_offer_id is None or int(old_offer_id) != int(offer_id):
            sent = await _refresh_dispatch_for_order_if_needed(order_id, bot=bot, cfg=cfg)
            if sent:
                await cb.message.answer(f"📨 Рассылка обновлена: отправлено <b>{sent}</b> мастерам.", parse_mode=ParseMode.HTML)
            else:
                await cb.message.answer("📨 Рассылка обновлена.", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    # Покажем актуальную карточку (меню редактирования не открываем автоматически)
    try:
        text, kb = await _admin_render_order_card(order_id, u.role)
        await cb.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("oe_offer_other:"))
async def admin_edit_offer_other(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    order = await repo.get_order(order_id)
    if not order:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if order.status == OrderStatus.PAID:
        await cb.answer("Нельзя редактировать оплаченные заявки", show_alert=True)
        return

    await state.clear()
    await state.update_data(order_id=order_id, field="device_other")
    await cb.message.answer(
        "Введите название техники (как вам удобно).\n"
        "Например: <i>Стиральная машина</i> / <i>Холодильник</i> / <i>Посудомойка</i>",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(EditOrder.value)
    await cb.answer("Ок")


@router.message(EditOrder.value, F.text, ~F.text.startswith("/"))
async def admin_edit_value(message: Message, state: FSMContext, bot: Bot, cfg: Config):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await state.clear()
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return
    if u.role == Role.FIRED:
        await state.clear()
        await message.answer("Ваш доступ к боту заблокирован.")
        return
    if not _is_admin_role(u.role):
        await state.clear()
        await message.answer("Недостаточно прав.")
        return

    txt = (message.text or "").strip()
    if txt.lower() in {"отмена", "cancel", "стоп"}:
        await state.clear()
        await message.answer("Ок, отменил.")
        return

    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)
    field = str(data.get("field") or "")

    if not order_id or not field:
        await state.clear()
        await message.answer("Ошибка состояния. Начните заново: откройте заявку и нажмите 'Редактировать'.")
        return

    order = await repo.get_order(order_id)
    if not order:
        await state.clear()
        await message.answer("Заявка не найдена.")
        return
    if order.status == OrderStatus.PAID:
        await state.clear()
        await message.answer("Нельзя редактировать оплаченные заявки.")
        return

    # Запомним старые значения для решения: нужно ли пересылать заявку мастерам.
    old_offer_id = getattr(order, "offer_id", None)
    old_city = (getattr(order, "city", None) or None)

    updates = {}

    if field == "client_name":
        updates["client_name"] = txt

    elif field == "client_phone":
        updates["client_phone"] = txt

    elif field == "city":
        updates["city"] = None if _is_clear_text(txt) else txt

    elif field == "address":
        updates["address"] = txt

    elif field == "source":
        updates["source"] = None if _is_clear_text(txt) else txt

    elif field == "problem":
        p_old, w_old = _split_problem(getattr(order, "problem", "") or "")
        updates["problem"] = _pack_problem(txt, w_old or "")

    elif field == "when":
        p_old, w_old = _split_problem(getattr(order, "problem", "") or "")
        new_when = "" if _is_clear_text(txt) else txt
        updates["problem"] = _pack_problem(p_old, new_when)

    elif field == "device_other":
        off = await repo.get_or_create_offer_by_title(txt)
        updates["offer_id"] = off.id

    else:
        await state.clear()
        await message.answer("Неизвестное поле.")
        return

    upd = await repo.admin_update_order_fields(order_id, **updates)
    if not upd:
        await state.clear()
        await message.answer("Не удалось сохранить (проверьте введённые данные и статус заявки).")
        return

    await state.clear()

    await message.answer(f"✅ Сохранено для заявки <b>#{order_id}</b>.", parse_mode=ParseMode.HTML)

    # Если меняли город или технику (offer) для NEW-заявки — обновим рассылку.
    try:
        new_offer_id = updates.get("offer_id", old_offer_id)
        new_city = updates.get("city", old_city)
        if (new_offer_id != old_offer_id) or (new_city != old_city):
            sent = await _refresh_dispatch_for_order_if_needed(order_id, bot=bot, cfg=cfg)
            if sent:
                await message.answer(f"📨 Рассылка обновлена: отправлено <b>{sent}</b> мастерам.", parse_mode=ParseMode.HTML)
            else:
                await message.answer("📨 Рассылка обновлена.", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    # Покажем актуальную карточку
    try:
        text, kb = await _admin_render_order_card(order_id, u.role)
        await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ------------------------
# ADMIN: создание заявки
# ------------------------
@router.message(F.text == BTN_CREATE_ORDER)
async def admin_create_order_start(message: Message, state: FSMContext):
    u = await _get_user_or_ask_start(message)
    if not u:
        return
    if not _is_admin_role(u.role):
        await message.answer("Недостаточно прав для создания заявки.")
        return

    await state.clear()
    await state.update_data(
        order_type=None,
        client_name=None,
        phone=None,
        city=None,
        address=None,
        device=None,
        offer_id=None,
        problem=None,
        when=None,
        source=None,
        percent_master=None,
    )

    await message.answer("Выберите тип заявки:", reply_markup=_client_type_kb())


@router.callback_query(F.data.startswith("order_create:"))
async def admin_create_order_callbacks(cb: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.message.answer("Вы не зарегистрированы. Нажмите /start")
        await cb.answer()
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return
    if not _is_admin_role(u.role):
        await cb.answer("Недостаточно прав", show_alert=True)
        return

    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        await state.clear()
        await cb.message.answer("Ок, отменил.")
        await cb.answer()
        return

    if action == "type":
        t = parts[2] if len(parts) > 2 else "new"
        await state.update_data(order_type=t)
        await cb.message.answer("Введите имя клиента (ФИО):")
        await state.set_state(CreateOrder.client_name)
        await cb.answer()
        return

    if action == "city":
        city = parts[2] if len(parts) > 2 else ""
        if not city:
            await cb.answer("Город не выбран", show_alert=True)
            return
        if city == "manual":
            await cb.message.answer("Введите город:")
            await state.set_state(CreateOrder.city)
            await cb.answer()
            return

        await state.update_data(city=city)
        await cb.message.answer("Введите адрес (улица, дом):")
        await state.set_state(CreateOrder.address)
        await cb.answer()
        return


    if action == "confirm":
        data = await state.get_data()
        missing = [
            k
            for k in ("order_type", "client_name", "phone", "city", "address", "device", "problem", "when", "percent_master")
            if not data.get(k)
        ]
        if missing:
            await cb.answer("Не все поля заполнены", show_alert=True)
            return

        offer_id = data.get("offer_id")
        if offer_id is None:
            off = await repo.get_or_create_offer_by_title(str(data["device"]))
            offer_id = off.id

        problem_full = _pack_problem(str(data["problem"]), str(data["when"]))

        order = await repo.create_order(
            order_type=OrderType(str(data["order_type"])),
            client_name=str(data["client_name"]),
            client_phone=str(data["phone"]),
            city=str(data["city"]),
            address=str(data["address"]),
            apartment=(data.get("apartment") or None),
            offer_id=int(offer_id) if offer_id is not None else None,
            problem=problem_full,
            created_by_user_id=u.id,
            percent_master=int(data.get("percent_master")),
            source=(data.get("source") or None),
        )

        await state.clear()
        await cb.message.answer(f"✅ Заявка создана. Номер: <b>#{order.id}</b>", parse_mode=ParseMode.HTML)

        masters = await repo.find_masters_for_order(offer_id=order.offer_id, city=order.city)
        tech_title = str(data.get("device") or "—")
        public_text = _public_order_text(
            order.id,
            order.order_type,
            order.city,
            tech_title,
            order.address,
            problem_full,
            percent_master=int(data.get("percent_master")),
        )

        for m in masters:
            try:
                msg = await bot.send_message(
                    m.tg_id,
                    public_text,
                    reply_markup=_order_accept_kb(order.id),
                    parse_mode=ParseMode.HTML,
                )
                # Сохраняем chat_id/message_id уведомления мастеру — пригодится для авто-удаления
                # у остальных мастеров при принятии заявки, и для кнопки «🗑 Убрать».
                if m.id is not None:
                    await repo.upsert_order_dispatch_message(
                        order_id=order.id,
                        user_id=m.id,
                        chat_id=msg.chat.id,
                        message_id=msg.message_id,
                    )
            except Exception:
                continue

        if getattr(cfg, "masters_chat_id", None):
            try:
                await bot.send_message(cfg.masters_chat_id, public_text, reply_markup=_order_accept_kb(order.id), parse_mode=ParseMode.HTML)
            except Exception:
                pass

        await cb.answer("Готово")
        return

    await cb.answer()


@router.message(CreateOrder.client_name, F.text, ~F.text.startswith("/"))
async def admin_create_order_client_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text.strip())
    await message.answer("Введите телефон клиента:")
    await state.set_state(CreateOrder.phone)


@router.message(CreateOrder.phone, F.text, ~F.text.startswith("/"))
async def admin_create_order_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await message.answer("Выберите город:", reply_markup=_create_order_city_kb())
    await state.set_state(CreateOrder.city)


@router.message(CreateOrder.city, F.text, ~F.text.startswith("/"))
async def admin_create_order_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await message.answer("Введите адрес:")
    await state.set_state(CreateOrder.address)


@router.message(CreateOrder.address, F.text, ~F.text.startswith("/"))
async def admin_create_order_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text.strip())
    await state.set_state(CreateOrder.apartment)
    await message.answer("Введите квартиру (номер). Если нет — напишите «-» или «пропустить»:")


@router.message(CreateOrder.apartment, F.text, ~F.text.startswith("/"))
async def admin_create_order_apartment(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.lower() in {"-", "—", "нет", "пропустить", "skip", "0", "00"}:
        apartment = None
    else:
        apartment = raw or None

    await state.update_data(apartment=apartment)

    offers = await repo.list_offers(include_inactive=False)
    await state.set_state(CreateOrder.device)
    await message.answer(
        "✅ Техника: выберите из списка или введите вручную:",
        reply_markup=_create_order_device_kb(offers),
    )
@router.callback_query(CreateOrder.device, F.data == "co_device_cancel")
async def admin_create_order_device_cancel(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return
    await state.clear()
    try:
        await cb.message.edit_text("Создание заявки отменено.")
    except Exception:
        await cb.message.answer("Создание заявки отменено.")
    await cb.answer("Ок")


@router.callback_query(CreateOrder.device, F.data == "co_device_other")
async def admin_create_order_device_other(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return

    await state.update_data(offer_id=None, device=None, device_other=True)
    await cb.message.answer("Введите название техники текстом:")
    await cb.answer("Ок")


@router.callback_query(CreateOrder.device, F.data.startswith("co_device:"))
async def admin_create_order_device_pick(cb: CallbackQuery, state: FSMContext):
    if not cb.message:
        return
    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role) or u.role == Role.FIRED:
        await cb.answer("Только для админов", show_alert=True)
        return

    try:
        offer_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Ошибка offer_id", show_alert=True)
        return

    if hasattr(repo, "list_active_offers"):
        offers = await repo.list_active_offers()
    else:
        try:
            offers = await repo.list_offers(include_inactive=False)
        except TypeError:
            offers = await repo.list_offers()
    offers = [o for o in offers if getattr(o, "is_active", True)]
    off = next((o for o in offers if o.id == offer_id), None)
    if not off:
        await cb.answer("Техника не найдена", show_alert=True)
        return

    await state.update_data(device=off.title, offer_id=off.id, device_other=False)

    # Убираем клавиатуру выбора и идем дальше
    try:
        await cb.message.edit_text(f"✅ Техника выбрана: <b>{html_escape(off.title)}</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    await cb.message.answer("Опишите проблему:")
    await state.set_state(CreateOrder.problem)
    await cb.answer("Ок")

@router.message(CreateOrder.device, F.text, ~F.text.startswith("/"))
async def admin_create_order_device(message: Message, state: FSMContext):
    data = await state.get_data()
    device = message.text.strip()

    # если выбрали "Другое…" — это ожидаемый ручной ввод
    if data.get("device_other"):
        await state.update_data(device=device, offer_id=None, device_other=False)
        await message.answer("Опишите проблему:")
        await state.set_state(CreateOrder.problem)
        return

    # совместимость: если все же ввели текстом (без кнопок) — пробуем сопоставить с каталогом
    await state.update_data(device=device)

    if hasattr(repo, "list_active_offers"):
        offers = await repo.list_active_offers()
    else:
        try:
            offers = await repo.list_offers(include_inactive=False)
        except TypeError:
            offers = await repo.list_offers()
    offers = [o for o in offers if getattr(o, "is_active", True)]

    offer_id = None
    for o in offers:
        if (o.title or "").strip().casefold() == device.casefold():
            offer_id = o.id
            break

    await state.update_data(offer_id=offer_id)

    await message.answer("Опишите проблему:")
    await state.set_state(CreateOrder.problem)


@router.message(CreateOrder.problem, F.text, ~F.text.startswith("/"))
async def admin_create_order_problem(message: Message, state: FSMContext):
    await state.update_data(problem=message.text.strip())
    await message.answer("Когда удобно (дата/время текстом):")
    await state.set_state(CreateOrder.when)


@router.message(CreateOrder.when, F.text, ~F.text.startswith("/"))
async def admin_create_order_when(message: Message, state: FSMContext):
    await state.update_data(when=message.text.strip())
    await message.answer("Откуда поступила заявка? (например: сайт / звонок / рекомендация).\nМожно написать \"пропустить\".")
    await state.set_state(CreateOrder.source)


@router.message(CreateOrder.source, F.text, ~F.text.startswith("/"))
async def admin_create_order_source(message: Message, state: FSMContext):
    src = (message.text or "").strip()
    if src.lower() in {"пропустить", "skip", "нет", "-", "—"}:
        src = ""

    await state.update_data(source=(src or None))

    await message.answer(
        "Укажите <b>долю мастера</b> (40 / 50 / 60) — это процент от <b>чистых</b>:\n"
        "Напишите число:",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CreateOrder.percent_master)


@router.message(CreateOrder.percent_master, F.text, ~F.text.startswith("/"), F.text.regexp(r"^(40|50|60)$"))
async def admin_create_order_percent(message: Message, state: FSMContext):
    await state.update_data(percent_master=int(message.text.strip()))

    data = await state.get_data()
    try:
        t = OrderType(str(data.get("order_type") or "new"))
        type_h = order_type_label(t)
    except Exception:
        type_h = str(data.get("order_type") or "—")

    text = (
        "Проверьте заявку:\n\n"
        f"Тип: <b>{type_h}</b>\n"
        f"Клиент: <b>{html_escape(str(data.get('client_name') or '—'))}</b>\n"
        f"Телефон: <b>{html_escape(str(data.get('phone') or '—'))}</b>\n"
        f"Город: <b>{html_escape(str(data.get('city') or '—'))}</b>\n"
        f"Адрес: <b>{html_escape(str(data.get('address') or '—'))}</b>\n"
        f"Квартира: <b>{html_escape(str(data.get('apartment') or '—'))}</b>\n"
        f"Техника: <b>{html_escape(str(data.get('device') or '—'))}</b>\n"
        f"Проблема: {html_escape(str(data.get('problem') or '—'))}\n"
        f"Когда: {html_escape(str(data.get('when') or '—'))}\n"
        f"Источник: <b>{html_escape(str(data.get('source') or '—'))}</b>\n"
        f"Доля мастера: <b>{data.get('percent_master')}%</b> (от чистых)\n\n"
        "Создать?"
    )
    await message.answer(text, reply_markup=_confirm_create_kb(), parse_mode=ParseMode.HTML)


@router.message(CreateOrder.percent_master, F.text, ~F.text.startswith("/"))
async def admin_create_order_percent_invalid(message: Message):
    await message.answer("Нужно отправить одно из значений: 40, 50 или 60.")





# ------------------------
# Общие: открыть заявку (и для админа, и для мастера)
# ------------------------
@router.callback_query(F.data.startswith("order_view:"))
async def order_view(cb: CallbackQuery):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.message.answer("Вы не зарегистрированы. Нажмите /start")
        await cb.answer()
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return

    order, _created_by, assigned_master = await repo.get_order_with_users(order_id)
    if not order:
        await cb.answer("Не найдено", show_alert=True)
        return

    if u.role == Role.MASTER and order.assigned_master_id != u.id:
        await cb.answer("Недостаточно прав для просмотра", show_alert=True)
        return

    text = _private_order_text(order)

    if assigned_master:
        text += f"\n\nМастер: <b>{html_escape(assigned_master.fio)}</b>"

    expense_cnt = 0
    contract_cnt = 0
    act_cnt = 0
    has_payout = False

    if _is_admin_role(u.role):
        expense_cnt = int(await repo.count_order_files(order.id, "expense") or 0)
        contract_cnt = int(await repo.count_order_files(order.id, "contract") or 0)
        act_cnt = int(await repo.count_order_files(order.id, "act") or 0)
        has_payout = bool(getattr(order, "payout_screenshot_file_id", None))

        text += f"\n\n🧾 Чеки: <b>{expense_cnt}</b>\n📄 Договор: <b>{contract_cnt}</b>\n📝 Акт: <b>{act_cnt}</b>"
        if has_payout:
            text += "\n💸 Скрин сдачи: <b>есть</b>"

    if order.total_amount is not None:
        text += (
            f"\n\nИтого: <b>{order.total_amount}</b>"
            f"\nРасходы: <b>{order.expense_amount or 0}</b>"
            f"\nЧистыми: <b>{order.net_amount or 0}</b>"
            f"\nК сдаче: <b>{order.company_amount or 0}</b>"
        )

    reply_markup = None
    if u.role == Role.MASTER and order.assigned_master_id == u.id:
        if order.status == OrderStatus.NEEDS_PAYOUT:
            reply_markup = _order_send_payout_kb(order.id)
        else:
            reply_markup = _order_master_actions_kb(order.id, order.status)
    elif _is_admin_role(u.role):
        reply_markup = _order_admin_view_kb(
            order.id,
            order.status,
            expense_cnt,
            contract_cnt,
            act_cnt,
            has_payout,
            order.assigned_master_id,
            allow_paid_confirm=(u.role in (Role.SUPER_ADMIN, Role.DISPATCHER)),
        )

    await cb.message.answer(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data.startswith("order_files:"))
async def admin_show_order_files(cb: CallbackQuery, bot: Bot):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    try:
        _p = cb.data.split(":")
        kind = _p[1]
        order_id = int(_p[2])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    files = await repo.list_order_files(order_id, kind)
    if not files:
        await cb.answer("Файлов нет", show_alert=True)
        return

    if kind == "expense":
        title = "🧾 Чеки"
    elif kind == "contract":
        title = "📄 Договор"
    else:
        title = "📝 Акт"
    await cb.message.answer(f"📎 <b>{title}</b> по заявке <b>#{order_id}</b>:", parse_mode=ParseMode.HTML)

    file_ids: list[str] = []
    for f in files:
        if isinstance(f, str):
            file_ids.append(f)
        else:
            fid = getattr(f, "file_id", None)
            if fid:
                file_ids.append(str(fid))

    for i in range(0, len(file_ids), 10):
        chunk = file_ids[i : i + 10]
        media: list[InputMediaPhoto] = []
        for j, fid in enumerate(chunk):
            if i == 0 and j == 0:
                media.append(InputMediaPhoto(media=fid, caption=f"{title} | заявка #{order_id}"))
            else:
                media.append(InputMediaPhoto(media=fid))
        try:
            await bot.send_media_group(chat_id=cb.from_user.id, media=media)
        except Exception:
            for fid in chunk:
                try:
                    await bot.send_photo(chat_id=cb.from_user.id, photo=fid)
                except Exception:
                    pass

    await cb.answer("Отправил")


@router.callback_query(F.data.startswith("order_payout_file:"))
async def admin_show_payout_file(cb: CallbackQuery, bot: Bot):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    order = await repo.get_order(order_id)
    if not order or not getattr(order, "payout_screenshot_file_id", None):
        await cb.answer("Скрина нет", show_alert=True)
        return

    try:
        await bot.send_photo(
            chat_id=cb.from_user.id,
            photo=order.payout_screenshot_file_id,
            caption=f"💸 Скрин сдачи | заявка #{order_id}",
        )
    except Exception:
        await cb.message.answer("Не удалось отправить скрин (возможно file_id устарел).")

    await cb.answer("Отправил")


# ------------------------
# ADMIN: назначить/снять мастера (вручную)
# ------------------------
@router.callback_query(F.data.startswith("order_assign:"))
async def admin_assign_master_choose(cb: CallbackQuery):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    order = await repo.get_order(order_id)
    if not order:
        await cb.answer("Заявка не найдена", show_alert=True)
        return

    if order.status != OrderStatus.NEW or order.assigned_master_id is not None:
        await cb.answer("Назначение доступно только для NEW без мастера", show_alert=True)
        return

    masters = await repo.find_masters_for_order(order.offer_id, order.city)
    if not masters:
        masters = await repo.list_masters()

    if not masters:
        await cb.answer("Мастеров нет", show_alert=True)
        return

    kb_rows: list[list[InlineKeyboardButton]] = []
    for m in masters[:30]:
        title = (m.fio or "Мастер").strip()
        if m.city:
            title += f" ({m.city})"
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=title[:40],
                    callback_data=f"order_assign_to:{order_id}:{m.id}",
                )
            ]
        )

    kb_rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"order_assign_cancel:{order_id}")])

    await cb.message.answer(
        f"👷 <b>Назначение мастера</b> для заявки <b>#{order_id}</b>\n"
        f"Выберите мастера из списка:",
        reply_markup=_kb_inline(kb_rows),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("order_assign_cancel:"))
async def admin_assign_master_cancel(cb: CallbackQuery):
    await cb.answer("Ок")


@router.callback_query(F.data.startswith("order_assign_to:"))
async def admin_assign_master_apply(cb: CallbackQuery, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    try:
        _p = cb.data.split(":")
        order_id = int(_p[1])
        master_id = int(_p[2])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    order = await repo.assign_order_to_master(order_id, master_id)
    if not order:
        await cb.answer("Не удалось назначить (проверьте статус)", show_alert=True)
        return

    # При принудительном назначении — удаляем старую рассылку NEW-заявки у всех мастеров
    # (чтобы никто больше не пытался принять старое сообщение).
    try:
        delivered = await repo.list_order_dispatch_messages(order.id)
        for dm in delivered:
            try:
                await bot.delete_message(chat_id=dm.chat_id, message_id=dm.message_id)
            except Exception:
                pass
        await repo.delete_order_dispatch_messages(order_id=order.id)
    except Exception:
        pass

    full_order, _created_by, assigned_master = await repo.get_order_with_users(order.id)
    if assigned_master:
        try:
            await bot.send_message(
                assigned_master.tg_id,
                "🧾 <b>Вас назначили на заявку</b>\n\n" + _private_order_text(full_order),
                reply_markup=_order_master_actions_kb(order.id, order.status),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    await cb.message.answer(
        f"✅ Назначил мастера на заявку <b>#{order.id}</b>. Статус: <b>{order_status_label(order.status)}</b>",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Ок")

    await _notify_admins(
        bot,
        cfg,
        f"🧑‍💼 Админ назначил мастера на заявку <b>#{order.id}</b>. Статус: <b>{order_status_label(order.status)}</b>",
    )


@router.callback_query(F.data.startswith("order_unassign:"))
async def admin_unassign_master(cb: CallbackQuery, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or not _is_admin_role(u.role):
        await cb.answer("Только для админов", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    order_before, _created_by, assigned_master = await repo.get_order_with_users(order_id)
    order = await repo.unassign_order(order_id)
    if not order:
        await cb.answer("Не удалось снять (можно только в статусе 'Принял')", show_alert=True)
        return

    if assigned_master:
        try:
            await bot.send_message(
                assigned_master.tg_id,
                f"⚠️ Администратор снял вас с заявки <b>#{order.id}</b>.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    await cb.message.answer(
        f"↩️ Мастер снят с заявки <b>#{order.id}</b>. Статус возвращён в <b>{order_status_label(order.status)}</b>.",
        parse_mode=ParseMode.HTML,
    )

    # После снятия мастера заявка снова NEW — пересылаем мастерам по текущей технике/городу.
    try:
        sent = await _refresh_dispatch_for_order_if_needed(order.id, bot=bot, cfg=cfg)
        if sent:
            await cb.message.answer(f"📨 Заявка снова доступна мастерам: отправлено <b>{sent}</b> уведомлений.", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cb.answer("Ок")
    await _notify_admins(bot, cfg, f"↩️ Мастер снят с заявки <b>#{order.id}</b> (возврат в NEW).")



async def _get_user_or_ask_start(message: Message):
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Вы не зарегистрированы. Нажмите /start")
        return None
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ к боту заблокирован. Обратитесь к администратору.")
        return None
    return u



# ------------------------
# ADMIN: подтвердить оплату (SUPER_ADMIN / DISPATCHER)
# ------------------------
@router.callback_query(F.data.startswith("order_admin_paid:"))
async def admin_mark_paid(cb: CallbackQuery, bot: Bot, cfg: Config):
    if not cb.message:
        return

    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u or u.role not in (Role.SUPER_ADMIN, Role.DISPATCHER):
        await cb.answer("Только супер-админ или диспетчер может подтверждать оплату", show_alert=True)
        return
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return

    order_id = int(cb.data.split(":", 1)[1])

    # Защита: без скрина сдачи подтверждать нельзя
    cur = await repo.get_order(order_id)
    if not cur:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if not getattr(cur, 'payout_screenshot_file_id', None):
        await cb.answer("Нет скрина сдачи от мастера", show_alert=True)
        return

    order = await repo.mark_order_paid(order_id)
    if not order:
        await cb.answer("Не удалось", show_alert=True)
        return

    await cb.message.answer(
        f"✅ По заявке <b>#{order.id}</b> отмечено: <b>{order_status_label(order.status)}</b>.",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Ок")

    if order.assigned_master_id:
        m = await repo.get_user_by_id(order.assigned_master_id)
        if m:
            try:
                await bot.send_message(
                    m.tg_id,
                    f"✅ Администратор подтвердил оплату по заявке <b>#{order.id}</b>.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass