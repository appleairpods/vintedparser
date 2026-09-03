from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain import regions


@dataclass(slots=True)
class Listing:
    """Объявление Vinted, приведённое к нашему виду."""

    vinted_id: int
    title: str
    url: str
    price: Decimal
    currency: str
    region: str
    brand: str | None = None
    size: str | None = None
    condition: str | None = None
    photo_url: str | None = None
    seller_login: str | None = None
    total_price: Decimal | None = None
    posted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def searchable_text(self) -> str:
        parts = [self.title, self.brand or "", self.size or ""]
        return " ".join(parts).casefold()


@dataclass(slots=True)
class FilterSpec:
    """Пользовательский фильтр в доменном виде.

    Пустые поля означают «не ограничивать», а не «искать пустое значение».
    """

    keywords: tuple[str, ...] = ()
    brand: str | None = None
    size: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    regions: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Регионы намеренно не считаются условием.

        Иначе фильтр, где выбрана только страна, подошёл бы каждому объявлению
        в ней — то есть всей ленте.
        """
        return not any(
            (self.keywords, self.brand, self.size, self.price_min, self.price_max)
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.keywords:
            parts.append(" ".join(self.keywords))
        if self.brand:
            parts.append(f"бренд: {self.brand}")
        if self.size:
            parts.append(f"размер: {self.size}")
        if self.price_min is not None or self.price_max is not None:
            low = f"{self.price_min:g}" if self.price_min is not None else "0"
            high = f"{self.price_max:g}" if self.price_max is not None else "∞"
            parts.append(f"цена: {low}–{high}")
        if self.regions:
            parts.append(f"регионы: {regions.labels(self.regions)}")
        return "; ".join(parts) if parts else "без ограничений"
