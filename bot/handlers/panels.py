from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

router = Router()


@router.message(F.text == "↩️ Сброс")
async def reset(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, сбросил текущий шаг. Нажмите /start чтобы начать заново.")


@router.message(F.text.in_({"📋 Доступные заказы", "🧰 Мои заказы", "📚 Все заявки", "👥 Мастера"}))
async def stub_sections(message: Message):
    await message.answer("Этот раздел следующий по плану. Сейчас подключим логику заявок.")


@router.message(F.text == "➕ Создать заявку")
async def create_order_stub(message: Message):
    await message.answer("Следующий шаг — подключаем мастер создания заявки диспетчером (FSM).")
