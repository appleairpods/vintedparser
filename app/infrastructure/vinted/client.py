"""Единственная точка входа в Vinted.

Весь остальной код знает только про интерфейс VintedClient. Это сделано
намеренно: DataDome обновляется несколько раз в год и ломает именно этот
модуль, а домен, БД и бот при этом меняться не должны. По той же причине
сюда легко подставить платный managed-скрапер, не трогая остальное.
"""

import asyncio
import random
import time
from typing import Any, Protocol

import structlog
from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from app.domain.models import Listing
from app.domain.regions import Region
from app.infrastructure.vinted.normalizer import normalize_batch

log = structlog.get_logger(__name__)

SESSION_TTL_SECONDS = 20 * 60
PER_PAGE = 96
IMPERSONATE = "chrome"


class VintedAccessError(RuntimeError):
    """Vinted не отдал данные: истёкшая сессия, бан, челлендж или сеть."""


def _drop_duplicates(listings: list[Listing]) -> list[Listing]:
    """Убирает повторы между страницами.

    Пока мы запрашиваем вторую страницу, лента успевает сдвинуться, и часть
    объявлений приходит дважды. На замерах это около четверти выдачи.
    """
    seen: set[int] = set()
    unique = []
    for listing in listings:
        if listing.vinted_id in seen:
            continue
        seen.add(listing.vinted_id)
        unique.append(listing)
    return unique


class VintedClient(Protocol):
    region: str

    async def fetch_new(self, pages: int = 1) -> list[Listing]: ...
    async def close(self) -> None: ...


class HttpVintedClient:
    def __init__(
        self,
        region: Region,
        *,
        proxies: dict[str, str] | None = None,
        min_delay: float = 1.0,
        max_delay: float = 2.5,
    ) -> None:
        self.region = region.code
        self._domain = region.domain
        self._proxies = proxies
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._session: AsyncSession | None = None
        self._session_born_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def domain(self) -> str:
        return self._domain

    def _headers(self) -> dict[str, str]:
        # Sec-Fetch-* и Referer убеждают edge Vinted, что это same-origin XHR,
        # а не внешний запрос. Без них ответ заметно чаще оказывается 403.
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://{self._domain}/",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    async def _new_session(self) -> AsyncSession:
        if self._session is not None:
            await self._close_session()

        session = AsyncSession(
            impersonate=IMPERSONATE,
            timeout=30,
            proxies=self._proxies,
        )
        try:
            response = await session.get(f"https://{self._domain}/", allow_redirects=True)
        except RequestException as exc:
            await session.close()
            raise VintedAccessError(f"не удалось открыть главную: {exc}") from exc

        if response.status_code != 200:
            await session.close()
            raise VintedAccessError(f"главная вернула {response.status_code}")

        cookies = {c.name for c in session.cookies.jar}
        if "access_token_web" not in cookies:
            await session.close()
            raise VintedAccessError("главная не выдала access_token_web")

        self._session = session
        self._session_born_at = time.monotonic()
        log.info("vinted.session.created", domain=self._domain)
        return session

    async def _close_session(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001 - закрытие не должно ронять цикл
                log.warning("vinted.session.close_failed", exc_info=True)
            self._session = None

    async def _ensure_session(self) -> AsyncSession:
        expired = time.monotonic() - self._session_born_at > SESSION_TTL_SECONDS
        if self._session is None or expired:
            return await self._new_session()
        return self._session

    async def _get_page(self, page: int) -> list[dict[str, Any]]:
        session = await self._ensure_session()
        url = f"https://{self._domain}/api/v2/catalog/items"
        params = {"page": page, "per_page": PER_PAGE, "order": "newest_first"}

        try:
            response = await session.get(url, params=params, headers=self._headers())
        except RequestException as exc:
            raise VintedAccessError(f"сетевая ошибка: {exc}") from exc

        if response.status_code in (401, 403):
            raise VintedAccessError(f"доступ закрыт: {response.status_code}")
        if response.status_code != 200:
            raise VintedAccessError(f"неожиданный статус: {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise VintedAccessError("ответ не является JSON") from exc

        items = payload.get("items")
        if not isinstance(items, list):
            raise VintedAccessError("в ответе нет списка items")
        return items

    async def fetch_new(self, pages: int = 1) -> list[Listing]:
        """Забирает свежие объявления из общей ленты.

        Запрос не зависит от фильтров пользователей: их мы применяем локально,
        поэтому нагрузка на Vinted не растёт вместе с числом пользователей.
        """
        async with self._lock:
            raw: list[dict[str, Any]] = []
            for page in range(1, pages + 1):
                try:
                    raw.extend(await self._get_page(page))
                except VintedAccessError as exc:
                    # Одна повторная попытка с новой сессией: типичная причина —
                    # протухший токен, и она лечится именно пересозданием.
                    log.warning("vinted.fetch.retry", page=page, reason=str(exc))
                    await self._close_session()
                    self._session_born_at = 0.0
                    raw.extend(await self._get_page(page))

                if page < pages:
                    await asyncio.sleep(random.uniform(self._min_delay, self._max_delay))

            normalized = normalize_batch(
                raw, domain=self._domain, region=self.region
            )
            return _drop_duplicates(normalized)

    async def close(self) -> None:
        async with self._lock:
            await self._close_session()
