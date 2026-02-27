from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from bot.db import repo
from bot.db.models import Role
from bot.states_admin import AdminOffers

try:
    from bot.constants import BTN_TECH_CATALOG
except Exception:  # pragma: no cover
    BTN_TECH_CATALOG = "🛠️ Каталог техники"


router = Router()


def _kb_offers_manage(offers) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for off in offers:
        # Кнопка удаления рядом — чтобы не занимать 2 строки
        rows.append(
            [
                InlineKeyboardButton(text=f"🛠️ {off.title}"[:64], callback_data=f"off:noop:{off.id}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"off:del:{off.id}"),
            ]
        )

    rows.append([
        InlineKeyboardButton(text="➕ Добавить", callback_data="off:add"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data="off:refresh"),
    ])
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="off:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _deny_if_not_super_admin_message(message: Message) -> bool:
    u = await repo.get_user_by_tg_id(message.from_user.id)
    if not u:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return True
    if u.role == Role.FIRED:
        await message.answer("Ваш доступ заблокирован.")
        return True
    if u.role != Role.SUPER_ADMIN:
        await message.answer("⛔ Доступно только супер-администратору.")
        return True
    return False


async def _deny_if_not_super_admin_cb(cb: CallbackQuery) -> bool:
    u = await repo.get_user_by_tg_id(cb.from_user.id)
    if not u:
        await cb.answer("Сначала /start", show_alert=True)
        return True
    if u.role == Role.FIRED:
        await cb.answer("Доступ заблокирован", show_alert=True)
        return True
    if u.role != Role.SUPER_ADMIN:
        await cb.answer("Только супер-администратор", show_alert=True)
        return True
    return False


async def _render_offers_manage(message_or_cb, *, note: str | None = None):
    offers = await repo.list_offers(include_inactive=False)

    if not offers:
        text = (
            "🛠️ <b>Каталог техники пуст</b>\n\n"
            "Нажмите <b>➕ Добавить</b>, чтобы добавить первую позицию."
        )
    else:
        text = "🛠️ <b>Каталог техники</b>\n" \
               "Выберите действие: удалить позицию или добавить новую."

    if note:
        text = f"{note}\n\n" + text

    kb = _kb_offers_manage(offers)

    # message_or_cb может быть Message или CallbackQuery
    if isinstance(message_or_cb, Message):
        await message_or_cb.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        # безопасно: редактируем если можем, иначе отправляем новым сообщением
        try:
            await message_or_cb.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            await message_or_cb.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.message(F.text == BTN_TECH_CATALOG)
async def open_tech_catalog(message: Message, state: FSMContext):
    if await _deny_if_not_super_admin_message(message):
        return
    await state.clear()
    await _render_offers_manage(message)


@router.callback_query(F.data == "off:refresh")
async def offers_refresh(cb: CallbackQuery):
    if await _deny_if_not_super_admin_cb(cb):
        return
    await _render_offers_manage(cb)
    await cb.answer("Обновлено")


@router.callback_query(F.data == "off:close")
async def offers_close(cb: CallbackQuery):
    if await _deny_if_not_super_admin_cb(cb):
        return
    try:
        await cb.message.edit_text("Ок, закрыл каталог техники.")
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data == "off:add")
async def offers_add_start(cb: CallbackQuery, state: FSMContext):
    if await _deny_if_not_super_admin_cb(cb):
        return
    await state.clear()
    await state.set_state(AdminOffers.wait_title)
    await cb.message.answer(
        "Введите <b>название техники</b> (например: <i>Микроволновка</i>).\n"
        "\n"
        "Чтобы отменить — нажмите <b>↩️ Сброс</b> в меню.",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.message(AdminOffers.wait_title, F.text)
async def offers_add_finish(message: Message, state: FSMContext):
    if await _deny_if_not_super_admin_message(message):
        await state.clear()
        return

    title = (message.text or "").strip()
    if not title or len(title) < 2:
        await message.answer("Название слишком короткое. Введите ещё раз:")
        return

    # create_offer также ре-активирует, если позиция была "удалена"
    try:
        await repo.create_offer(title)
    except Exception:
        await message.answer("Не удалось добавить. Попробуйте другое название.")
        return

    await state.clear()
    await _render_offers_manage(message, note=f"✅ Добавлено: <b>{title}</b>")


@router.callback_query(F.data.startswith("off:del:"))
async def offers_delete(cb: CallbackQuery):
    if await _deny_if_not_super_admin_cb(cb):
        return

    try:
        offer_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Ошибка", show_alert=True)
        return

    ok = await repo.deactivate_offer(offer_id)
    if not ok:
        await cb.answer("Не найдено", show_alert=True)
        return

    await _render_offers_manage(cb, note="🗑️ Позиция удалена (скрыта из списка)")
    await cb.answer("Удалено")


@router.callback_query(F.data.startswith("off:noop:"))
async def offers_noop(cb: CallbackQuery):
    # Кнопка-заголовок строки — просто чтобы можно было нажимать без действий
    await cb.answer()
