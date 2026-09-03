"""Проверка напоминаний о подписке на временных пользователях.

Заводит двух подставных: у одного подписка кончается через двое суток, у
второго уже истекла вчера. Прогоняет цикл напоминаний, показывает, что попало
в очередь, и убирает за собой. Реальные пользователи не затрагиваются.

Запуск: .venv\\Scripts\\python.exe -m scripts.check_reminders
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.application.subscriptions import run_once
from app.infrastructure.db import repo
from app.infrastructure.db.base import engine, get_session
from app.infrastructure.db.models import Outbox, User
from app.logging_setup import setup_logging
from app.utils import run_async

SOON_TG_ID = -999_000_101
EXPIRED_TG_ID = -999_000_102


async def seed() -> dict[int, str]:
    now = datetime.now(UTC)
    async with get_session() as session:
        await session.execute(
            delete(User).where(User.tg_id.in_([SOON_TG_ID, EXPIRED_TG_ID]))
        )
        soon = await repo.get_or_create_user(session, SOON_TG_ID, "истекает_скоро")
        soon.subscription_until = now + timedelta(days=2)
        expired = await repo.get_or_create_user(session, EXPIRED_TG_ID, "уже_истекла")
        expired.subscription_until = now - timedelta(days=1)
        await session.flush()
        return {soon.id: "истекает через 2 дня", expired.id: "истекла вчера"}


async def cleanup() -> None:
    async with get_session() as session:
        await session.execute(
            delete(User).where(User.tg_id.in_([SOON_TG_ID, EXPIRED_TG_ID]))
        )


async def main() -> int:
    setup_logging("WARNING")
    try:
        users = await seed()
        print("[1] Заведены двое: истекает через 2 дня и истекла вчера")

        upcoming, expired = await run_once()
        print(f"[2] Цикл напоминаний: предупреждений={upcoming}, истёкших={expired}")

        async with get_session() as session:
            rows = list(
                await session.execute(
                    select(Outbox.user_id, Outbox.kind, Outbox.payload).where(
                        Outbox.user_id.in_(users)
                    )
                )
            )
        repeat_upcoming, repeat_expired = await run_once()

        for user_id, kind, payload in rows:
            print(f"\n  --- {users[user_id]} · kind={kind} ---")
            for line in (payload or "").splitlines():
                print(f"  {line}")

        ok = upcoming == 1 and expired == 1 and len(rows) == 2
        no_repeat = repeat_upcoming == 0 and repeat_expired == 0

        print("\n=== ИТОГ ===")
        print("Оба напоминания поставлены в очередь" if ok else "Что-то не сошлось")
        print(
            "Повторно не отправляются" if no_repeat else "ОШИБКА: шлёт по второму разу"
        )
        return 0 if ok and no_repeat else 1
    finally:
        await cleanup()
        await engine.dispose()
        print("Временные пользователи удалены")


if __name__ == "__main__":
    raise SystemExit(run_async(main()))
