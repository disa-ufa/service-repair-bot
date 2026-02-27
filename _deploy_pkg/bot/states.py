from aiogram.fsm.state import StatesGroup, State


class Registration(StatesGroup):
    fio = State()
    city = State()
    offers = State()


class CreateOrder(StatesGroup):
    """Создание заявки администратором/диспетчером."""

    client_type = State()
    client_name = State()
    phone = State()
    city = State()
    address = State()
    apartment = State()
    device = State()
    problem = State()
    when = State()
    source = State()
    percent_master = State()


class CloseOrder(StatesGroup):
    """Закрытие заявки мастером (суммы + чеки/договор)."""

    sum_total = State()
    sum_expenses = State()
    receipts = State()
    contract_photo = State()
    warranty_days = State()
    close_comment = State()
    act_photo = State()


class PayoutProof(StatesGroup):
    """Отправка скрина/чека сдачи."""

    photo = State()


class ModernizationComment(StatesGroup):
    """Комментарий при переводе в модернизацию (ДР)."""

    comment = State()


class EditOrder(StatesGroup):
    """Редактирование заявки администратором/диспетчером."""

    field = State()
    value = State()
