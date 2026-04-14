import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import async_sessionmaker

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.queries import (
    get_inactive_clients, get_setting_json, update_user_field, save_message,
)
from etalon_bot.database.models import MessageRole, MessageType
from etalon_bot.keyboards.client_kb import main_menu_kb, procrastination_kb

logger = logging.getLogger("etalon_bot.reminder")


async def check_inactive_clients(bot: Bot, session_factory: async_sessionmaker):
    try:
        await _check_inactive_clients_impl(bot, session_factory)
    except Exception as exc:
        logger.exception("check_inactive_clients failed: %s", exc)


async def _check_inactive_clients_impl(bot: Bot, session_factory: async_sessionmaker):
    logger.info("Checking inactive clients...")

    async with session_factory() as session:
        thresholds = await get_setting_json(session, "reminder_thresholds")
        if not thresholds:
            thresholds = {"soft": 3, "medium": 7, "hard": 14}

        soft_days = thresholds.get("soft", 3)
        medium_days = thresholds.get("medium", 7)
        hard_days = thresholds.get("hard", 14)

        # 14+ days — notify admin only
        hard_inactive = await get_inactive_clients(session, hard_days)
        for user in hard_inactive:
            name = user.display_name or user.full_name
            username_str = f"@{user.username}" if user.username else "без username"
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"⚠️ {name} ({username_str}) не заходила {hard_days}+ дней."
                    )
                except Exception:
                    pass

        # 7+ days — medium reminder (exclude 14+ already notified)
        medium_inactive = await get_inactive_clients(session, medium_days)
        hard_ids = {u.telegram_id for u in hard_inactive}
        for user in medium_inactive:
            if user.telegram_id in hard_ids:
                continue
            name = user.display_name or user.full_name
            text = (
                f"Милая {name}, я немного скучаю по нашим диалогам 🌸\n\n"
                f"Если сейчас сложный период — это нормально. "
                f"У меня есть несколько техник, которые могут помочь.\n\n"
                f"Хочешь попробовать?"
            )
            await _send_reminder(bot, session, user, text, procrastination_kb())

        # 3+ days — soft reminder (exclude 7+ and 14+)
        soft_inactive = await get_inactive_clients(session, soft_days)
        medium_ids = {u.telegram_id for u in medium_inactive}
        for user in soft_inactive:
            if user.telegram_id in medium_ids or user.telegram_id in hard_ids:
                continue
            name = user.display_name or user.full_name
            text = (
                f"{name}, как ты? 💛\n\n"
                f"Я здесь, когда будешь готова продолжить. "
                f"Даже маленький шаг — это шаг вперёд.\n\n"
                f"Напиши мне или загляни в свой план 🌿"
            )
            await _send_reminder(bot, session, user, text, main_menu_kb())

    logger.info("Inactive client check completed.")


async def _send_reminder(bot, session, user, text, reply_markup):
    try:
        await bot.send_message(
            user.telegram_id, text, reply_markup=reply_markup,
        )
        await save_message(
            session, user.telegram_id, MessageRole.assistant,
            text, MessageType.reminder,
        )
    except TelegramForbiddenError:
        logger.warning(f"User {user.telegram_id} blocked the bot.")
        await update_user_field(session, user.telegram_id, bot_blocked=True)
        for admin_id in ADMIN_IDS:
            name = user.display_name or user.full_name
            try:
                await bot.send_message(admin_id, f"🚫 {name} заблокировала бота.")
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Reminder failed for {user.telegram_id}: {e}")
