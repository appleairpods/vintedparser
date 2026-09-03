from decimal import Decimal

import pytest

from app.application.filter_parser import FilterParseError, parse_filter


def test_plain_keywords():
    parsed = parse_filter("nike air max")
    assert parsed.query == "nike air max"
    assert parsed.brand is None


def test_price_range():
    parsed = parse_filter("куртка price:10-50")
    assert parsed.query == "куртка"
    assert parsed.price_min == Decimal("10")
    assert parsed.price_max == Decimal("50")


def test_price_open_bounds():
    assert parse_filter("price:-50").price_min is None
    assert parse_filter("price:-50").price_max == Decimal("50")
    assert parse_filter("price:20-").price_min == Decimal("20")
    assert parse_filter("price:20-").price_max is None


def test_price_accepts_comma_decimal():
    assert parse_filter("price:10,5-20").price_min == Decimal("10.5")


def test_brand_and_size():
    parsed = parse_filter("brand:Zara size:M")
    assert parsed.brand == "Zara"
    assert parsed.size == "M"
    assert parsed.query == ""


def test_quoted_multiword_brand():
    assert parse_filter('brand:"The North Face"').brand == "The North Face"


def test_unknown_modifier_falls_back_to_keywords():
    """Незнакомый префикс лучше искать как текст, чем ронять команду ошибкой."""
    assert parse_filter("color:red").query == "color:red"


def test_empty_filter_is_rejected():
    with pytest.raises(FilterParseError):
        parse_filter("   ")


def test_inverted_price_range_is_rejected():
    with pytest.raises(FilterParseError, match="больше верхней"):
        parse_filter("price:50-10")


def test_malformed_price_is_rejected():
    with pytest.raises(FilterParseError, match="диапазон цены"):
        parse_filter("price:abc")


def test_negative_price_is_rejected():
    with pytest.raises(FilterParseError):
        parse_filter("price:5--10")
