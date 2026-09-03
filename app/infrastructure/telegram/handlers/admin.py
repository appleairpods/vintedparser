"""Админка.

Доступ закрыт фильтром на уровне роутера: так нельзя случайно забыть проверку
в новом хендлере, а не-админ просто не видит этих экранов.
"""

from datetime import UTC, datetime
from html import escape

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.application.poll import heartbeat_key
from app.config import settings
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.db.models import User
from app.infrastructure.telegram import keyboards as kb
from app.infrastructure.telegram.handlers.common import show

log = structlog.get_logger(__name__)

router = Router()
router.message.filter(F.from_user.id.in_(settings.admins))
router.callback_query.filter(F.from_user.id.in_(settings.admins))

USERS_PAGE = 10
GRANT_DAYS = 30
BROADCAST_PREVIEW_LIMIT = 3000


class AdminFlow(StatesGroup):
    search = State()
    broadcast = State()


# --- Главный экран ----------------------------------------------------------


@router.callback_query(kb.AdminCB.filter(F.action == "home"))
async def open_admin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(callback, await _stats_text(), kb.admin_menu())


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await _stats_text(), reply_markup=kb.admin_menu())


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await message.answer(await _stats_text(), reply_markup=kb.admin_menu())


async def _stats_text() -> str:
    async with get_session() as session:
        users = await repo.count_users(session)
        active = await repo.count_active_users(session)
        subscribers = await repo.count_subscribers(session)
        revenue = await repo.revenue_stars(session)
        depth = await repo.outbox_depth(session)
        seen = await repo.count_seen(session)
        heartbeats = {
            region.code: await repo.get_state(session, heartbeat_key(region.code))
            for region in settings.regions
        }

    lines = [
        "⚙️ <b>Админка</b>",
        "",
        "<b>Пользователи</b>",
        f"Всего: {users} · активных: {active}",
        f"С подпиской: {subscribers}",
        "",
        "<b>Деньги</b>",
        f"Заработано: {revenue} ⭐",
        "",
        "<b>Регионы</b>",
    ]
    for region in settings.regions:
        lines.append(f"{region.label} — {_lag_text(heartbeats[region.code])}")

    lines += [
        "",
        "<b>Система</b>",
        f"Очередь отправки: {depth}",
        f"В памяти дедупа: {seen}",
    ]
    return "\n".join(lines)


def _lag_text(heartbeat: str | None) -> str:
    if not heartbeat:
        return "❓ нет данных"

    seconds = int((datetime.now(UTC) - datetime.fromisoformat(heartbeat)).total_seconds())
    mark = "🟢" if seconds < 120 else "🔴"
    return f"{mark} {seconds} сек назад"


# --- Пользователи -----------------------------------------------------------


@router.callback_query(kb.AdminCB.filter(F.action == "users"))
async def open_users(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        users = await repo.recent_users(session, USERS_PAGE)

    await show(
        callback, _users_text(users, "Последние пользователи"), kb.admin_users(users)
    )


@router.callback_query(kb.AdminCB.filter(F.action == "search"))
async def ask_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.search)
    await show(
        callback,
        "🔎 <b>Поиск пользователя</b>\n\nПришли Telegram id или часть имени.",
        kb.admin_back(),
    )


@router.message(AdminFlow.search)
async def do_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        users = await repo.find_users(session, message.text or "", USERS_PAGE)

    if not users:
        await message.answer("Ничего не нашлось", reply_markup=kb.admin_back())
        return

    await message.answer(
        _users_text(users, "Найдено"), reply_markup=kb.admin_users(users)
    )


def _users_text(users: list[User], title: str) -> str:
    if not users:
        return f"👥 <b>{title}</b>\n\nПусто."

    lines = [f"👥 <b>{title}</b>", ""]
    for user in users:
        name = f"@{escape(user.username)}" if user.username else "без имени"
        marks = []
        if user.role == "admin":
            marks.append("админ")
        if not user.is_active:
            marks.append("заблокирован")
        suffix = f" · {', '.join(marks)}" if marks else ""
        lines.append(f"<code>{user.tg_id}</code> — {name}{suffix}")
    lines.append("\nНажми на пользователя, чтобы открыть карточку.")
    return "\n".join(lines)


