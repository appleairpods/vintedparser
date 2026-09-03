from decimal import Decimal

import pytest

from app.application.filter_parser import FilterParseError, parse_price_range


def test_bare_number_means_upper_bound():
    assert parse_price_range("50") == (None, Decimal("50"))


def test_range():
    assert parse_price_range("10-50") == (Decimal("10"), Decimal("50"))


def test_open_bounds():
    assert parse_price_range("-50") == (None, Decimal("50"))
    assert parse_price_range("20-") == (Decimal("20"), None)


def test_empty_input_means_no_limit():
    assert parse_price_range("  ") == (None, None)


def test_comma_is_accepted_as_separator():
    assert parse_price_range("10,5") == (None, Decimal("10.5"))


def test_reversed_range_is_rejected():
    with pytest.raises(FilterParseError):
        parse_price_range("50-10")


def test_garbage_is_rejected():
    with pytest.raises(FilterParseError):
        parse_price_range("дёшево")
