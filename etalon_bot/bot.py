import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from etalon_bot.config import BOT_TOKEN, LOG_LEVEL, ADMIN_IDS
from etalon_bot.database.engine import init_db, SessionFactory
from etalon_bot.database.queries import init_default_settings, init_questions_from_json
from etalon_bot.middlewares.auth import AuthMiddleware
from etalon_bot.middlewares.rate_limit import RateLimitMiddleware
from etalon_bot.middlewares.logging_mw import LoggingMiddleware
from etalon_bot.handlers import (
    admin_main_router,
    admin_clients_router,
    admin_etalon_router,
    admin_strategy_router,
    admin_knowledge_router,
    admin_broadcast_router,
    admin_settings_router,
    start_router,
    onboarding_router,
    plan_router,
    client_goals_router,
    client_etalon_router,
    client_pointa_router,
    chat_router,
)
from etalon_bot.scheduler.jobs import setup_scheduler

logger = logging.getLogger("etalon_bot")


async def on_startup(bot: Bot):
    logger.info("Initializing database...")
    await init_db()

    async with SessionFactory() as session:
        await init_default_settings(session)

        questions_path = os.path.join(
            os.path.dirname(__file__), "data", "init_questions.json"
        )
        await init_questions_from_json(session, questions_path)

    logger.info("Database initialized.")

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "🟢 Бот запущен и готов к работе.")
        except Exception:
            pass

    logger.info("Bot started successfully.")


def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Register middlewares (order: logging → rate_limit → auth)
    dp.message.outer_middleware(LoggingMiddleware())
    dp.callback_query.outer_middleware(LoggingMiddleware())
    dp.message.outer_middleware(RateLimitMiddleware())
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    # Register routers (order matters: admin first, chat last)
    dp.include_router(admin_main_router)
    dp.include_router(admin_clients_router)
    dp.include_router(admin_etalon_router)
    dp.include_router(admin_strategy_router)
    dp.include_router(admin_knowledge_router)
    dp.include_router(admin_broadcast_router)
    dp.include_router(admin_settings_router)
    dp.include_router(start_router)
    dp.include_router(onboarding_router)
    dp.include_router(plan_router)
    dp.include_router(client_goals_router)
    dp.include_router(client_etalon_router)
    dp.include_router(client_pointa_router)
    dp.include_router(chat_router)  # catch-all — last

    dp.startup.register(on_startup)

    # Setup scheduler for proactive checks and reminders
    scheduler = setup_scheduler(bot, SessionFactory)
    scheduler.start()

    async def shutdown_scheduler():
        scheduler.shutdown(wait=False)

    dp.shutdown.register(shutdown_scheduler)

    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()
