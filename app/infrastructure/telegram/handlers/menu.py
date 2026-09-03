"""Главное меню, личный кабинет, пауза и справка.

Здесь же разбираются нажатия постоянной клавиатуры. Роутер подключается первым,
поэтому её кнопки работают и посреди пошагового создания фильтра — иначе
«Пауза», нажатая под потоком карточек, стала бы ключевым словом.
"""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.telegram import keyboards as kb
from app.infrastructure.telegram import texts
from app.infrastructure.telegram.handlers import filters as filters_handlers
from app.infrastructure.telegram.handlers.common import ensure_user, show

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in settings.admins


# --- Вход и главное меню ----------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    async with get_session() as session:
        user = await ensure_user(session, message.from_user)
        await session.flush()
        paused = user.paused

    await message.answer(texts.WELCOME, reply_markup=kb.reply_menu(paused=paused))


@router.callback_query(kb.MenuCB.filter(F.action == "main"))
async def open_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    assert callback.from_user is not None
    await show(
        callback, texts.MENU, kb.main_menu(is_admin=is_admin(callback.from_user.id))
    )


@router.message(Command("menu"))
@router.message(F.text == kb.BTN_MORE)
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    await message.answer(
        texts.MENU, reply_markup=kb.main_menu(is_admin=is_admin(message.from_user.id))
    )


# --- Пауза ------------------------------------------------------------------


@router.message(Command("pause"))
@router.message(F.text == kb.BTN_PAUSE)
async def pause_from_keyboard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _switch_notifications(message, paused=True)


@router.message(Command("resume"))
@router.message(F.text == kb.BTN_RESUME)
async def resume_from_keyboard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _switch_notifications(message, paused=False)


@router.callback_query(kb.MenuCB.filter(F.action.in_({"pause", "resume"})))
async def toggle_from_button(
    callback: CallbackQuery, callback_data: kb.MenuCB, state: FSMContext
) -> None:
    await state.clear()
    paused = callback_data.action == "pause"
    assert callback.from_user is not None and callback.message is not None

    dropped = await _set_paused(callback.from_user, paused)
    await callback.answer("Уведомления выключены" if paused else "Уведомления включены")
    # Карточку объявления не редактируем: отдельное сообщение заодно обновит
    # подпись кнопки на постоянной клавиатуре.
    await callback.message.answer(
        texts.paused_notice(dropped) if paused else texts.resumed_notice(),
        reply_markup=kb.reply_menu(paused=paused),
    )


async def _switch_notifications(message: Message, *, paused: bool) -> None:
    assert message.from_user is not None
    dropped = await _set_paused(message.from_user, paused)
    await message.answer(
        texts.paused_notice(dropped) if paused else texts.resumed_notice(),
        reply_markup=kb.reply_menu(paused=paused),
    )


async def _set_paused(tg_user, paused: bool) -> int:
    async with get_session() as session:
        user = await ensure_user(session, tg_user)
        await session.flush()
        return await repo.set_paused(session, user.id, paused)


# --- Справка и кабинет ------------------------------------------------------


@router.callback_query(kb.MenuCB.filter(F.action == "help"))
async def open_help(callback: CallbackQuery) -> None:
    await show(callback, texts.HELP, kb.back_to_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP, reply_markup=kb.back_to_menu())


@router.callback_query(kb.MenuCB.filter(F.action == "profile"))
async def open_profile(callback: CallbackQuery) -> None:
    assert callback.from_user is not None
    await show(callback, *await _profile_screen(callback.from_user))


@router.message(Command("profile"))
@router.message(F.text == kb.BTN_PROFILE)
async def cmd_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    assert message.from_user is not None
    text, markup = await _profile_screen(message.from_user)
    await message.answer(text, reply_markup=markup)


async def _profile_screen(tg_user):
    async with get_session() as session:
        user = await ensure_user(session, tg_user)
        await session.flush()
        quota = await repo.get_quota(session, user)
        filters_count = await repo.count_filters(session, user.id)
        subscription_until = user.subscription_until
        paused = user.paused

    return (
        texts.profile(quota, subscription_until, filters_count, paused=paused),
        kb.profile_menu(quota.is_pro, paused=paused),
    )


# --- Кнопки клавиатуры, ведущие в другие разделы ----------------------------


@router.message(F.text == kb.BTN_FILTERS)
async def filters_from_keyboard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await filters_handlers.show_filters(message)


@router.message(F.text == kb.BTN_NEW)
async def new_filter_from_keyboard(message: Message, state: FSMContext) -> None:
    await filters_handlers.begin_new_filter(message, state)
