"""Сырой JSON Vinted -> доменная модель Listing.

Схема эндпоинта недокументирована и меняется без предупреждения, поэтому здесь
всё читается защитно, а исходный объект целиком сохраняется в Listing.raw.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.models import Listing


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money(node: Any) -> tuple[Decimal | None, str | None]:
    if not isinstance(node, dict):
        return None, None
    return _decimal(node.get("amount")), node.get("currency_code")


def _posted_at(item: dict[str, Any]) -> datetime | None:
    """У объявлений в каталоге нет даты публикации.

    Ближайший доступный ориентир — timestamp загрузки главного фото.
    """
    photo = item.get("photo")
    if not isinstance(photo, dict):
        return None
    high_res = photo.get("high_resolution")
    if not isinstance(high_res, dict):
        return None
    ts = high_res.get("timestamp")
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _photo_url(item: dict[str, Any]) -> str | None:
    photo = item.get("photo")
    if isinstance(photo, dict) and photo.get("url"):
        return str(photo["url"])
    photos = item.get("photos")
    if isinstance(photos, list) and photos and isinstance(photos[0], dict):
        return photos[0].get("url")
    return None


def normalize_item(item: dict[str, Any], *, domain: str, region: str) -> Listing | None:
    vinted_id = item.get("id")
    if not isinstance(vinted_id, int):
        return None

    price, currency = _money(item.get("price"))
    if price is None:
        return None

    url = item.get("url")
    if not url:
        path = item.get("path")
        url = f"https://{domain}{path}" if path else f"https://{domain}/items/{vinted_id}"

    total_price, _ = _money(item.get("total_item_price"))
    user = item.get("user") if isinstance(item.get("user"), dict) else {}

    return Listing(
        vinted_id=vinted_id,
        title=str(item.get("title") or "").strip() or "(без названия)",
        url=str(url),
        price=price,
        currency=currency or "EUR",
        region=region,
        brand=(item.get("brand_title") or None),
        size=(item.get("size_title") or None),
        condition=(item.get("status") or None),
        photo_url=_photo_url(item),
        seller_login=(user.get("login") or None),
        total_price=total_price,
        posted_at=_posted_at(item),
        raw=item,
    )


def normalize_batch(
    items: list[dict[str, Any]], *, domain: str, region: str
) -> list[Listing]:
    result = []
    for item in items:
        listing = normalize_item(item, domain=domain, region=region)
        if listing is not None:
            result.append(listing)
    return result