@router.callback_query(kb.AdminCB.filter(F.action == "card"))
async def open_card(callback: CallbackQuery, callback_data: kb.AdminCB) -> None:
    await _render_card(callback, callback_data.target)


USER_ACTIONS = {"grant", "revoke", "ban", "unban"}


@router.callback_query(kb.AdminCB.filter(F.action.in_(USER_ACTIONS)))
async def user_action(callback: CallbackQuery, callback_data: kb.AdminCB) -> None:
    action = callback_data.action
    async with get_session() as session:
        user = await repo.get_user(session, callback_data.target)
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        if action == "grant":
            await repo.extend_subscription(session, user.id, GRANT_DAYS)
            note = f"Pro на {GRANT_DAYS} дней"
        elif action == "revoke":
            await repo.revoke_subscription(session, user.id)
            note = "Pro снят"
        else:
            user.is_active = action == "unban"
            note = "Разблокирован" if user.is_active else "Заблокирован"

        tg_id = user.tg_id

    log.info("admin.action", action=action, tg_id=tg_id)
    await callback.answer(note)
    await _render_card(callback, callback_data.target)


async def _render_card(callback: CallbackQuery, user_id: int) -> None:
    async with get_session() as session:
        user = await repo.get_user(session, user_id)
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        quota = await repo.get_quota(session, user)
        filters_count = await repo.count_filters(session, user.id)
        paid = await repo.user_revenue_stars(session, user.id)
        plan = quota.plan

        name = f"@{escape(user.username)}" if user.username else "без имени"
        subscription = (
            f"до {user.subscription_until:%d.%m.%Y}"
            if quota.is_pro and user.subscription_until
            else "нет"
        )
        limit = "∞" if plan.is_unlimited else str(plan.daily_notifications)

        text = (
            f"👤 <b>{name}</b>\n\n"
            f"Telegram id: <code>{user.tg_id}</code>\n"
            f"Роль: {user.role}\n"
            f"Статус: {'активен' if user.is_active else '🚫 заблокирован'}\n\n"
            f"Тариф: <b>{plan.title}</b>\n"
            f"Подписка: {subscription}\n"
            f"Сегодня получил: {quota.used_today} из {limit}\n"
            f"Фильтров: {filters_count} из {plan.max_filters}\n"
            f"Оплачено: {paid} ⭐\n"
            f"С нами с {user.created_at:%d.%m.%Y}"
        )
        markup = kb.admin_user_card(user, is_pro=quota.is_pro)

    await show(callback, text, markup)


# --- Платежи ----------------------------------------------------------------


