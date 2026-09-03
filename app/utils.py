import asyncio
import contextlib
import sys
from collections.abc import Coroutine
from typing import Any


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Запускает корутину с циклом событий, пригодным для psycopg.

    На Windows по умолчанию используется ProactorEventLoop, с которым psycopg
    в асинхронном режиме работать не умеет. На Linux (в том числе в контейнере)
    поведение обычное.
    """
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """Спит указанное время, но просыпается сразу при остановке приложения.

    Возвращает True, если пора завершаться.
    """
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=max(seconds, 0))
    return stop.is_set()
