"""Запросы к БД. Тонкий слой над SQLAlchemy, без собственных абстракций."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import FilterSpec
from app.domain.models import Listing as DomainListing
from app.domain.plans import Quota
from app.infrastructure.db.models import (
    AppState,
    DailyUsage,
    Filter,
    Listing,
    Outbox,
    Payment,
    SeenItem,
    User,
)


def today() -> date:
    """Сутки считаем по UTC: так лимит сбрасывается одинаково для всех часовых поясов."""
    return datetime.now(UTC).date()

# --- Пользователи -----------------------------------------------------------


async def get_or_create_user(
    session: AsyncSession, tg_id: int, username: str | None, *, is_admin: bool = False
) -> User:
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user is not None:
        if username and user.username != username:
            user.username = username
        if is_admin and user.role != "admin":
            user.role = "admin"
        return user

    user = User(
        tg_id=tg_id, username=username, role="admin" if is_admin else "user"
    )
    session.add(user)
    await session.flush()
    return user


async def count_users(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User)) or 0


async def count_active_users(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(User).where(User.is_active.is_(True))
        )
        or 0
    )


async def recent_users(session: AsyncSession, limit: int = 10) -> list[User]:
    result = await session.scalars(select(User).order_by(User.id.desc()).limit(limit))
    return list(result)


async def find_users(session: AsyncSession, query: str, limit: int = 10) -> list[User]:
    """Ищет по Telegram id или части имени пользователя."""
    text = query.strip().lstrip("@")
    if not text:
        return []

    condition = User.username.ilike(f"%{text}%")
    if text.isdigit():
        condition = condition | (User.tg_id == int(text))

    result = await session.scalars(
        select(User).where(condition).order_by(User.id.desc()).limit(limit)
    )
    return list(result)


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def set_paused(session: AsyncSession, user_id: int, paused: bool) -> int:
    """Ставит уведомления на паузу и гасит уже накопленную очередь.

    Без очистки очереди пауза выглядела бы сломанной: сообщения, поставленные
    до нажатия, продолжали бы приходить ещё минуты.
    """
    user = await session.get(User, user_id)
    if user is not None:
        user.paused = paused

    if not paused:
        return 0

    result = await session.execute(
        delete(Outbox).where(
            Outbox.user_id == user_id,
            Outbox.status == "pending",
            Outbox.kind == "listing",
        )
    )
    return result.rowcount


async def revoke_subscription(session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user is not None:
        user.subscription_until = None


async def set_user_active(session: AsyncSession, tg_id: int, active: bool) -> bool:
    result = await session.execute(
        update(User).where(User.tg_id == tg_id).values(is_active=active)
    )
    return result.rowcount > 0


# --- Подписка и лимиты ------------------------------------------------------


def _is_pro(subscription_until: datetime | None) -> bool:
    return subscription_until is not None and subscription_until > datetime.now(UTC)


async def get_quota(session: AsyncSession, user: User) -> Quota:
    used = await session.get(DailyUsage, (user.id, today()))
    return Quota(
        is_pro=_is_pro(user.subscription_until),
        used_today=used.sent if used else 0,
        limit_notified=used.limit_notified if used else False,
    )


async def load_quotas(session: AsyncSession) -> dict[int, Quota]:
    """Квоты всех активных пользователей одним запросом — на цикл опроса."""
    rows = await session.execute(
        select(
            User.id,
            User.subscription_until,
            func.coalesce(DailyUsage.sent, 0),
            func.coalesce(DailyUsage.limit_notified, False),
        )
        .outerjoin(
            DailyUsage,
            (DailyUsage.user_id == User.id) & (DailyUsage.day == today()),
        )
        .where(User.is_active.is_(True))
    )
    return {
        user_id: Quota(
            is_pro=_is_pro(subscription_until),
            used_today=sent,
            limit_notified=notified,
        )
        for user_id, subscription_until, sent, notified in rows
    }


async def bump_usage(session: AsyncSession, spent: dict[int, int]) -> None:
    """Записывает израсходованные за цикл уведомления."""
    if not spent:
        return

    day = today()
    stmt = insert(DailyUsage).values(
        [
            {"user_id": user_id, "day": day, "sent": count}
            for user_id, count in spent.items()
        ]
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[DailyUsage.user_id, DailyUsage.day],
            set_={"sent": DailyUsage.sent + stmt.excluded.sent},
        )
    )


async def mark_limit_notified(session: AsyncSession, user_ids: list[int]) -> None:
    if not user_ids:
        return

    day = today()
    stmt = insert(DailyUsage).values(
        [
            {"user_id": user_id, "day": day, "limit_notified": True}
            for user_id in user_ids
        ]
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[DailyUsage.user_id, DailyUsage.day],
            set_={"limit_notified": True},
        )
    )


async def extend_subscription(
    session: AsyncSession, user_id: int, days: int
) -> datetime:
    """Продлевает подписку. Если она ещё активна, дни добавляются к остатку."""
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"user {user_id} not found")

    now = datetime.now(UTC)
    base = user.subscription_until if _is_pro(user.subscription_until) else now
    user.subscription_until = base + timedelta(days=days)
    # Новый срок — новые напоминания: иначе о следующем окончании мы промолчим.
    user.renewal_notified_at = None
    user.expiry_notified_at = None
    return user.subscription_until


async def users_to_remind(
    session: AsyncSession, days_before: int
) -> list[tuple[int, datetime]]:
    """Подписки, которые заканчиваются в ближайшие дни и о которых ещё не писали."""
    now = datetime.now(UTC)
    rows = await session.execute(
        select(User.id, User.subscription_until).where(
            User.is_active.is_(True),
            User.renewal_notified_at.is_(None),
            User.subscription_until > now,
            User.subscription_until <= now + timedelta(days=days_before),
        )
    )
    return [(user_id, until) for user_id, until in rows]


async def users_just_expired(
    session: AsyncSession, within_days: int = 7
) -> list[int]:
    """Недавно истёкшие подписки.

    Окно нужно, чтобы при первом запуске этой логики не рассылать уведомления
    тем, у кого подписка кончилась полгода назад.
    """
    now = datetime.now(UTC)
    rows = await session.scalars(
        select(User.id).where(
            User.is_active.is_(True),
            User.expiry_notified_at.is_(None),
            User.subscription_until.is_not(None),
            User.subscription_until <= now,
            User.subscription_until > now - timedelta(days=within_days),
        )
    )
    return list(rows)


async def mark_reminded(
    session: AsyncSession, user_ids: list[int], *, expired: bool
) -> None:
    if not user_ids:
        return

    column = "expiry_notified_at" if expired else "renewal_notified_at"
    await session.execute(
        update(User).where(User.id.in_(user_ids)).values(**{column: func.now()})
    )


async def record_payment(
    session: AsyncSession,
    *,
    user_id: int,
    charge_id: str,
    offer_code: str,
    stars: int,
    days: int,
) -> bool:
    """Сохраняет платёж. False — такой charge_id уже был (повторный апдейт)."""
    stmt = (
        insert(Payment)
        .values(
            user_id=user_id,
            charge_id=charge_id,
            offer_code=offer_code,
            stars=stars,
            days=days,
        )
        .on_conflict_do_nothing(index_elements=[Payment.charge_id])
        .returning(Payment.id)
    )
    return await session.scalar(stmt) is not None


async def last_payments(
    session: AsyncSession, user_id: int, limit: int = 5
) -> list[Payment]:
    result = await session.scalars(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.id.desc())
        .limit(limit)
    )
    return list(result)


async def get_payment(session: AsyncSession, charge_id: str) -> Payment | None:
    return await session.scalar(select(Payment).where(Payment.charge_id == charge_id))


async def get_payment_by_id(session: AsyncSession, payment_id: int) -> Payment | None:
    return await session.get(Payment, payment_id)


async def recent_payments(
    session: AsyncSession, limit: int = 10
) -> list[tuple[Payment, int, str | None]]:
    """Последние платежи вместе с Telegram id и именем плательщика."""
    rows = await session.execute(
        select(Payment, User.tg_id, User.username)
        .join(User, User.id == Payment.user_id)
        .order_by(Payment.id.desc())
        .limit(limit)
    )
    return [(payment, tg_id, username) for payment, tg_id, username in rows]


async def user_revenue_stars(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.coalesce(func.sum(Payment.stars), 0)).where(
                Payment.user_id == user_id, Payment.refunded_at.is_(None)
            )
        )
        or 0
    )


async def revenue_stars(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.coalesce(func.sum(Payment.stars), 0)).where(
                Payment.refunded_at.is_(None)
            )
        )
        or 0
    )


async def count_subscribers(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.subscription_until > func.now())
        )
        or 0
    )


# --- Фильтры ----------------------------------------------------------------


async def list_filters(session: AsyncSession, user_id: int) -> list[Filter]:
    result = await session.scalars(
        select(Filter).where(Filter.user_id == user_id).order_by(Filter.id)
    )
    return list(result)


async def count_filters(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(Filter).where(Filter.user_id == user_id)
        )
        or 0
    )


async def add_filter(
    session: AsyncSession,
    user_id: int,
    *,
    query: str = "",
    regions: list[str],
    brand: str | None = None,
    size: str | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
) -> Filter:
    flt = Filter(
        user_id=user_id,
        query=query,
        regions=regions,
        brand=brand,
        size=size,
        price_min=price_min,
        price_max=price_max,
    )
    session.add(flt)
    await session.flush()
    return flt


async def delete_filter(session: AsyncSession, user_id: int, filter_id: int) -> bool:
    result = await session.execute(
        delete(Filter).where(Filter.id == filter_id, Filter.user_id == user_id)
    )
    return result.rowcount > 0


async def active_filters_with_users(
    session: AsyncSession, region: str
) -> list[tuple[int, int, int, FilterSpec]]:
    """Возвращает (filter_id, user_id, tg_id, spec) для фильтров этого региона.

    Отсев по региону делает Postgres: цикл опроса идёт по одной стране, и
    поднимать в память фильтры остальных незачем.
    """
    rows = await session.execute(
        select(
            Filter.id,
            Filter.user_id,
            User.tg_id,
            Filter.query,
            Filter.brand,
            Filter.size,
            Filter.price_min,
            Filter.price_max,
        )
        .join(User, User.id == Filter.user_id)
        .where(
            Filter.is_active.is_(True),
            Filter.regions.any(region),
            User.is_active.is_(True),
            User.paused.is_(False),
        )
    )

    result = []
    for fid, uid, tg_id, query, brand, size, price_min, price_max in rows:
        spec = FilterSpec(
            keywords=tuple(query.casefold().split()) if query else (),
            brand=brand,
            size=size,
            price_min=price_min,
            price_max=price_max,
        )
        result.append((fid, uid, tg_id, spec))
    return result


# --- Дедупликация ленты -----------------------------------------------------


async def register_seen(
    session: AsyncSession, region: str, vinted_ids: list[int]
) -> set[int]:
    """Отмечает объявления как обработанные и возвращает те, что видим впервые.

    Вся дедупликация делается одним запросом на цикл: Postgres сам решает,
    какие строки вставились, и возвращает только их.
    """
    if not vinted_ids:
        return set()

    stmt = (
        insert(SeenItem)
        .values([{"region": region, "vinted_id": vid} for vid in vinted_ids])
        .on_conflict_do_nothing(index_elements=[SeenItem.region, SeenItem.vinted_id])
        .returning(SeenItem.vinted_id)
    )
    result = await session.scalars(stmt)
    return set(result)


async def cleanup_seen(session: AsyncSession, ttl_hours: int = 24) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    result = await session.execute(delete(SeenItem).where(SeenItem.seen_at < cutoff))
    return result.rowcount


async def count_seen(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(SeenItem)) or 0


# --- Объявления -------------------------------------------------------------


async def upsert_listing(session: AsyncSession, listing: DomainListing) -> int:
    """Сохраняет объявление и возвращает его внутренний id.

    Вызывается только для объявлений, совпавших хотя бы с одним фильтром:
    хранить всю ленту целиком нет смысла, она огромная и никому не нужна.
    """
    stmt = (
        insert(Listing)
        .values(
            vinted_id=listing.vinted_id,
            region=listing.region,
            title=listing.title,
            brand=listing.brand,
            size=listing.size,
            condition=listing.condition,
            price=listing.price,
            total_price=listing.total_price,
            currency=listing.currency,
            url=listing.url,
            photo_url=listing.photo_url,
            seller_login=listing.seller_login,
            posted_at=listing.posted_at,
            raw=listing.raw,
        )
        .on_conflict_do_update(
            index_elements=[Listing.vinted_id],
            set_={"fetched_at": func.now()},
        )
        .returning(Listing.id)
    )
    return await session.scalar(stmt)  # type: ignore[return-value]


async def cleanup_old_listings(session: AsyncSession, ttl_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
    result = await session.execute(delete(Listing).where(Listing.fetched_at < cutoff))
    return result.rowcount


# --- Outbox -----------------------------------------------------------------


async def enqueue(
    session: AsyncSession, user_id: int, listing_id: int, filter_id: int
) -> bool:
    """Ставит сообщение в очередь. Повтор для той же пары user/listing игнорируется."""
    stmt = (
        insert(Outbox)
        .values(user_id=user_id, listing_id=listing_id, filter_id=filter_id)
        .on_conflict_do_nothing(constraint="uq_outbox_user_listing")
        .returning(Outbox.id)
    )
    return await session.scalar(stmt) is not None


async def enqueue_limit_notice(session: AsyncSession, user_id: int) -> bool:
    """Ставит в очередь уведомление об исчерпании суточного лимита."""
    stmt = (
        insert(Outbox)
        .values(user_id=user_id, listing_id=None, kind="limit")
        .returning(Outbox.id)
    )
    return await session.scalar(stmt) is not None


async def enqueue_notice(
    session: AsyncSession, user_id: int, kind: str, text: str
) -> None:
    """Служебное сообщение с готовым текстом (напоминания о подписке)."""
    await session.execute(
        insert(Outbox).values(
            user_id=user_id, listing_id=None, kind=kind, payload=text
        )
    )


async def enqueue_broadcast(session: AsyncSession, text: str) -> int:
    """Ставит рассылку в очередь всем активным пользователям одним запросом.

    Через outbox, а не прямой рассылкой: тысяча сообщений подряд гарантированно
    упрётся в лимиты Telegram, а здесь уже есть и ретраи, и темп отправки.
    """
    stmt = insert(Outbox).from_select(
        ["user_id", "kind", "payload"],
        select(User.id, literal("broadcast"), literal(text)).where(
            User.is_active.is_(True)
        ),
    )
    result = await session.execute(stmt)
    return result.rowcount


async def claim_pending(
    session: AsyncSession, limit: int
) -> list[tuple[Outbox, Listing | None, int]]:
    """Забирает пачку неотправленных сообщений.

    SKIP LOCKED оставлен на будущее: сейчас процесс один, но когда отправщик
    переедет в отдельный контейнер, менять запрос уже не придётся.
    """
    rows = await session.execute(
        select(Outbox, Listing, User.tg_id)
        .outerjoin(Listing, Listing.id == Outbox.listing_id)
        .join(User, User.id == Outbox.user_id)
        .where(
            Outbox.status == "pending",
            Outbox.next_attempt_at <= func.now(),
            User.is_active.is_(True),
        )
        .order_by(Outbox.id)
        .limit(limit)
        .with_for_update(of=Outbox, skip_locked=True)
    )
    return [(outbox, listing, tg_id) for outbox, listing, tg_id in rows]


async def mark_sent(session: AsyncSession, outbox_id: int) -> None:
    await session.execute(
        update(Outbox)
        .where(Outbox.id == outbox_id)
        .values(status="sent", sent_at=func.now())
    )


async def mark_failed(
    session: AsyncSession, outbox_id: int, error: str, *, retry_in: timedelta | None
) -> None:
    values: dict[str, Any] = {
        "attempts": Outbox.attempts + 1,
        "last_error": error[:500],
    }
    if retry_in is None:
        values["status"] = "failed"
    else:
        values["next_attempt_at"] = datetime.now(UTC) + retry_in
    await session.execute(update(Outbox).where(Outbox.id == outbox_id).values(**values))


async def outbox_depth(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.status == "pending")
        )
        or 0
    )


# --- Служебное состояние ----------------------------------------------------


async def get_state(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(select(AppState.value).where(AppState.key == key))


async def set_state(session: AsyncSession, key: str, value: str) -> None:
    stmt = (
        insert(AppState)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[AppState.key], set_={"value": value, "updated_at": func.now()}
        )
    )
    await session.execute(stmt)
