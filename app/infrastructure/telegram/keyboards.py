"""Клавиатуры и callback-данные.

Собраны в одном месте: так видно всю навигацию бота целиком, а не по кускам
в хендлерах.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.domain.models import FilterSpec
from app.infrastructure.db.models import Filter, User

# Подписи постоянной клавиатуры. Она приходит обычными сообщениями, поэтому
# тексты нужны и при отрисовке, и при разборе нажатий.
BTN_NEW = "➕ Новый фильтр"
BTN_FILTERS = "🔍 Мои фильтры"
BTN_PROFILE = "👤 Кабинет"
BTN_PAUSE = "🔕 Пауза"
BTN_RESUME = "🔔 Включить"
BTN_MORE = "☰ Ещё"


def reply_menu(*, paused: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура под полем ввода.

    Главное здесь — пауза: когда сыплются карточки, нужная кнопка не должна
    уезжать вверх вместе с историей чата.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_FILTERS)],
            [
                KeyboardButton(text=BTN_PROFILE),
                KeyboardButton(text=BTN_RESUME if paused else BTN_PAUSE),
            ],
            [KeyboardButton(text=BTN_MORE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


class MenuCB(CallbackData, prefix="menu"):
    action: str


class FilterCB(CallbackData, prefix="flt"):
    action: str
    filter_id: int = 0


class BuyCB(CallbackData, prefix="buy"):
    code: str


class RegionCB(CallbackData, prefix="reg"):
    code: str


class AdminCB(CallbackData, prefix="adm"):
    action: str
    target: int = 0


def main_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Мои фильтры", callback_data=MenuCB(action="filters"))
    builder.button(text="➕ Новый фильтр", callback_data=FilterCB(action="new"))
    builder.button(text="👤 Личный кабинет", callback_data=MenuCB(action="profile"))
    builder.button(text="💎 Подписка", callback_data=MenuCB(action="sub"))
    builder.button(text="❓ Как это работает", callback_data=MenuCB(action="help"))
    builder.adjust(2, 2, 1)

    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text="⚙️ Админка", callback_data=AdminCB(action="home").pack()
            )
        )
    return builder.as_markup()


def back_to_menu(text: str = "⬅️ В меню") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data=MenuCB(action="main"))
    return builder.as_markup()


def filters_menu(filters: list[Filter]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, flt in enumerate(filters, start=1):
        builder.row(
            InlineKeyboardButton(
                text=f"🗑 Удалить фильтр {index}",
                callback_data=FilterCB(action="del", filter_id=flt.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ Новый фильтр", callback_data=FilterCB(action="new").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ В меню", callback_data=MenuCB(action="main").pack()
        )
    )
    return builder.as_markup()


def profile_menu(is_pro: bool, *, paused: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💎 Продлить Pro" if is_pro else "💎 Оформить Pro",
        callback_data=MenuCB(action="sub"),
    )
    builder.button(
        text="🔔 Включить уведомления" if paused else "🔕 Поставить на паузу",
        callback_data=MenuCB(action="resume" if paused else "pause"),
    )
    builder.button(text="🔍 Мои фильтры", callback_data=MenuCB(action="filters"))
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="main"))
    builder.adjust(1, 1, 2)
    return builder.as_markup()


def subscription_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for offer in settings.offers:
        builder.row(
            InlineKeyboardButton(
                text=f"{offer.title} — {offer.stars} ⭐",
                callback_data=BuyCB(code=offer.code).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ В меню", callback_data=MenuCB(action="main").pack()
        )
    )
    return builder.as_markup()


def upsell() -> InlineKeyboardMarkup:
    """Кнопка под сообщением об исчерпании лимита."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Снять лимит", callback_data=MenuCB(action="sub"))
    return builder.as_markup()


def skip_step(*, with_back: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data=FilterCB(action="skip"))
    if with_back:
        builder.button(text="⬅️ Назад", callback_data=FilterCB(action="confirm"))
    builder.button(text="❌ Отмена", callback_data=FilterCB(action="cancel"))
    builder.adjust(2)
    return builder.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Обновить", callback_data=AdminCB(action="home"))
    builder.button(text="👥 Пользователи", callback_data=AdminCB(action="users"))
    builder.button(text="💰 Платежи", callback_data=AdminCB(action="payments"))
    builder.button(text="🔎 Найти", callback_data=AdminCB(action="search"))
    builder.button(text="📣 Рассылка", callback_data=AdminCB(action="broadcast"))
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="main"))
    builder.adjust(1, 2, 2, 1)
    return builder.as_markup()


def admin_back(action: str = "home") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action=action))
    return builder.as_markup()


def admin_users(users: list[User]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        label = f"@{user.username}" if user.username else str(user.tg_id)
        builder.row(
            InlineKeyboardButton(
                text=f"{'🚫 ' if not user.is_active else ''}{label}",
                callback_data=AdminCB(action="card", target=user.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔎 Найти", callback_data=AdminCB(action="search").pack()
        ),
        InlineKeyboardButton(
            text="⬅️ В админку", callback_data=AdminCB(action="home").pack()
        ),
    )
    return builder.as_markup()


def admin_user_card(user: User, *, is_pro: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💎 Снять Pro" if is_pro else "💎 Выдать Pro на 30 дней",
        callback_data=AdminCB(
            action="revoke" if is_pro else "grant", target=user.id
        ),
    )
    builder.button(
        text="✅ Разблокировать" if not user.is_active else "🚫 Заблокировать",
        callback_data=AdminCB(
            action="unban" if not user.is_active else "ban", target=user.id
        ),
    )
    builder.button(text="👥 К списку", callback_data=AdminCB(action="users"))
    builder.adjust(1)
    return builder.as_markup()


def admin_payments(payments: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, (payment, _tg_id, _username) in enumerate(payments, start=1):
        if payment.refunded_at is not None:
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"↩️ Вернуть {payment.stars} ⭐ по платежу {index}",
                callback_data=AdminCB(action="refund", target=payment.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ В админку", callback_data=AdminCB(action="home").pack()
        )
    )
    return builder.as_markup()


def admin_broadcast_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📣 Отправить всем", callback_data=AdminCB(action="send"))
    builder.button(text="❌ Отмена", callback_data=AdminCB(action="home"))
    builder.adjust(1)
    return builder.as_markup()


def regions_picker(selected: set[str]) -> InlineKeyboardMarkup:
    """Флаги стран с галочками. Показываем только те, что реально опрашиваем."""
    builder = InlineKeyboardBuilder()
    for region in settings.regions:
        mark = "✅ " if region.code in selected else ""
        builder.button(
            text=f"{mark}{region.label}", callback_data=RegionCB(code=region.code)
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="Готово", callback_data=FilterCB(action="regions_done").pack()
        ),
        InlineKeyboardButton(
            text="❌ Отмена", callback_data=FilterCB(action="cancel").pack()
        ),
    )
    return builder.as_markup()


def confirm_filter(spec: FilterSpec) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data=FilterCB(action="save"))
    builder.button(
        text=f"🏷 Бренд: {spec.brand}" if spec.brand else "🏷 Указать бренд",
        callback_data=FilterCB(action="brand"),
    )
    builder.button(
        text=f"📏 Размер: {spec.size}" if spec.size else "📏 Указать размер",
        callback_data=FilterCB(action="size"),
    )
    builder.button(text="🌍 Регионы", callback_data=FilterCB(action="regions"))
    builder.button(text="❌ Отмена", callback_data=FilterCB(action="cancel"))
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()
