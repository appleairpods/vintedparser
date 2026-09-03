from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Пауза — это выбор пользователя, а is_active снимаем мы (бан, блокировка
    # бота). Одним полем их не описать: сняв бан, мы вернули бы уведомления и
    # тому, кто сам их выключил.
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Тариф не храним отдельным полем: он однозначно выводится из даты окончания
    # подписки, а denormalized-копия рано или поздно разъедется с реальностью.
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Отметки об уже отправленных напоминаниях. Сбрасываются при продлении,
    # поэтому следующий цикл подписки снова получит и то, и другое.
    renewal_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Filter(Base):
    __tablename__ = "filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Массивом, а не строкой: по нему идёт выборка фильтров на каждом цикле
    # опроса, и `code = ANY(regions)` индексируется, в отличие от LIKE.
    regions: Mapped[list[str]] = mapped_column(ARRAY(String(8)))
    brand: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[str | None] = mapped_column(String(64))
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    vinted_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    region: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[str | None] = mapped_column(String(64))
    condition: Mapped[str | None] = mapped_column(String(64))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8))
    url: Mapped[str] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)
    seller_login: Mapped[str | None] = mapped_column(String(128))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SeenItem(Base):
    """Идентификаторы уже обработанных объявлений.

    Лента Vinted не отсортирована ни по id, ни по дате: у объявления,
    выложенного позже, id вполне может оказаться меньше. Поэтому курсор по
    максимальному id терял бы часть объявлений, и вместо него — множество
    виденных id со сроком жизни в сутки.
    """

    __tablename__ = "seen_items"

    # Ключ составной: одно и то же объявление видно в лентах нескольких стран,
    # и общий на всех ключ означал бы, что первая же страна «съедает» его для
    # остальных, а их подписчики ничего не получат.
    region: Mapped[str] = mapped_column(String(8), primary_key=True)
    # autoincrement=False: id приходит от Vinted, своя последовательность не нужна
    vinted_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", name="uq_outbox_user_listing"),
        Index("ix_outbox_pending", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Пусто у служебных сообщений вроде «лимит исчерпан»
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(
        String(16), default="listing", server_default="listing"
    )
    # Готовый текст для рассылки: у неё нет объявления, из которого его собрать
    payload: Mapped[str | None] = mapped_column(Text)
    filter_id: Mapped[int | None] = mapped_column(
        ForeignKey("filters.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyUsage(Base):
    """Сколько уведомлений пользователь получил за календарные сутки (UTC)."""

    __tablename__ = "daily_usage"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Чтобы не напоминать про лимит на каждое пропущенное объявление
    limit_notified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class Payment(Base):
    """Журнал успешных оплат. Нужен для возвратов и разбора спорных случаев."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Идентификатор платежа в Telegram: по нему делается возврат, и он же
    # защищает от повторного начисления, если апдейт придёт дважды.
    charge_id: Mapped[str] = mapped_column(String(128), unique=True)
    offer_code: Mapped[str] = mapped_column(String(16))
    stars: Mapped[int] = mapped_column(Integer)
    days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppState(Base):
    """Маленький key-value для служебных отметок (heartbeat, курсоры)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
