"""Сопоставление объявлений с пользовательскими фильтрами.

Здесь нет обращений к сети и БД: чистые функции, которые легко тестировать.
"""

import re

from app.domain.models import FilterSpec, Listing

_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    return _WORD_RE.sub(" ", text.casefold()).strip()


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(word for word in normalize(text).split() if word)


def matches(spec: FilterSpec, listing: Listing) -> bool:
    """Пустой фильтр не пропускает ничего.

    Иначе один недозаполненный фильтр рассылал бы пользователю всю ленту Vinted.
    """
    if spec.is_empty:
        return False

    if spec.keywords:
        haystack = normalize(listing.searchable_text)
        haystack_words = set(haystack.split())
        for raw_keyword in spec.keywords:
            keyword = normalize(raw_keyword)
            if not keyword:
                continue
            # Точное совпадение слова либо вхождение подстроки: пользователи
            # пишут и "nike", и "airmax" там, где в заголовке "air max".
            if keyword in haystack_words or keyword in haystack:
                continue
            return False

    if spec.brand:
        if not listing.brand:
            return False
        if normalize(spec.brand) != normalize(listing.brand):
            return False

    if spec.size:
        if not listing.size:
            return False
        if normalize(spec.size) != normalize(listing.size):
            return False

    if spec.price_min is not None and listing.price < spec.price_min:
        return False

    return spec.price_max is None or listing.price <= spec.price_max
