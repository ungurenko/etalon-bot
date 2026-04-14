import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from etalon_bot.database.queries import get_users_expiring_soon
from etalon_bot.config import ADMIN_IDS

logger = logging.getLogger(__name__)

_WARNING_DAYS = [30, 7, 1]


async def check_expiring_access(bot: Bot, session_factory: async_sessionmaker):
    try:
        await _check_expiring_access_impl(bot, session_factory)
    except Exception as exc:
        logger.exception("check_expiring_access failed: %s", exc)


async def _check_expiring_access_impl(bot: Bot, session_factory: async_sessionmaker):
    async with session_factory() as session:
        for days in _WARNING_DAYS:
            users = await get_users_expiring_soon(session, days)
            for user in users:
                # Check if exactly within this warning window
                if user.access_until is None:
                    continue
                remaining = (user.access_until - datetime.utcnow()).days
                if remaining > days:
                    continue

                name = user.display_name or user.full_name or "друг"
                if days == 1:
                    msg = f"⚠️ {name}, завтра истекает срок доступа к боту. Для продления свяжись с Дианой."
                elif days == 7:
                    msg = f"⏰ {name}, через неделю истечёт срок доступа к боту. Для продления свяжись с Дианой."
                else:
                    msg = f"ℹ️ {name}, через {remaining} дней истечёт срок доступа к боту."

                try:
                    await bot.send_message(user.telegram_id, msg)
                except Exception as exc:
                    logger.warning("Failed to send expiry warning to %s: %s", user.telegram_id, exc)

                # Notify admins about 1-day expiry
                if days == 1:
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ У клиента «{name}» завтра истекает доступ.",
                            )
                        except Exception:
                            pass
