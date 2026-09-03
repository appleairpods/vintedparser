"""Тарифы и лимиты.

Чистые данные и правила без обращений к БД: так лимиты видно в одном месте,
а не размазанными по хендлерам.
"""

from dataclasses import dataclass

UNLIMITED = None


@dataclass(frozen=True, slots=True)
class Plan:
    code: str
    title: str
    daily_notifications: int | None
    max_filters: int

    @property
    def is_unlimited(self) -> bool:
        return self.daily_notifications is UNLIMITED


FREE = Plan(
    code="free",
    title="Бесплатный",
    daily_notifications=10,
    max_filters=1,
)

PRO = Plan(
    code="pro",
    title="Pro",
    daily_notifications=UNLIMITED,
    max_filters=10,
)


@dataclass(frozen=True, slots=True)
class Offer:
    """Вариант оплаты подписки. Цена — в звёздах Telegram (валюта XTR)."""

    code: str
    days: int
    stars: int
    title: str

    @property
    def payload(self) -> str:
        return f"sub:{self.code}"


def plan_for(is_pro: bool) -> Plan:
    return PRO if is_pro else FREE


def remaining_notifications(plan: Plan, used_today: int) -> int | None:
    """Сколько уведомлений ещё доступно сегодня. None — безлимит."""
    if plan.is_unlimited:
        return UNLIMITED
    return max(plan.daily_notifications - used_today, 0)


@dataclass(slots=True)
class Quota:
    """Состояние суточного лимита одного пользователя.

    Изменяемый объект: цикл опроса поднимает квоты всех пользователей один раз,
    а дальше списывает их в памяти, чтобы не ходить в БД на каждое совпадение.
    """

    is_pro: bool
    used_today: int = 0
    limit_notified: bool = False
    spent: int = 0

    @property
    def plan(self) -> Plan:
        return plan_for(self.is_pro)

    @property
    def remaining(self) -> int | None:
        return remaining_notifications(self.plan, self.used_today)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining == 0

    def take(self) -> bool:
        """Пытается списать одно уведомление. False — лимит исчерпан."""
        if self.is_exhausted:
            return False
        self.used_today += 1
        self.spent += 1
        return True
