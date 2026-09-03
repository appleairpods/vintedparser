from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain import regions as regions_catalog
from app.domain.plans import Offer
from app.domain.regions import Region


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    admin_ids: str = ""

    database_url: str = "postgresql+psycopg://vinted:vinted@localhost:5432/vinted"

    # Какие национальные площадки опрашиваем. Каждая — отдельный цикл опроса,
    # поэтому список стоит держать коротким: нагрузка растёт линейно.
    vinted_regions: str = "de,fr,pl,com"
    poll_interval: int = 4
    poll_pages: int = 1
    vinted_proxy: str = ""

    # Цены подписки в звёздах Telegram
    sub_stars_30: int = 150
    sub_stars_90: int = 350

    listing_ttl_days: int = 7
    # Насколько старым может быть объявление, чтобы о нём ещё имело смысл
    # уведомлять. Заодно страхует от повторной рассылки, если объявление
    # всплывёт в ленте после того, как истечёт его запись в seen_items.
    max_listing_age_minutes: int = 60
    seen_ttl_hours: int = 24

    log_level: str = "INFO"

    @cached_property
    def admins(self) -> set[int]:
        return {int(part) for part in self.admin_ids.replace(" ", "").split(",") if part}

    @cached_property
    def regions(self) -> tuple[Region, ...]:
        codes = [part for part in self.vinted_regions.replace(" ", "").split(",") if part]
        known = tuple(
            region for code in codes if (region := regions_catalog.get(code)) is not None
        )
        # Пустой список означал бы бота, который ничего не опрашивает, — это
        # почти наверняка опечатка в .env, а не осознанный выбор.
        return known or (regions_catalog.BY_CODE["com"],)

    @cached_property
    def region_codes(self) -> tuple[str, ...]:
        return tuple(region.code for region in self.regions)

    @property
    def default_region(self) -> str:
        return self.regions[0].code

    @cached_property
    def offers(self) -> tuple[Offer, ...]:
        return (
            Offer(code="30d", days=30, stars=self.sub_stars_30, title="1 месяц"),
            Offer(code="90d", days=90, stars=self.sub_stars_90, title="3 месяца"),
        )

    def offer_by_code(self, code: str) -> Offer | None:
        return next((offer for offer in self.offers if offer.code == code), None)

    @cached_property
    def proxies(self) -> dict[str, str] | None:
        if not self.vinted_proxy:
            return None
        return {"http": self.vinted_proxy, "https": self.vinted_proxy}


settings = Settings()  # type: ignore[call-arg]
