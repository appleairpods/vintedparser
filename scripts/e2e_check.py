"""Сквозная проверка: Vinted -> дедуп -> матчинг -> очередь отправки.

Заводит временного пользователя с широким фильтром, прогоняет два цикла опроса
и показывает, что попало в outbox. Пользователя за собой убирает.

Запуск: .venv\\Scripts\\python.exe -m scripts.e2e_check [пауза_сек]
"""

import asyncio
import sys
from decimal import Decimal

from sqlalchemy import delete, func, select

from app.application.poll import run_cycle
from app.config import settings
from app.infrastructure.db import repo
from app.infrastructure.db.base import engine, get_session
from app.infrastructure.db.models import Listing, Outbox, User
from app.infrastructure.telegram.sender import render
from app.infrastructure.vinted.client import HttpVintedClient
from app.logging_setup import setup_logging
from app.utils import run_async

TEST_TG_ID = -999_000_001
PAUSE = int(sys.argv[1]) if len(sys.argv) > 1 else 45


async def setup_user(region: str) -> int:
    async with get_session() as session:
        await session.execute(delete(User).where(User.tg_id == TEST_TG_ID))
        user = await repo.get_or_create_user(session, TEST_TG_ID, "e2e_test")
        await repo.add_filter(
            session, user.id, regions=[region], price_max=Decimal("1000")
        )
        # Бесплатный тариф отдал бы всего 10 объявлений, и проверка конвейера
        # уперлась бы в квоту вместо реальных проблем.
        await repo.extend_subscription(session, user.id, days=1)
        return user.id


async def show_outbox(user_id: int) -> int:
    async with get_session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(Outbox)
            .where(Outbox.user_id == user_id)
        )
        rows = list(
            await session.execute(
                select(Listing)
                .join(Outbox, Outbox.listing_id == Listing.id)
                .where(Outbox.user_id == user_id)
                .order_by(Outbox.id)
                .limit(2)
            )
        )

    for (listing,) in rows:
        print("\n  --- сообщение, которое ушло бы в Telegram ---")
        for line in render(listing).splitlines():
            print(f"  {line}")
    return total or 0


async def cleanup() -> None:
    async with get_session() as session:
        await session.execute(delete(User).where(User.tg_id == TEST_TG_ID))


async def main() -> int:
    setup_logging("INFO")
    region = settings.regions[0]
    client = HttpVintedClient(region, proxies=settings.proxies)

    try:
        user_id = await setup_user(region.code)
        print(f"[1] Заведён тестовый пользователь ({region.label}, цена до 1000)")

        print("\n[2] Первый цикл — ожидается bootstrap без рассылки")
        first = await run_cycle(client, pages=settings.poll_pages)
        print(f"    fetched={first.fetched} bootstrapped={first.bootstrapped} "
              f"queued={first.queued}")
        if not first.bootstrapped:
            print("    (bootstrap уже проходил раньше — это нормально при повторе)")

        print(f"\n[3] Ждём {PAUSE} сек, чтобы на Vinted появились новые объявления...")
        await asyncio.sleep(PAUSE)

        print("\n[4] Второй цикл — здесь и должны появиться новые объявления")
        second = await run_cycle(client, pages=settings.poll_pages)
        print(f"    fetched={second.fetched} unseen={second.unseen} "
              f"fresh={second.fresh} matched={second.matched} queued={second.queued} "
              f"gap={second.gap_suspected}")

        total = await show_outbox(user_id)

        print("\n=== ИТОГ ===")
        ok = second.queued > 0
        print("Конвейер работает: объявления дошли до очереди отправки" if ok
              else "Очередь пуста — смотри логи выше")
        if second.gap_suspected:
            print("Обнаружен разрыв: увеличь POLL_PAGES или уменьши POLL_INTERVAL")
        print(f"Всего в очереди для тестового пользователя: {total}")
        return 0 if ok else 1
    finally:
        await cleanup()
        await client.close()
        await engine.dispose()
        print("\nТестовый пользователь удалён")


if __name__ == "__main__":
    raise SystemExit(run_async(main()))
