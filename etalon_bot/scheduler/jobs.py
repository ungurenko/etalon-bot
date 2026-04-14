import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from etalon_bot.services.checkin_service import run_proactive_checkins
from etalon_bot.services.reminder_service import check_inactive_clients
from etalon_bot.services.access_service import check_expiring_access

logger = logging.getLogger("etalon_bot.scheduler")


def setup_scheduler(bot: Bot, session_factory: async_sessionmaker) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    # Proactive check-ins: Monday and Thursday at 10:00 MSK
    scheduler.add_job(
        run_proactive_checkins,
        CronTrigger(day_of_week="mon,thu", hour=10, minute=0, timezone="Europe/Moscow"),
        args=[bot, session_factory],
        id="proactive_checkins",
        name="Proactive check-ins",
        replace_existing=True,
    )

    # Inactive client check: daily at 09:00 MSK
    scheduler.add_job(
        check_inactive_clients,
        CronTrigger(hour=9, minute=0, timezone="Europe/Moscow"),
        args=[bot, session_factory],
        id="inactive_check",
        name="Inactive client check",
        replace_existing=True,
    )

    # Access expiry warnings: daily at 11:00 MSK
    scheduler.add_job(
        check_expiring_access,
        CronTrigger(hour=11, minute=0, timezone="Europe/Moscow"),
        args=[bot, session_factory],
        id="access_expiry_check",
        name="Access expiry warnings",
        replace_existing=True,
    )

    logger.info("Scheduler configured: check-ins Mon/Thu 10:00, reminders daily 09:00, access expiry 11:00 MSK")
    return scheduler
