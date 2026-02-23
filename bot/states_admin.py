from aiogram.fsm.state import StatesGroup, State


class AdminMasters(StatesGroup):
    pick_master = State()
    pick_offers = State()


# ✅ НОВОЕ: управление диспетчерами
class AdminDispatchers(StatesGroup):
    wait_tg_id = State()


# ✅ НОВОЕ: управление справочником техники (офферами)
class AdminOffers(StatesGroup):
    wait_title = State()


# ✅ НОВОЕ: редактирование реквизитов для сдачи денег (только супер-админ)
class AdminRequisites(StatesGroup):
    wait_bank = State()
    wait_phone = State()
