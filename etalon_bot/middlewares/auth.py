from __future__ import annotations
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.engine import SessionFactory
from etalon_bot.database.models import UserRole, UserStatus
from etalon_bot.database.queries import get_user, update_user_field

logger = logging.getLogger("etalon_bot")

_ADMIN_CALLBACK_PREFIXES = (
    "admin_",
    "etalon_",
    "strategy_",
    "broadcast_",
    "settings_",
    "kb_",
)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # --- Determine telegram_id ---
        telegram_id: int | None = None

        if isinstance(event, Message):
            telegram_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            telegram_id = event.from_user.id if event.from_user else None

        if telegram_id is None:
            return await handler(event, data)

        # --- Open DB session and fetch user ---
        async with SessionFactory() as session:
            user = await get_user(session, telegram_id)

            # New user hitting /start — let the handler create them
            if user is None:
                if isinstance(event, Message) and event.text and event.text.startswith("/start"):
                    data["session"] = session
                    return await handler(event, data)
                # Unknown user, not /start — silently ignore
                return None

            # Bot was blocked by user earlier
            if user.bot_blocked:
                return None

            # User is blocked by admin
            if user.status == UserStatus.blocked:
                if isinstance(event, Message):
                    await event.answer("Доступ ограничен.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("Доступ ограничен.", show_alert=True)
                return None

            # Access expiry check
            if (user.access_until is not None
                    and user.access_until < datetime.utcnow()
                    and user.role != UserRole.admin):
                msg = "Срок вашего доступа истёк. Для продления свяжитесь с Дианой."
                if isinstance(event, Message):
                    await event.answer(msg)
                elif isinstance(event, CallbackQuery):
                    await event.answer(msg, show_alert=True)
                return None

            # Admin-only callbacks check
            if isinstance(event, CallbackQuery) and event.data:
                if any(event.data.startswith(p) for p in _ADMIN_CALLBACK_PREFIXES):
                    if user.role != UserRole.admin and telegram_id not in ADMIN_IDS:
                        await event.answer("Нет доступа.", show_alert=True)
                        return None

            # Update last activity
            await update_user_field(
                session, telegram_id, last_activity_at=datetime.utcnow()
            )

            # Inject user and session into handler data
            data["user"] = user
            data["session"] = session

            return await handler(event, data)
