"""Справочник площадок Vinted по странам.

Vinted — это не один сайт, а десяток национальных порталов со своими лентами.
Регион хранится кодом, а не доменом: домен может измениться, а код уже разъехался
бы по фильтрам всех пользователей.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Region:
    code: str
    title: str
    flag: str
    domain: str
    currency: str

    @property
    def label(self) -> str:
        return f"{self.flag} {self.title}"


REGIONS: tuple[Region, ...] = (
    Region("de", "Германия", "🇩🇪", "www.vinted.de", "EUR"),
    Region("fr", "Франция", "🇫🇷", "www.vinted.fr", "EUR"),
    Region("pl", "Польша", "🇵🇱", "www.vinted.pl", "PLN"),
    Region("it", "Италия", "🇮🇹", "www.vinted.it", "EUR"),
    Region("es", "Испания", "🇪🇸", "www.vinted.es", "EUR"),
    Region("uk", "Великобритания", "🇬🇧", "www.vinted.co.uk", "GBP"),
    Region("nl", "Нидерланды", "🇳🇱", "www.vinted.nl", "EUR"),
    Region("be", "Бельгия", "🇧🇪", "www.vinted.be", "EUR"),
    Region("at", "Австрия", "🇦🇹", "www.vinted.at", "EUR"),
    Region("cz", "Чехия", "🇨🇿", "www.vinted.cz", "CZK"),
    Region("sk", "Словакия", "🇸🇰", "www.vinted.sk", "EUR"),
    Region("lt", "Литва", "🇱🇹", "www.vinted.lt", "EUR"),
    Region("pt", "Португалия", "🇵🇹", "www.vinted.pt", "EUR"),
    Region("se", "Швеция", "🇸🇪", "www.vinted.se", "SEK"),
    Region("fi", "Финляндия", "🇫🇮", "www.vinted.fi", "EUR"),
    Region("dk", "Дания", "🇩🇰", "www.vinted.dk", "DKK"),
    Region("hu", "Венгрия", "🇭🇺", "www.vinted.hu", "HUF"),
    Region("ro", "Румыния", "🇷🇴", "www.vinted.ro", "RON"),
    Region("gr", "Греция", "🇬🇷", "www.vinted.gr", "EUR"),
    Region("ie", "Ирландия", "🇮🇪", "www.vinted.ie", "EUR"),
    Region("lu", "Люксембург", "🇱🇺", "www.vinted.lu", "EUR"),
    Region("com", "США", "🇺🇸", "www.vinted.com", "USD"),
)

BY_CODE: dict[str, Region] = {region.code: region for region in REGIONS}


def get(code: str) -> Region | None:
    return BY_CODE.get(code)


def labels(codes: list[str] | tuple[str, ...]) -> str:
    """Читаемый список регионов для описания фильтра."""
    known = [BY_CODE[code].label for code in codes if code in BY_CODE]
    return ", ".join(known) if known else "не выбраны"


def currencies(codes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Валюты выбранных регионов — цену в фильтре нужно понимать в них."""
    seen: list[str] = []
    for code in codes:
        region = BY_CODE.get(code)
        if region is not None and region.currency not in seen:
            seen.append(region.currency)
    return tuple(seen)
