"""Напоминания о подписке.

Тихо истёкшая подписка — самая дорогая потеря: человек замечает её по
пропавшим объявлениям и чаще уходит, чем возвращается. Поэтому предупреждаем
заранее и ещё раз в момент окончания, когда мотивация максимальна.
"""

import asyncio
from datetime import UTC, datetime
from math import ceil

import structlog

from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.telegram import texts
from app.utils import sleep_or_stop

log = structlog.get_logger(__name__)

REMIND_DAYS_BEFORE = 3
CHECK_INTERVAL = 3600


async def run_once() -> tuple[int, int]:
    """Ставит в очередь напоминания. Возвращает (предупреждений, истёкших)."""
    async with get_session() as session:
        upcoming = await repo.users_to_remind(session, REMIND_DAYS_BEFORE)
        for user_id, until in upcoming:
            # Округляем вверх: «через 2 дня» честнее, чем «завтра», когда до
            # конца ещё 47 часов.
            left = (until - datetime.now(UTC)).total_seconds()
            days_left = max(ceil(left / 86400), 0)
            await repo.enqueue_notice(
                session, user_id, "renewal", texts.renewal_reminder(until, days_left)
            )
        await repo.mark_reminded(
            session, [user_id for user_id, _ in upcoming], expired=False
        )

        expired = await repo.users_just_expired(session)
        for user_id in expired:
            await repo.enqueue_notice(
                session, user_id, "expired", texts.subscription_expired()
            )
        await repo.mark_reminded(session, expired, expired=True)

    return len(upcoming), len(expired)


async def reminder_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            upcoming, expired = await run_once()
            if upcoming or expired:
                log.info("subscriptions.reminded", upcoming=upcoming, expired=expired)
        except Exception:  # noqa: BLE001
            log.exception("subscriptions.failed")

        await sleep_or_stop(stop, CHECK_INTERVAL)
