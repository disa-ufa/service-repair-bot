from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from html import escape as html_escape

from bot.config import Config
from bot.states import Registration
from bot.db import repo
from bot.db.models import Role

from bot.handlers.menu import menu_for_role

router = Router()

# callback prefixes for registration offer selection
CB_TOGGLE = "reg:toggle"   # reg:toggle:<offer_id>
CB_DONE = "reg:done"
CB_CANCEL = "reg:cancel"

# callback prefixes for city selection (during registration)
CB_CITY = "reg:city"  # reg:city:<key>

# Пока только один город. Позже можно добавить другие.
CITY_CHOICES: dict[str, str] = {
    "ekb": "Екатеринбург",
}


def _cities_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, title in CITY_CHOICES.items():
        rows.append([InlineKeyboardButton(text=f"🏙 {title}", callback_data=f"{CB_CITY}:{key}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _offers_kb(offers, selected_ids: set[int]) -> InlineKeyboardMarkup:
    rows = []
    for off in offers:
        mark = "✅" if off.id in selected_ids else "➖"
        rows.append([InlineKeyboardButton(text=f"{mark} {off.title}", callback_data=f"{CB_TOGGLE}:{off.id}")])

    rows.append(
        [
            InlineKeyboardButton(text="✅ Готово", callback_data=CB_DONE),
            InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _offers_text(selected_count: int) -> str:
    return (
        "Выберите технику (можно отметить несколько).\n"
        "Нажатие включает/выключает.\n"
        f"Выбрано: <b>{selected_count}</b>"
    )

async def _start_offers_selection(msg: Message, state: FSMContext) -> None:
    """После выбора города запускает экран выбора техники (офферов)."""
    offers = await repo.list_active_offers() if hasattr(repo, "list_active_offers") else await repo.list_offers()
    # фильтр на всякий случай
    offers = [o for o in offers if getattr(o, "is_active", True)]

    if not offers:
        await msg.answer(
            "Список техники пока пуст.\n"
            "Попросите супер-администратора заполнить каталог техники и повторите регистрацию позже."
        )
        await state.clear()
        return

    await state.update_data(offer_ids=[])
    await msg.answer(
        _offers_text(0),
        reply_markup=_offers_kb(offers, set()),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Registration.offers)




async def _get_offer_titles_by_ids(offer_ids: list[int]) -> list[str]:
    """Преобразовать ids офферов в названия для вывода в сообщениях."""
    try:
        offers = await repo.list_offers(include_inactive=True)
    except TypeError:
        offers = await repo.list_offers()
    id_to_title = {int(o.id): (getattr(o, "title", "") or "") for o in offers}
    titles: list[str] = []
    for oid in offer_ids:
        oid_int = int(oid)
        t = id_to_title.get(oid_int)
        if t:
            titles.append(t)
        else:
            titles.append(f"(id {oid_int})")
    return titles


def _format_tech_block(titles: list[str]) -> str:
    if not titles:
        return "—"
    return "\n".join([f"• {html_escape(t)}" for t in titles])


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, cfg: Config):
    tg_id = message.from_user.id
    is_admin = tg_id in (cfg.admins or [])

    u = await repo.get_user_by_tg_id(tg_id)

    # Если пользователь уже есть, но он в admins — повышаем до SUPER_ADMIN
    if u and is_admin and u.role != Role.SUPER_ADMIN:
        u = await repo.set_user_role(tg_id, Role.SUPER_ADMIN)

    # Если пользователя нет и это админ — создаем сразу как SUPER_ADMIN
    if not u and is_admin:
        fio = (
            (message.from_user.full_name or "").strip()
            or (message.from_user.username or "").strip()
            or "Администратор"
        )
        u = await repo.upsert_user(
            tg_id=tg_id,
            username=message.from_user.username,
            fio=fio,
            role=Role.SUPER_ADMIN,
            city=None,
            percent_master=None,
            is_approved=True,
        )
        await state.clear()
        await message.answer(
            f"Привет, <b>{u.fio}</b>!\n"
            f"Роль: <b>{u.role.value}</b>\n\n"
            "Открываю меню администратора.",
            reply_markup=menu_for_role(u.role),
            parse_mode=ParseMode.HTML,
        )
        return

    # Если пользователь уже зарегистрирован — показываем меню (или ожидание подтверждения для мастера)
    if u:
        await state.clear()

        if u.role == Role.FIRED:
            await message.answer(
                "Ваш доступ к боту был заблокирован.\n"
                "Вы можете пройти повторную регистрацию и отправить заявку на активацию.\n\n"
                "Введите ваше <b>ФИО</b>:",
                parse_mode=ParseMode.HTML,
            )
            await state.set_state(Registration.fio)
            return

        # Мастер должен быть подтвержден супер-админом
        if u.role == Role.MASTER and not getattr(u, "is_approved", False):
            await message.answer(
                "✅ Регистрация завершена.\n"
                "⏳ Ваш аккаунт мастера ожидает подтверждения <b>супер-администратором</b>.\n\n"
                "Как только вас активируют — нажмите /start еще раз.",
                parse_mode=ParseMode.HTML,
            )
            return

        await message.answer(
            f"Привет, <b>{u.fio}</b>!\n"
            f"Роль: <b>{u.role.value}</b>\n\n"
            "Выберите действие в меню ниже.",
            reply_markup=menu_for_role(u.role),
            parse_mode=ParseMode.HTML,
        )
        return

    # Иначе — регистрация мастера
    await message.answer("Привет! Давайте зарегистрируемся.\n\nВведите ваше <b>ФИО</b>:", parse_mode=ParseMode.HTML)
    await state.set_state(Registration.fio)


# ВАЖНО: не принимаем команды (текст, начинающийся с "/") как ФИО
@router.message(
    Registration.fio,
    F.text,
    ~F.text.startswith("/"),
    F.text.len() >= 3,
)
async def reg_fio(message: Message, state: FSMContext):
    await state.update_data(fio=message.text.strip())
    await message.answer("Выберите ваш <b>город</b>:", reply_markup=_cities_kb(), parse_mode=ParseMode.HTML)
    await state.set_state(Registration.city)




@router.callback_query(Registration.city, F.data.startswith(f"{CB_CITY}:"))
async def reg_city_pick(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", maxsplit=2)[-1].strip()
    city = CITY_CHOICES.get(key)
    if not city:
        await cb.answer("Неизвестный город", show_alert=True)
        return

    await state.update_data(city=city)
    await cb.answer(f"Город: {city}")

    # Дальше — выбор техники
    await _start_offers_selection(cb.message, state)


@router.callback_query(Registration.city, F.data == CB_CANCEL)
async def reg_city_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("Регистрация отменена. Нажмите /start чтобы начать заново.")
    except Exception:
        await cb.message.answer("Регистрация отменена. Нажмите /start чтобы начать заново.")
    await cb.answer()

@router.message(
    Registration.city,
    F.text,
    ~F.text.startswith("/"),
    F.text.len() >= 2,
)
async def reg_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await _start_offers_selection(message, state)


@router.callback_query(Registration.offers, F.data.startswith(f"{CB_TOGGLE}:"))
async def reg_toggle_offer(cb: CallbackQuery, state: FSMContext):
    try:
        offer_id = int(cb.data.split(":")[-1])
    except Exception:
        await cb.answer("Ошибка offer_id", show_alert=True)
        return

    data = await state.get_data()
    selected = set(data.get("offer_ids") or [])
    if offer_id in selected:
        selected.remove(offer_id)
    else:
        selected.add(offer_id)

    await state.update_data(offer_ids=list(selected))

    offers = await repo.list_active_offers() if hasattr(repo, "list_active_offers") else await repo.list_offers()
    offers = [o for o in offers if getattr(o, "is_active", True)]

    await cb.message.edit_text(
        _offers_text(len(selected)),
        reply_markup=_offers_kb(offers, selected),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("Ок")


@router.callback_query(Registration.offers, F.data == CB_CANCEL)
async def reg_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Регистрация отменена. Нажмите /start чтобы начать заново.")
    await cb.answer()


@router.callback_query(Registration.offers, F.data == CB_DONE)
async def reg_done(cb: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config):
    data = await state.get_data()
    fio = (data.get("fio") or "").strip()
    city = (data.get("city") or "").strip()
    offer_ids = data.get("offer_ids") or []
    offer_ids = [int(x) for x in offer_ids if str(x).isdigit()]

    if not fio or not city:
        await cb.answer("Данные регистрации потерялись. Нажмите /start заново.", show_alert=True)
        await state.clear()
        return

    if not offer_ids:
        await cb.answer("Выберите хотя бы одну технику.", show_alert=True)
        return

    # создаём пользователя-мастера (ожидает подтверждения)
    u = await repo.upsert_user(
        tg_id=cb.from_user.id,
        username=cb.from_user.username,
        fio=fio,
        role=Role.MASTER,
        city=city,
        percent_master=None,
        is_approved=False,
    )
    # Привязка офферов только по ID (ничего не создаём)
    if hasattr(repo, "set_master_offers_by_ids"):
        await repo.set_master_offers_by_ids(u.id, offer_ids)
    else:
        # fallback: привяжем через toggle (на старых версиях)
        # сначала очистим
        current = await repo.get_master_offer_ids(u.id)
        for oid in current:
            await repo.toggle_master_offer(u.id, oid)
        for oid in offer_ids:
            await repo.toggle_master_offer(u.id, oid)

    await state.clear()

    await cb.message.edit_text(
        f"Готово! Вы зарегистрированы как <b>{u.fio}</b>.\n"
        f"Город: <b>{u.city or '—'}</b>.\n\n"
        "⏳ Теперь дождитесь подтверждения <b>супер-администратором</b>.\n"
        "После активации нажмите /start.",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer("✅ Готово")

    # уведомление супер-админам
    approve_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Активировать мастера", callback_data=f"am:approve:{u.id}")],
            [InlineKeyboardButton(text="👥 Мастера", callback_data="am:back")],
        ]
    )
    tech_titles = await _get_offer_titles_by_ids(offer_ids)
    tech_block = _format_tech_block(tech_titles)

    text = (
        "🆕 <b>Новый мастер ожидает подтверждения</b>\n"
        f"👤 <b>{html_escape(u.fio)}</b>\n"
        f"🏙 Город: <b>{html_escape(u.city or '—')}</b>\n"
        f"🛠 Техника:\n{tech_block}\n"
        f"🆔 tg_id: <code>{u.tg_id}</code>"
    )

    staff_ids = set(cfg.admins or [])
    try:
        for su in (await repo.list_users_by_role(Role.SUPER_ADMIN)):
            staff_ids.add(int(su.tg_id))
    except Exception:
        pass

    staff_ids.discard(int(u.tg_id))

    for admin_tg_id in sorted(staff_ids):
        try:
            await bot.send_message(admin_tg_id, text, reply_markup=approve_kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass


@router.message(Registration.offers, F.text)
async def reg_offers_text_fallback(message: Message, state: FSMContext):
    # На этом шаге ввод текстом не нужен — выбираем кнопками.
    offers = await repo.list_active_offers() if hasattr(repo, "list_active_offers") else await repo.list_offers()
    offers = [o for o in offers if getattr(o, "is_active", True)]
    data = await state.get_data()
    selected = set(data.get("offer_ids") or [])
    await message.answer(
        "Пожалуйста, выберите технику кнопками ниже (можно несколько):",
        reply_markup=_offers_kb(offers, selected),
    )