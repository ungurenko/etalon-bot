from __future__ import annotations
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from etalon_bot.config import RATE_LIMIT_PER_MINUTE


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._limits: dict[int, list[float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_id: int | None = None

        if isinstance(event, Message):
            telegram_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            telegram_id = event.from_user.id if event.from_user else None

        if telegram_id is None:
            return await handler(event, data)

        now = time.monotonic()
        window_start = now - 60.0

        # Get or create timestamp list and prune old entries
        timestamps = self._limits.get(telegram_id, [])
        timestamps = [t for t in timestamps if t > window_start]

        if len(timestamps) >= RATE_LIMIT_PER_MINUTE:
            if isinstance(event, Message):
                await event.answer("Подожди немного, пожалуйста 🙏")
            elif isinstance(event, CallbackQuery):
                await event.answer("Подожди немного, пожалуйста 🙏", show_alert=True)
            self._limits[telegram_id] = timestamps
            return None

        timestamps.append(now)
        self._limits[telegram_id] = timestamps

        return await handler(event, data)
