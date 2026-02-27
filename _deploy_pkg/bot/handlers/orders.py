"""Orders handlers package facade.

The original monolithic `orders.py` has been decomposed into smaller modules:
- orders_admin.py  – admin/dispatcher flows (create/edit/list/view/assign/confirm paid)
- orders_master.py – master flows (accept/status/close/payout)
- orders_stats.py  – stats menu button

This file keeps backward compatibility: `from bot.handlers.orders import router`.
"""

from aiogram import Router

from .orders_admin import router as admin_router
from .orders_master import router as master_router
from .orders_stats import router as stats_router

router = Router()
router.include_router(admin_router)
router.include_router(master_router)
router.include_router(stats_router)
