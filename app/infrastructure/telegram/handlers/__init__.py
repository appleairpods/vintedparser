"""Сборка роутеров.

Порядок важен: пошаговое создание фильтра ловит любые сообщения в своём
состоянии, поэтому роутеры с командами подключаются раньше — иначе /menu,
набранный посреди диалога, стал бы ключевым словом фильтра.
"""

from aiogram import Router

from app.infrastructure.telegram.handlers import admin, billing, filters, menu

router = Router()
router.include_router(menu.router)
router.include_router(billing.router)
router.include_router(admin.router)
router.include_router(filters.router)

__all__ = ["router"]
