"""Подписка и оплата звёздами Telegram.

Stars выбраны сознательно: для цифровых товаров Telegram другого способа и не
допускает, а нам не нужны ни юрлицо, ни договор с эквайрингом, ни вебхуки —
факт оплаты приходит обычным апдейтом.
"""

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from app.config import settings
from app.domain.plans import PRO
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.telegram import keyboards as kb
from app.infrastructure.telegram import texts
from app.infrastructure.telegram.handlers.common import ensure_user, show

log = structlog.get_logger(__name__)
router = Router()

STARS_CURRENCY = "XTR"


@router.callback_query(kb.MenuCB.filter(F.action == "sub"))
async def open_subscription(callback: CallbackQuery) -> None:
    await show(callback, texts.subscription(), kb.subscription_menu())


@router.message(Command("sub"))
async def cmd_subscription(message: Message) -> None:
    await message.answer(texts.subscription(), reply_markup=kb.subscription_menu())


@router.callback_query(kb.BuyCB.filter())
async def send_invoice(
    callback: CallbackQuery, callback_data: kb.BuyCB, bot: Bot
) -> None:
    offer = settings.offer_by_code(callback_data.code)
    if offer is None or callback.message is None:
        await callback.answer("Этот тариф больше недоступен", show_alert=True)
        return

    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"Vinted Monitor Pro — {offer.title}",
        description=(
            "Уведомления без суточного лимита и до "
            f"{PRO.max_filters} фильтров на {offer.days} дней."
        ),
        payload=offer.payload,
        currency=STARS_CURRENCY,
        # Для XTR amount — это количество звёзд, без домножения на 100
        prices=[LabeledPrice(label=offer.title, amount=offer.stars)],
        provider_token="",
    )


@router.pre_checkout_query()
async def confirm_checkout(query: PreCheckoutQuery) -> None:
    """Telegram даёт 10 секунд на подтверждение, поэтому здесь только проверка."""
    _, _, code = query.invoice_payload.partition(":")
    if settings.offer_by_code(code) is None:
        await query.answer(ok=False, error_message="Тариф больше недоступен")
        return

    await query.answer(ok=True)


@router.message(F.successful_payment)
async def payment_received(message: Message) -> None:
    payment = message.successful_payment
    assert payment is not None and message.from_user is not None

    _, _, code = payment.invoice_payload.partition(":")
    offer = settings.offer_by_code(code)
    if offer is None:
        # Оплата уже прошла, отказать нельзя: начисляем минимальный тариф
        # и разбираемся руками.
        offer = settings.offers[0]
        log.error("billing.unknown_offer", payload=payment.invoice_payload)

    async with get_session() as session:
        user = await ensure_user(session, message.from_user)
        await session.flush()

        # Telegram может прислать апдейт повторно — второй раз дни не начисляем.
        first_time = await repo.record_payment(
            session,
            user_id=user.id,
            charge_id=payment.telegram_payment_charge_id,
            offer_code=offer.code,
            stars=offer.stars,
            days=offer.days,
        )
        if not first_time:
            log.warning("billing.duplicate", charge=payment.telegram_payment_charge_id)
            return

        until = await repo.extend_subscription(session, user.id, offer.days)

    log.info(
        "billing.paid",
        tg_id=message.from_user.id,
        stars=offer.stars,
        days=offer.days,
    )
    await message.answer(
        texts.payment_success(until, offer.days), reply_markup=kb.main_menu()
    )

    for admin_id in settings.admins:
        if admin_id == message.from_user.id:
            continue
        try:
            await message.bot.send_message(
                admin_id,
                f"💰 Оплата: {offer.stars} ⭐ за {offer.title} "
                f"от @{message.from_user.username or message.from_user.id}",
            )
        except Exception:  # noqa: BLE001 - уведомление админа не критично
            log.warning("billing.admin_notify_failed", admin_id=admin_id)
