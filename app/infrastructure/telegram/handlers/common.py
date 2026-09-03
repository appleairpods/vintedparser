"""Общие помощники хендлеров."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.models import FilterSpec
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.db.models import Filter, User


async def ensure_user(session: AsyncSession, tg_user: TgUser) -> User:
    """Достаёт пользователя в рамках открытой сессии, заводя его при первом обращении."""
    return await repo.get_or_create_user(
        session,
        tg_id=tg_user.id,
        username=tg_user.username,
        is_admin=tg_user.id in settings.admins,
    )


async def resolve_user_id(tg_user: TgUser) -> int:
    async with get_session() as session:
        user = await ensure_user(session, tg_user)
        return user.id


def spec_of(flt: Filter) -> FilterSpec:
    return FilterSpec(
        keywords=tuple(flt.query.split()) if flt.query else (),
        brand=flt.brand,
        size=flt.size,
        price_min=flt.price_min,
        price_max=flt.price_max,
        regions=tuple(flt.regions or ()),
    )


def describe_filter(flt: Filter) -> str:
    return spec_of(flt).describe()


async def show(
    event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup
) -> None:
    """Показывает экран: правит текущее сообщение или шлёт новое.

    Редактирование вместо новых сообщений держит чат чистым — пользователь
    ходит по одному «экрану», а не листает историю нажатий.
    """
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
        return

    await event.answer()
    if event.message is None:
        return

    try:
        await event.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        # Сообщение с фото не редактируется в текст, да и повтор того же
        # текста Telegram считает ошибкой — оба случая безобидны.
        await event.message.answer(text, reply_markup=markup)
