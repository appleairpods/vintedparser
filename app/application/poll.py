"""Цикл опроса Vinted: fetch -> отсев по курсору -> матчинг -> outbox."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from app.config import settings
from app.domain.matcher import matches
from app.domain.models import Listing
from app.domain.plans import Quota
from app.infrastructure.db import repo
from app.infrastructure.db.base import get_session
from app.infrastructure.vinted.client import VintedAccessError, VintedClient
from app.utils import sleep_or_stop

log = structlog.get_logger(__name__)


def bootstrap_key(region: str) -> str:
    return f"vinted:bootstrapped:{region}"


def heartbeat_key(region: str) -> str:
    """Отметка живости у каждого региона своя.

    Иначе одна работающая страна маскировала бы отказ всех остальных.
    """
    return f"worker:last_success_at:{region}"


@dataclass(slots=True)
class CycleStats:
    fetched: int = 0
    unseen: int = 0
    fresh: int = 0
    matched: int = 0
    queued: int = 0
    throttled: int = 0
    gap_suspected: bool = False
    bootstrapped: bool = False


async def run_cycle(client: VintedClient, pages: int) -> CycleStats:
    region = client.region
    stats = CycleStats()
    listings = await client.fetch_new(pages=pages)
    stats.fetched = len(listings)
    if not listings:
        log.warning("poll.empty_batch", region=region)
        return stats

    async with get_session() as session:
        new_ids = await repo.register_seen(
            session, region, [item.vinted_id for item in listings]
        )
        stats.unseen = len(new_ids)

        if not await repo.get_state(session, bootstrap_key(region)):
            # Первый запуск: вся лента формально новая, но рассылать её нельзя.
            # Просто запоминаем, что видели, и начинаем следить с этого момента.
            await repo.set_state(session, bootstrap_key(region), "1")
            await _touch_heartbeat(session, region)
            stats.bootstrapped = True
            log.info("poll.bootstrap", region=region, seen=len(new_ids))
            return stats

        if stats.unseen == stats.fetched:
            # Ни одного знакомого объявления: значит, между циклами Vinted
            # выложил больше, чем мы забираем, и часть ленты мы не увидели.
            stats.gap_suspected = True
            log.warning("poll.gap", region=region, fetched=stats.fetched, pages=pages)

        cutoff = datetime.now(UTC) - timedelta(minutes=settings.max_listing_age_minutes)
        fresh = [
            item
            for item in listings
            if item.vinted_id in new_ids
            and (item.posted_at is None or item.posted_at >= cutoff)
        ]
        stats.fresh = len(fresh)

        if fresh:
            filters = await repo.active_filters_with_users(session, region)
            quotas = await repo.load_quotas(session)
            stats.matched, stats.queued, stats.throttled = await _match_and_enqueue(
                session, fresh, filters, quotas
            )
            await _settle_quotas(session, quotas)

        await _touch_heartbeat(session, region)

    return stats


async def _match_and_enqueue(
    session, fresh: list[Listing], filters, quotas: dict[int, Quota]
) -> tuple[int, int, int]:
    matched_count = 0
    queued = 0
    throttled = 0

    for listing in fresh:
        # Один пользователь получает объявление один раз, даже если оно
        # подошло сразу под несколько его фильтров.
        hits: dict[int, int] = {}
        for filter_id, user_id, _tg_id, spec in filters:
            if user_id in hits:
                continue
            if matches(spec, listing):
                hits[user_id] = filter_id

        if not hits:
            continue

        matched_count += 1
        # Квоту проверяем до постановки в очередь, иначе накопим сообщения,
        # которые всё равно никогда не уйдут.
        allowed = {
            user_id: filter_id
            for user_id, filter_id in hits.items()
            if quotas.get(user_id, Quota(is_pro=False)).take()
        }
        throttled += len(hits) - len(allowed)
        if not allowed:
            continue

        listing_db_id = await repo.upsert_listing(session, listing)
        for user_id, filter_id in allowed.items():
            if await repo.enqueue(session, user_id, listing_db_id, filter_id):
                queued += 1

    return matched_count, queued, throttled


async def _settle_quotas(session, quotas: dict[int, Quota]) -> None:
    """Сохраняет расход и один раз за сутки предупреждает об исчерпании лимита."""
    spent = {user_id: q.spent for user_id, q in quotas.items() if q.spent}
    await repo.bump_usage(session, spent)

    # Предупреждаем только тех, кто упёрся в лимит на реальном совпадении:
    # так сообщение приходит в момент, когда потеря ощутима.
    to_notify = [
        user_id
        for user_id, q in quotas.items()
        if q.spent and q.is_exhausted and not q.limit_notified
    ]
    for user_id in to_notify:
        await repo.enqueue_limit_notice(session, user_id)
    await repo.mark_limit_notified(session, to_notify)


async def _touch_heartbeat(session, region: str) -> None:
    await repo.set_state(session, heartbeat_key(region), datetime.now(UTC).isoformat())


async def poll_loop(client: VintedClient, stop: asyncio.Event) -> None:
    """Один цикл на регион.

    Регионы намеренно не делят общий цикл: бан или сбой одной страны не должен
    останавливать остальные, а темп у каждой ленты свой.
    """
    region = client.region
    consecutive_errors = 0

    while not stop.is_set():
        started = asyncio.get_running_loop().time()
        try:
            stats = await run_cycle(client, pages=settings.poll_pages)
            consecutive_errors = 0
            log.info(
                "poll.cycle",
                region=region,
                fetched=stats.fetched,
                unseen=stats.unseen,
                fresh=stats.fresh,
                matched=stats.matched,
                queued=stats.queued,
                throttled=stats.throttled,
                gap=stats.gap_suspected,
            )
        except VintedAccessError as exc:
            consecutive_errors += 1
            log.error(
                "poll.access_error",
                region=region,
                reason=str(exc),
                streak=consecutive_errors,
            )
        except Exception:  # noqa: BLE001 - цикл не должен умирать целиком
            consecutive_errors += 1
            log.exception("poll.failed", region=region, streak=consecutive_errors)

        # При повторяющихся ошибках отходим всё дальше, но не дольше 10 минут:
        # долбить заблокировавший нас Vinted в прежнем темпе бессмысленно.
        delay = settings.poll_interval
        if consecutive_errors:
            delay = min(settings.poll_interval * (2**consecutive_errors), 600)

        elapsed = asyncio.get_running_loop().time() - started
        await sleep_or_stop(stop, max(delay - elapsed, 1))


async def cleanup_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with get_session() as session:
                listings = await repo.cleanup_old_listings(
                    session, settings.listing_ttl_days
                )
                seen = await repo.cleanup_seen(session, settings.seen_ttl_hours)
            if listings or seen:
                log.info("cleanup.done", listings=listings, seen=seen)
        except Exception:  # noqa: BLE001
            log.exception("cleanup.failed")

        await sleep_or_stop(stop, 3600)
