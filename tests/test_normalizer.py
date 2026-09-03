"""Тесты на разбор ответа Vinted.

Фикстура — реальный объект из /api/v2/catalog/items. Если Vinted поменяет
схему, эти тесты покраснеют раньше, чем бот начнёт молча слать пустые карточки.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.infrastructure.vinted.normalizer import normalize_batch, normalize_item

FIXTURE = Path(__file__).parent / "fixtures" / "catalog_item.json"
DOMAIN = "www.vinted.com"
REGION = "com"


@pytest.fixture
def raw_item() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_maps_core_fields(raw_item):
    listing = normalize_item(raw_item, domain=DOMAIN, region=REGION)

    assert listing is not None
    assert listing.vinted_id == 9618030294
    assert listing.title == "New Squeeze cheese"
    assert listing.price == Decimal("11.0")
    assert listing.currency == "USD"
    assert listing.brand == "Squishy"
    assert listing.size == "One size"
    assert listing.condition == "New with tags"
    assert listing.seller_login == "aldunc0622"
    assert listing.total_price == Decimal("12.25")
    assert listing.url.startswith("https://www.vinted.com/items/")
    assert listing.photo_url is not None


def test_posted_at_comes_from_photo_timestamp(raw_item):
    """В каталоге нет даты публикации, ближайший ориентир — время загрузки фото."""
    listing = normalize_item(raw_item, domain=DOMAIN, region=REGION)
    assert listing.posted_at == datetime.fromtimestamp(1786291804, tz=UTC)


def test_url_is_built_from_path_when_missing(raw_item):
    raw_item.pop("url")
    listing = normalize_item(raw_item, domain=DOMAIN, region=REGION)
    assert listing.url == "https://www.vinted.com/items/9618030294-new-squeeze-cheese"


def test_item_without_id_is_skipped(raw_item):
    raw_item["id"] = None
    assert normalize_item(raw_item, domain=DOMAIN, region=REGION) is None


def test_item_without_price_is_skipped(raw_item):
    raw_item["price"] = None
    assert normalize_item(raw_item, domain=DOMAIN, region=REGION) is None


def test_missing_optional_blocks_do_not_crash(raw_item):
    for key in ("photo", "user", "total_item_price", "brand_title", "size_title"):
        raw_item.pop(key, None)

    listing = normalize_item(raw_item, domain=DOMAIN, region=REGION)

    assert listing is not None
    assert listing.brand is None
    assert listing.photo_url is None
    assert listing.posted_at is None


def test_batch_drops_broken_items_and_keeps_good_ones(raw_item):
    listings = normalize_batch(
        [raw_item, {"id": "не число"}], domain=DOMAIN, region=REGION
    )
    assert len(listings) == 1


def test_searchable_text_includes_brand_and_size(raw_item):
    listing = normalize_item(raw_item, domain=DOMAIN, region=REGION)
    assert "squishy" in listing.searchable_text
    assert "one size" in listing.searchable_text
