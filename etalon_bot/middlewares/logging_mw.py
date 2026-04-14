import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger("etalon_bot")


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        start = time.perf_counter()

        user_id: int | str = "unknown"
        update_type = type(event).__name__
        text_preview = ""

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else "unknown"
            if event.text:
                text_preview = event.text[:50]
            elif event.caption:
                text_preview = event.caption[:50]
            else:
                text_preview = f"[{event.content_type}]"
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else "unknown"
            text_preview = event.data[:50] if event.data else ""

        logger.info(
            "[%s] user=%s text=%r",
            update_type,
            user_id,
            text_preview,
        )

        try:
            result = await handler(event, data)
        except Exception:
            elapsed = (time.perf_counter() - start) * 1000
            logger.exception(
                "[%s] user=%s FAILED after %.1fms",
                update_type,
                user_id,
                elapsed,
            )
            raise

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "[%s] user=%s completed in %.1fms",
            update_type,
            user_id,
            elapsed,
        )

        return result
