from decimal import Decimal

from app.domain import regions
from app.domain.models import FilterSpec


def test_every_region_has_unique_code_and_domain():
    codes = [region.code for region in regions.REGIONS]
    domains = [region.domain for region in regions.REGIONS]

    assert len(codes) == len(set(codes))
    assert len(domains) == len(set(domains))


def test_lookup_by_code():
    assert regions.get("de").domain == "www.vinted.de"
    assert regions.get("нет такого") is None


def test_labels_skip_unknown_codes():
    assert regions.labels(("de", "мусор")) == "🇩🇪 Германия"
    assert regions.labels(()) == "не выбраны"


def test_currencies_are_deduplicated_and_ordered():
    # Германия и Франция обе в евро — валюта не должна повторяться
    assert regions.currencies(("de", "fr", "pl")) == ("EUR", "PLN")


def test_regions_alone_do_not_make_filter_meaningful():
    """Фильтр, где выбрана только страна, подошёл бы всей её ленте."""
    assert FilterSpec(regions=("de",)).is_empty is True
    assert FilterSpec(regions=("de",), price_max=Decimal("10")).is_empty is False


def test_describe_mentions_regions():
    spec = FilterSpec(keywords=("nike",), regions=("de", "pl"))

    assert "🇩🇪 Германия" in spec.describe()
    assert "🇵🇱 Польша" in spec.describe()
