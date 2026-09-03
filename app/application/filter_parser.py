"""Разбор текста команды /add в параметры фильтра.

Синтаксис намеренно однострочный, без пошагового диалога: так пользователь
заводит фильтр одним сообщением, а нам не нужно хранить состояние FSM.

    /add nike air max price:10-50 brand:Nike size:42
"""

import re
import shlex
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

PRICE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)?-(\d+(?:[.,]\d+)?)?$")
MAX_QUERY_LEN = 200


class FilterParseError(ValueError):
    pass


@dataclass(slots=True)
class ParsedFilter:
    query: str = ""
    brand: str | None = None
    size: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.query, self.brand, self.size, self.price_min, self.price_max)
        )


def _to_decimal(raw: str, field: str) -> Decimal:
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation as exc:
        raise FilterParseError(f"не понял число в «{field}»: {raw}") from exc
    if value < 0:
        raise FilterParseError("цена не может быть отрицательной")
    return value


def _parse_price(raw: str, parsed: ParsedFilter) -> None:
    match = PRICE_RE.match(raw)
    if not match:
        raise FilterParseError(
            f"не понял диапазон цены «{raw}». Ожидаю price:10-50, price:-50 или price:10-"
        )
    low, high = match.group(1), match.group(2)
    if low:
        parsed.price_min = _to_decimal(low, "price")
    if high:
        parsed.price_max = _to_decimal(high, "price")
    if (
        parsed.price_min is not None
        and parsed.price_max is not None
        and parsed.price_min > parsed.price_max
    ):
        raise FilterParseError("нижняя граница цены больше верхней")


def parse_price_range(raw: str) -> tuple[Decimal | None, Decimal | None]:
    """Разбирает цену из пошагового диалога: «10-50», «-50», «20-» или «50»."""
    text = raw.strip().replace(" ", "")
    if not text:
        return None, None

    parsed = ParsedFilter()
    # Одно число без дефиса читаем как «не дороже»: это то, что имеют в виду
    # в 9 случаях из 10, когда пишут в ответ на вопрос о цене.
    _parse_price(text if "-" in text else f"-{text}", parsed)
    return parsed.price_min, parsed.price_max


def parse_filter(text: str) -> ParsedFilter:
    try:
        tokens = shlex.split(text.strip())
    except ValueError as exc:
        raise FilterParseError(f"не смог разобрать строку: {exc}") from exc

    parsed = ParsedFilter()
    words: list[str] = []

    for token in tokens:
        key, sep, value = token.partition(":")
        key_lower = key.casefold()

        if not sep or not value:
            words.append(token)
            continue

        if key_lower == "price":
            _parse_price(value, parsed)
        elif key_lower == "brand":
            parsed.brand = value
        elif key_lower == "size":
            parsed.size = value
        else:
            words.append(token)

    parsed.query = " ".join(words)[:MAX_QUERY_LEN].strip()

    if parsed.is_empty:
        raise FilterParseError(
            "фильтр пустой. Укажи хотя бы ключевые слова, бренд, размер или цену"
        )

    return parsed
