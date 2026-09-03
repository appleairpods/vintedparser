"""Отправка уведомлений из outbox.

Прямая отправка из цикла опроса не годится: у Telegram свои лимиты (~30
сообщений в секунду), и всплеск совпадений не должен ни терять уведомления,
ни блокировать парсер.
"""

import asyncio
from datetime import timedelta
from html import escape

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain import regions
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.db.models import Listing, Outbox
from app.infrastructure.telegram import keyboards as kb
from app.infrastructure.telegram import texts
from app.utils import sleep_or_stop

log = structlog.get_logger(__name__)

BATCH_SIZE = 25
MAX_ATTEMPTS = 5
SEND_PAUSE = 0.05  # ~20 сообщений/сек, с запасом под лимит Telegram
IDLE_PAUSE = 3.0


def render(listing: Listing) -> str:
    # Флаг в заголовке: подписчику нескольких стран иначе не понять, откуда
    # товар и сколько будет стоить доставка.
    region = regions.get(listing.region)
    flag = f"{region.flag} " if region else ""
    lines = [f"{flag}<b>{escape(listing.title)}</b>"]

    price = f"{listing.price:g} {listing.currency}"
    if listing.total_price is not None and listing.total_price != listing.price:
        price += f" (с защитой: {listing.total_price:g} {listing.currency})"
    lines.append(f"💰 {price}")

    details = []
    if listing.brand:
        details.append(escape(listing.brand))
    if listing.size:
        details.append(f"размер {escape(listing.size)}")
    if listing.condition:
        details.append(escape(listing.condition))
    if details:
        lines.append("🏷 " + " · ".join(details))

    return "\n".join(lines)


def _listing_markup(listing: Listing) -> InlineKeyboardMarkup:
    # Пауза прямо на карточке: когда поток уже идёт, это ближайшая кнопка,
    # до которой можно дотянуться, не листая чат.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛒 Открыть на Vinted", url=listing.url),
                InlineKeyboardButton(
                    text="🔕 Пауза",
                    callback_data=kb.MenuCB(action="pause").pack(),
                ),
            ]
        ]
    )


async def _deliver(bot: Bot, tg_id: int, outbox: Outbox, listing: Listing | None) -> None:
    if outbox.kind == "broadcast":
        await bot.send_message(tg_id, outbox.payload or "", disable_web_page_preview=True)
        return

    if outbox.kind in ("renewal", "expired"):
        await bot.send_message(
            tg_id, outbox.payload or "", reply_markup=kb.subscription_menu()
        )
        return

    if outbox.kind == "limit" or listing is None:
        await bot.send_message(tg_id, texts.limit_reached(), reply_markup=kb.upsell())
        return

    text = render(listing)
    markup = _listing_markup(listing)

    if listing.photo_url:
        try:
            await bot.send_photo(
                tg_id, photo=listing.photo_url, caption=text, reply_markup=markup
            )
            return
        except TelegramRetryAfter:
            raise
        except Exception:  # noqa: BLE001
            # Ссылка на картинку может протухнуть или не понравиться Telegram —
            # текст с кнопкой всё равно полезнее, чем ничего.
            log.warning("sender.photo_fallback", listing_id=listing.id)

    await bot.send_message(tg_id, text, reply_markup=markup)


async def sender_loop(bot: Bot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        sent_any = False
        try:
            async with get_session() as session:
                batch = await repo.claim_pending(session, BATCH_SIZE)

                for outbox, listing, tg_id in batch:
                    sent_any = True
                    try:
                        await _deliver(bot, tg_id, outbox, listing)
                        await repo.mark_sent(session, outbox.id)
                    except TelegramRetryAfter as exc:
                        await repo.mark_failed(
                            session,
                            outbox.id,
                            f"flood control: {exc.retry_after}s",
                            retry_in=timedelta(seconds=exc.retry_after + 1),
                        )
                        log.warning("sender.flood", retry_after=exc.retry_after)
                        await asyncio.sleep(exc.retry_after)
                    except TelegramForbiddenError:
                        # Пользователь заблокировал бота: молчим и не тратим
                        # на него лимиты дальше.
                        await repo.mark_failed(
                            session, outbox.id, "бот заблокирован", retry_in=None
                        )
                        await repo.set_user_active(session, tg_id, False)
                        log.info("sender.user_blocked", tg_id=tg_id)
                    except Exception as exc:  # noqa: BLE001
                        attempts = outbox.attempts + 1
                        retry_in = (
                            timedelta(seconds=30 * attempts)
                            if attempts < MAX_ATTEMPTS
                            else None
                        )
                        await repo.mark_failed(
                            session, outbox.id, str(exc), retry_in=retry_in
                        )
                        log.warning("sender.failed", error=str(exc), attempts=attempts)

                    await asyncio.sleep(SEND_PAUSE)
        except Exception:  # noqa: BLE001
            log.exception("sender.loop_failed")

        if not sent_any:
            await sleep_or_stop(stop, IDLE_PAUSE)