@router.callback_query(kb.AdminCB.filter(F.action == "payments"))
async def open_payments(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_payments(callback)


async def _render_payments(callback: CallbackQuery) -> None:
    async with get_session() as session:
        payments = await repo.recent_payments(session)

    await show(callback, _payments_text(payments), kb.admin_payments(payments))


def _payments_text(payments: list) -> str:
    if not payments:
        return "💰 <b>Платежи</b>\n\nПока ни одного."

    lines = ["💰 <b>Последние платежи</b>", ""]
    for index, (payment, tg_id, username) in enumerate(payments, start=1):
        who = f"@{escape(username)}" if username else f"<code>{tg_id}</code>"
        state = " · возвращён" if payment.refunded_at else ""
        lines.append(
            f"<b>{index}.</b> {payment.stars} ⭐ · {payment.days} дн · {who}"
            f" · {payment.created_at:%d.%m %H:%M}{state}"
        )
    return "\n".join(lines)


@router.callback_query(kb.AdminCB.filter(F.action == "refund"))
async def refund_payment(
    callback: CallbackQuery, callback_data: kb.AdminCB, bot: Bot
) -> None:
    async with get_session() as session:
        payment = await repo.get_payment_by_id(session, callback_data.target)
        if payment is None or payment.refunded_at is not None:
            await callback.answer("Платёж не найден или уже возвращён", show_alert=True)
            return

        user = await repo.get_user(session, payment.user_id)
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        ok, note = await _refund(bot, user.tg_id, payment.charge_id)
        if not ok:
            await callback.answer(note, show_alert=True)
            return

        payment.refunded_at = datetime.now(UTC)
        # Подписку снимаем сразу: иначе возврат превращается в подарок.
        user.subscription_until = None

    await callback.answer(f"Возвращено {payment.stars} ⭐")
    await _render_payments(callback)


async def _refund(bot: Bot, tg_id: int, charge_id: str) -> tuple[bool, str]:
    try:
        await bot.refund_star_payment(
            user_id=tg_id, telegram_payment_charge_id=charge_id
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("billing.refund_failed", charge=charge_id, error=str(exc))
        return False, f"Telegram отказал: {exc}"

    log.info("billing.refunded", charge=charge_id, tg_id=tg_id)
    return True, "ok"


@router.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject, bot: Bot) -> None:
    """Возврат по идентификатору платежа — на случай, если карточки под рукой нет."""
    charge_id = (command.args or "").strip()
    if not charge_id:
        await message.answer("Укажи id платежа: <code>/refund &lt;charge_id&gt;</code>")
        return

    async with get_session() as session:
        payment = await repo.get_payment(session, charge_id)
        if payment is None:
            await message.answer("Платёж не найден")
            return

        user = await repo.get_user(session, payment.user_id)
        if user is None:
            await message.answer("Пользователь не найден")
            return

        ok, note = await _refund(bot, user.tg_id, charge_id)
        if not ok:
            await message.answer(note)
            return

        payment.refunded_at = datetime.now(UTC)
        user.subscription_until = None
        stars = payment.stars

    await message.answer(f"↩️ Возвращено {stars} ⭐, подписка снята")


# --- Рассылка ---------------------------------------------------------------


@router.callback_query(kb.AdminCB.filter(F.action == "broadcast"))
async def ask_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.broadcast)
    await show(
        callback,
        "📣 <b>Рассылка</b>\n\nПришли текст сообщения. "
        "Разметка HTML работает: <code>&lt;b&gt;</code>, <code>&lt;a href&gt;</code>.",
        kb.admin_back(),
    )


@router.message(AdminFlow.broadcast)
async def preview_broadcast(message: Message, state: FSMContext) -> None:
    text = (message.html_text or "").strip()[:BROADCAST_PREVIEW_LIMIT]
    if not text:
        await message.answer("Нужен текст", reply_markup=kb.admin_back())
        return

    await state.update_data(broadcast=text)
    async with get_session() as session:
        recipients = await repo.count_active_users(session)

    await message.answer(
        f"📣 <b>Так увидят пользователи</b>\n\n{text}\n\n"
        f"➖➖➖\nПолучателей: <b>{recipients}</b>",
        reply_markup=kb.admin_broadcast_confirm(),
    )


@router.callback_query(kb.AdminCB.filter(F.action == "send"))
async def send_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    text = data.get("broadcast")
    await state.clear()

    if not text:
        await callback.answer("Текст потерялся, набери заново", show_alert=True)
        return

    async with get_session() as session:
        queued = await repo.enqueue_broadcast(session, text)

    log.info("admin.broadcast", recipients=queued)
    await callback.answer("Поставлено в очередь")
    await show(
        callback,
        f"📣 Рассылка поставлена в очередь: <b>{queued}</b> получателей.\n\n"
        "Отправка идёт в общем темпе очереди, чтобы не поймать лимиты Telegram.",
        kb.admin_menu(),
    )


# --- Команды ----------------------------------------------------------------


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        await message.answer("Формат: <code>/grant &lt;tg_id&gt; &lt;дней&gt;</code>")
        return

    tg_id, days = int(parts[0]), int(parts[1])
    async with get_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            await message.answer("Пользователь не найден. Пусть напишет боту /start")
            return
        until = await repo.extend_subscription(session, user.id, days)

    await message.answer(f"💎 Pro для {tg_id} до {until:%d.%m.%Y}")


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject) -> None:
    await _set_active(message, command, active=False)


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject) -> None:
    await _set_active(message, command, active=True)


async def _set_active(message: Message, command: CommandObject, *, active: bool) -> None:
    if not (command.args or "").strip().isdigit():
        return

    tg_id = int(command.args.strip())
    async with get_session() as session:
        changed = await repo.set_user_active(session, tg_id, active)

    verb = "✅ Разблокирован" if active else "🚫 Заблокирован"
    await message.answer(f"{verb if changed else 'Не найден'}: {tg_id}")
