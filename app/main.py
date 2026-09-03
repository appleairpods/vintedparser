"""Точка входа: один процесс, несколько параллельных задач (вариант A).

Бот, парсер, отправщик и уборщик живут в одном event loop. Когда парсер
начнёт мешать боту или захочется перезапускать их отдельно, задачи
разъезжаются по контейнерам без переписывания логики — они уже общаются
только через БД.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.application.poll import cleanup_loop, heartbeat_key, poll_loop
from app.application.subscriptions import reminder_loop
from app.config import settings
from app.infrastructure.db import repo
from app.infrastructure.db.base import engine, get_session
from app.infrastructure.telegram.handlers import router
from app.infrastructure.telegram.sender import sender_loop
from app.infrastructure.vinted.client import HttpVintedClient
from app.logging_setup import setup_logging
from app.utils import run_async, sleep_or_stop

log = structlog.get_logger(__name__)

WATCHDOG_INTERVAL = 300
WATCHDOG_THRESHOLD = timedelta(minutes=15)


async def watchdog_loop(bot: Bot, stop: asyncio.Event) -> None:
    """Самый опасный отказ — тихий: процесс жив, а новых данных нет.

    Поэтому следим не за живостью процесса, а за временем последнего
    успешного опроса, и жалуемся администратору в тот же Telegram.
    """
    alerted: set[str] = set()

    while not stop.is_set():
        if await sleep_or_stop(stop, WATCHDOG_INTERVAL):
            return

        try:
            stale = await _stale_regions()

            for region in sorted(stale - alerted):
                await _tell_admins(
                    bot,
                    f"⚠️ Парсер {region} молчит дольше "
                    f"{int(WATCHDOG_THRESHOLD.total_seconds() // 60)} мин. "
                    "Скорее всего, Vinted закрыл доступ.",
                )
                log.error("watchdog.stale", region=region)

            for region in sorted(alerted - stale):
                await _tell_admins(bot, f"✅ Парсер {region} снова работает.")

            alerted = stale
        except Exception:  # noqa: BLE001
            log.exception("watchdog.failed")


async def _stale_regions() -> set[str]:
    stale: set[str] = set()
    async with get_session() as session:
        for region in settings.region_codes:
            heartbeat = await repo.get_state(session, heartbeat_key(region))
            if not heartbeat:
                continue
            lag = datetime.now(UTC) - datetime.fromisoformat(heartbeat)
            if lag > WATCHDOG_THRESHOLD:
                stale.add(region)
    return stale


async def _tell_admins(bot: Bot, text: str) -> None:
    for admin_id in settings.admins:
        with contextlib.suppress(Exception):
            await bot.send_message(admin_id, text)


async def setup_commands(bot: Bot) -> None:
    """Список в синей кнопке «Меню». Основная навигация всё равно на кнопках,
    поэтому здесь только самое нужное."""
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="pause", description="Выключить уведомления"),
            BotCommand(command="resume", description="Включить уведомления"),
            BotCommand(command="list", description="Мои фильтры"),
            BotCommand(command="profile", description="Личный кабинет"),
            BotCommand(command="sub", description="Подписка"),
            BotCommand(command="help", description="Как это работает"),
        ]
    )


async def main() -> None:
    setup_logging(settings.log_level)
    log.info(
        "app.start",
        regions=",".join(settings.region_codes),
        interval=settings.poll_interval,
        pages=settings.poll_pages,
        proxy=bool(settings.vinted_proxy),
    )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    clients = [
        HttpVintedClient(region, proxies=settings.proxies)
        for region in settings.regions
    ]
    stop = asyncio.Event()

    background = [
        asyncio.create_task(poll_loop(client, stop), name=f"poll:{client.region}")
        for client in clients
    ]
    background += [
        asyncio.create_task(sender_loop(bot, stop), name="sender"),
        asyncio.create_task(cleanup_loop(stop), name="cleanup"),
        asyncio.create_task(reminder_loop(stop), name="reminders"),
        asyncio.create_task(watchdog_loop(bot, stop), name="watchdog"),
    ]

    try:
        with contextlib.suppress(Exception):
            await setup_commands(bot)
        await dispatcher.start_polling(bot, handle_signals=True)
    finally:
        log.info("app.stopping")
        stop.set()
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        for client in clients:
            await client.close()
        await bot.session.close()
        await engine.dispose()
        log.info("app.stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        run_async(main())
