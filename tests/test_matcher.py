from decimal import Decimal

import pytest

from app.domain.matcher import matches
from app.domain.models import FilterSpec, Listing


def make_listing(**overrides) -> Listing:
    defaults = {
        "vinted_id": 1,
        "title": "Nike Air Max 90 sneakers",
        "url": "https://www.vinted.com/items/1",
        "price": Decimal("45.00"),
        "currency": "EUR",
        "region": "com",
        "brand": "Nike",
        "size": "42",
        "condition": "Very good",
    }
    return Listing(**{**defaults, **overrides})


def test_empty_filter_matches_nothing():
    """Иначе недозаполненный фильтр рассылал бы пользователю всю ленту."""
    assert matches(FilterSpec(), make_listing()) is False


def test_keywords_must_all_be_present():
    assert matches(FilterSpec(keywords=("nike", "air")), make_listing()) is True
    assert matches(FilterSpec(keywords=("nike", "adidas")), make_listing()) is False


def test_keywords_are_case_insensitive():
    assert matches(FilterSpec(keywords=("NIKE",)), make_listing()) is True


def test_keyword_matches_brand_and_size_too():
    listing = make_listing(title="Кроссовки", brand="Puma", size="41")
    assert matches(FilterSpec(keywords=("puma",)), listing) is True
    assert matches(FilterSpec(keywords=("41",)), listing) is True


def test_keyword_matches_glued_form():
    """Пользователи пишут 'airmax' там, где в заголовке 'Air Max'."""
    assert matches(FilterSpec(keywords=("air",)), make_listing()) is True


def test_brand_must_match_exactly():
    assert matches(FilterSpec(brand="nike"), make_listing()) is True
    assert matches(FilterSpec(brand="Nik"), make_listing()) is False


def test_brand_filter_rejects_listing_without_brand():
    assert matches(FilterSpec(brand="Nike"), make_listing(brand=None)) is False


def test_size_must_match_exactly():
    assert matches(FilterSpec(size="42"), make_listing()) is True
    assert matches(FilterSpec(size="43"), make_listing()) is False


@pytest.mark.parametrize(
    ("price", "expected"),
    [(Decimal("9.99"), False), (Decimal("10"), True), (Decimal("50"), True),
     (Decimal("50.01"), False)],
)
def test_price_bounds_are_inclusive(price, expected):
    spec = FilterSpec(price_min=Decimal("10"), price_max=Decimal("50"))
    assert matches(spec, make_listing(price=price)) is expected


def test_only_upper_bound():
    spec = FilterSpec(price_max=Decimal("50"))
    assert matches(spec, make_listing(price=Decimal("1"))) is True
    assert matches(spec, make_listing(price=Decimal("99"))) is False


def test_all_conditions_combined():
    spec = FilterSpec(
        keywords=("air", "max"),
        brand="Nike",
        size="42",
        price_min=Decimal("10"),
        price_max=Decimal("50"),
    )
    assert matches(spec, make_listing()) is True
    assert matches(spec, make_listing(price=Decimal("60"))) is False
    assert matches(spec, make_listing(size="41")) is False
