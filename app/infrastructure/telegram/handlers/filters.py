"""Фильтры: список, удаление и пошаговое создание.

Диалог намеренно короткий: обязательны только ключевые слова, всё остальное
уточняется по желанию с экрана подтверждения.
"""

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.application.filter_parser import (
    FilterParseError,
    parse_filter,
    parse_price_range,
)
from app.config import settings
from app.domain import regions
from app.domain.models import FilterSpec
from app.domain.plans import plan_for
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.telegram import keyboards as kb
from app.infrastructure.telegram import texts
from app.infrastructure.telegram.handlers.common import (
    describe_filter,
    ensure_user,
    resolve_user_id,
    show,
)

router = Router()

MAX_INPUT_LEN = 200


class NewFilter(StatesGroup):
    keywords = State()
    regions = State()
    price = State()
    brand = State()
    size = State()


# --- Список фильтров --------------------------------------------------------


@router.callback_query(kb.MenuCB.filter(F.action == "filters"))
async def open_filters(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    assert callback.from_user is not None
    text, markup = await _filters_screen(callback.from_user)
    await show(callback, text, markup)


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    await show_filters(message)


async def show_filters(message: Message) -> None:
    """Точка входа для кнопки постоянной клавиатуры."""
    assert message.from_user is not None
    text, markup = await _filters_screen(message.from_user)
    await message.answer(text, reply_markup=markup)


async def _filters_screen(tg_user):
    user_id = await resolve_user_id(tg_user)
    async with get_session() as session:
        filters = await repo.list_filters(session, user_id)
    return texts.filters_list(filters, describe_filter), kb.filters_menu(filters)


@router.callback_query(kb.FilterCB.filter(F.action == "del"))
async def delete_filter(
    callback: CallbackQuery, callback_data: kb.FilterCB, state: FSMContext
) -> None:
    await state.clear()
    assert callback.from_user is not None
    user_id = await resolve_user_id(callback.from_user)

    async with get_session() as session:
        deleted = await repo.delete_filter(session, user_id, callback_data.filter_id)
        filters = await repo.list_filters(session, user_id)

    await callback.answer("Удалён" if deleted else "Фильтр не найден")
    if callback.message is not None:
        await show(
            callback,
            texts.filters_list(filters, describe_filter),
            kb.filters_menu(filters),
        )


@router.message(Command("del"))
async def cmd_del(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Укажи номер фильтра: <code>/del 3</code>")
        return

    assert message.from_user is not None
    user_id = await resolve_user_id(message.from_user)
    async with get_session() as session:
        deleted = await repo.delete_filter(session, user_id, int(raw))

    await message.answer("🗑 Фильтр удалён" if deleted else "Такого фильтра нет")


# --- Создание фильтра -------------------------------------------------------


@router.callback_query(kb.FilterCB.filter(F.action == "new"))
async def start_new_filter(callback: CallbackQuery, state: FSMContext) -> None:
    await begin_new_filter(callback, state)


async def begin_new_filter(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Начинает диалог создания фильтра. Общая точка для кнопок обоих типов."""
    assert event.from_user is not None
    allowed, used, limit = await _filter_slots(event.from_user)
    if not allowed:
        await show(
            event,
            f"🔒 <b>Лимит фильтров</b>\n\nНа твоём тарифе доступно "
            f"{limit} фильтр(ов), сейчас создано {used}.\n"
            "Удали лишний или оформи Pro.",
            kb.profile_menu(is_pro=False),
        )
        return

    await state.set_state(NewFilter.keywords)
    await state.set_data({})
    await show(event, _ask_keywords(), kb.skip_step())


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Быстрый путь для тех, кто предпочитает одну строку вместо диалога."""
    assert message.from_user is not None
    if not command.args:
        await state.set_state(NewFilter.keywords)
        await state.set_data({})
        await message.answer(_ask_keywords(), reply_markup=kb.skip_step())
        return

    try:
        parsed = parse_filter(command.args)
    except FilterParseError as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.update_data(
        query=parsed.query,
        brand=parsed.brand,
        size=parsed.size,
        price_min=_dump(parsed.price_min),
        price_max=_dump(parsed.price_max),
        regions=[settings.default_region],
    )
    await _save(message, state)


@router.message(NewFilter.keywords)
async def got_keywords(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()[:MAX_INPUT_LEN]
    await state.update_data(query=query)
    await _ask_regions(message, state, back_to_confirm=False)


@router.callback_query(kb.FilterCB.filter(F.action == "skip"), NewFilter.keywords)
async def skip_keywords(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(query="")
    await _ask_regions(callback, state, back_to_confirm=False)


# --- Регионы ----------------------------------------------------------------


@router.callback_query(kb.FilterCB.filter(F.action == "regions"))
async def ask_regions_again(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_regions(callback, state, back_to_confirm=True)


async def _ask_regions(
    event: Message | CallbackQuery, state: FSMContext, *, back_to_confirm: bool
) -> None:
    data = await state.get_data()
    selected = set(data.get("regions") or [settings.default_region])

    await state.set_state(NewFilter.regions)
    await state.update_data(regions=sorted(selected), regions_back=back_to_confirm)
    await show(event, _ask_regions_text(), kb.regions_picker(selected))


@router.callback_query(kb.RegionCB.filter())
async def toggle_region(
    callback: CallbackQuery, callback_data: kb.RegionCB, state: FSMContext
) -> None:
    data = await state.get_data()
    selected = set(data.get("regions") or [])

    if callback_data.code in selected:
        selected.discard(callback_data.code)
    else:
        selected.add(callback_data.code)

    await state.update_data(regions=sorted(selected))
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=kb.regions_picker(selected)
        )


@router.callback_query(kb.FilterCB.filter(F.action == "regions_done"))
async def regions_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("regions"):
        await callback.answer("Выбери хотя бы одну страну", show_alert=True)
        return

    if data.get("regions_back"):
        await _show_confirm(callback, state)
        return

    await state.set_state(NewFilter.price)
    await show(callback, _ask_price(data.get("regions") or []), kb.skip_step())


@router.message(NewFilter.price)
async def got_price(message: Message, state: FSMContext) -> None:
    try:
        price_min, price_max = parse_price_range(message.text or "")
    except FilterParseError as exc:
        await message.answer(
            f"❌ {exc}\n\nНапример: <code>10-50</code> или <code>50</code>"
        )
        return

    await state.update_data(price_min=_dump(price_min), price_max=_dump(price_max))
    await _show_confirm(message, state)


@router.callback_query(kb.FilterCB.filter(F.action == "skip"), NewFilter.price)
async def skip_price(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_confirm(callback, state)


@router.callback_query(kb.FilterCB.filter(F.action == "brand"))
async def ask_brand(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewFilter.brand)
    await show(
        callback,
        "🏷 <b>Бренд</b>\n\nНапиши название бренда — я буду присылать только его.",
        kb.skip_step(with_back=True),
    )


@router.message(NewFilter.brand)
async def got_brand(message: Message, state: FSMContext) -> None:
    await state.update_data(brand=(message.text or "").strip()[:MAX_INPUT_LEN] or None)
    await _show_confirm(message, state)


@router.callback_query(kb.FilterCB.filter(F.action == "size"))
async def ask_size(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewFilter.size)
    await show(
        callback,
        "📏 <b>Размер</b>\n\nНапиши размер так, как он указан на Vinted: "
        "<code>M</code>, <code>42</code>, <code>XL</code>.",
        kb.skip_step(with_back=True),
    )


@router.message(NewFilter.size)
async def got_size(message: Message, state: FSMContext) -> None:
    await state.update_data(size=(message.text or "").strip()[:MAX_INPUT_LEN] or None)
    await _show_confirm(message, state)


@router.callback_query(kb.FilterCB.filter(F.action == "skip"))
@router.callback_query(kb.FilterCB.filter(F.action == "confirm"))
async def back_to_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_confirm(callback, state)


@router.callback_query(kb.FilterCB.filter(F.action == "save"))
async def save_filter(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _save(callback, state)


@router.callback_query(kb.FilterCB.filter(F.action == "cancel"))
async def cancel_filter(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    assert callback.from_user is not None
    await show(
        callback,
        texts.WELCOME,
        kb.main_menu(is_admin=callback.from_user.id in settings.admins),
    )


# --- Внутреннее -------------------------------------------------------------


def _ask_keywords() -> str:
    return (
        "➕ <b>Новый фильтр</b>\n\n"
        "Что ищем? Напиши ключевые слова — я поищу их в названии, бренде и размере.\n\n"
        "Например: <code>nike air max</code> или <code>кожаная куртка</code>"
    )


def _ask_regions_text() -> str:
    return (
        "🌍 <b>Где искать?</b>\n\n"
        "Отметь страны, ленты которых я буду просматривать. "
        "Можно выбрать несколько — но помни про доставку: посылка из другой "
        "страны часто съедает всю выгоду."
    )


def _ask_price(region_codes: list[str] | tuple[str, ...] = ()) -> str:
    # Цену сравниваем как есть, без конвертации: 50 злотых и 50 евро — разные
    # деньги, и пользователь должен понимать, в какой валюте задаёт порог.
    money = regions.currencies(region_codes)
    hint = f"Валюта выбранных регионов: {', '.join(money)}.\n" if money else ""
    return (
        "💰 <b>Цена</b>\n\n"
        f"{hint}"
        "До какой суммы? Напиши число — например <code>50</code>.\n"
        "Нужен диапазон — пиши <code>10-50</code>."
    )


def _dump(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _load(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _spec_from_state(data: dict) -> FilterSpec:
    query = data.get("query") or ""
    return FilterSpec(
        keywords=tuple(query.split()),
        brand=data.get("brand"),
        size=data.get("size"),
        price_min=_load(data.get("price_min")),
        price_max=_load(data.get("price_max")),
        regions=tuple(data.get("regions") or (settings.default_region,)),
    )


async def _filter_slots(tg_user) -> tuple[bool, int, int]:
    async with get_session() as session:
        user = await ensure_user(session, tg_user)
        await session.flush()
        quota = await repo.get_quota(session, user)
        used = await repo.count_filters(session, user.id)

    limit = plan_for(quota.is_pro).max_filters
    return used < limit, used, limit


async def _show_confirm(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    spec = _spec_from_state(data)
    await state.set_state(None)

    text = (
        "👀 <b>Проверь фильтр</b>\n\n"
        f"{spec.describe()}\n\n"
        "Можно уточнить бренд и размер — или сразу сохранить."
    )
    await show(event, text, kb.confirm_filter(spec))


async def _save(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    spec = _spec_from_state(data)

    if spec.is_empty:
        # Пустой фильтр совпадёт с каждым объявлением и мгновенно съест лимит
        # (а на Pro — превратит бота в ленту Vinted целиком).
        await state.set_state(NewFilter.keywords)
        await show(
            event,
            "❌ Фильтр получился пустой — так я буду присылать вообще всё.\n\n"
            + _ask_keywords(),
            kb.skip_step(),
        )
        return

    await state.clear()
    tg_user = event.from_user
    assert tg_user is not None

    allowed, used, limit = await _filter_slots(tg_user)
    if not allowed:
        await show(
            event,
            f"🔒 На твоём тарифе доступно {limit} фильтр(ов), создано {used}.",
            kb.profile_menu(is_pro=False),
        )
        return

    async with get_session() as session:
        user = await ensure_user(session, tg_user)
        await session.flush()
        await repo.add_filter(
            session,
            user.id,
            query=data.get("query") or "",
            regions=list(spec.regions),
            brand=data.get("brand"),
            size=data.get("size"),
            price_min=_load(data.get("price_min")),
            price_max=_load(data.get("price_max")),
        )
        filters = await repo.list_filters(session, user.id)

    await show(
        event,
        f"✅ <b>Фильтр сохранён</b>\n\n{spec.describe()}\n\n"
        "Пришлю подходящие объявления, как только они появятся.",
        kb.filters_menu(filters),
    )
