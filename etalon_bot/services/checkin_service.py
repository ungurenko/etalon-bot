import logging

from aiogram import Bot
from aiogram.enums import ChatAction
from sqlalchemy.ext.asyncio import async_sessionmaker

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.queries import (
    get_active_clients, get_active_strategy, get_stages_for_strategy,
    get_items_for_stage, get_stage_by_number, save_message,
)
from etalon_bot.database.models import MessageRole, MessageType, StrategyStatus
from etalon_bot.services.context_builder import build_checkin_prompt
from etalon_bot.services.llm_service import call_llm, LLMError
from etalon_bot.utils.text_utils import split_long_message
from etalon_bot.keyboards.client_kb import checkin_items_kb

logger = logging.getLogger("etalon_bot.checkin")


async def run_proactive_checkins(bot: Bot, session_factory: async_sessionmaker):
    try:
        await _run_proactive_checkins_impl(bot, session_factory)
    except Exception as exc:
        logger.exception("run_proactive_checkins failed: %s", exc)


async def _run_proactive_checkins_impl(bot: Bot, session_factory: async_sessionmaker):
    logger.info("Running proactive check-ins...")

    async with session_factory() as session:
        clients = await get_active_clients(session)

        for user in clients:
            if user.strategy_status != StrategyStatus.active:
                continue
            if user.bot_blocked:
                continue

            try:
                await _send_checkin(bot, session, user)
            except Exception as e:
                logger.error(f"Check-in failed for user {user.telegram_id}: {e}")
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"❌ Ошибка проверки прогресса для {user.display_name or user.full_name}: {e}"
                        )
                    except Exception:
                        pass

    logger.info("Proactive check-ins completed.")


async def _send_checkin(bot: Bot, session, user):
    strategy = await get_active_strategy(session, user.telegram_id)
    if not strategy:
        return

    current_stage_num = user.current_stage or 1
    stage = await get_stage_by_number(session, strategy.id, current_stage_num)
    if not stage:
        return

    items = await get_items_for_stage(session, stage.id)
    if not items:
        return

    try:
        system_prompt, user_prompt = await build_checkin_prompt(session, user)
        await bot.send_chat_action(user.telegram_id, ChatAction.TYPING)
        checkin_text = await call_llm(system_prompt, user_prompt, max_tokens=800)
    except LLMError:
        name = user.display_name or user.full_name
        checkin_text = (
            f"Привет, {name}! 🌸\n\n"
            f"Давай посмотрим, как продвигаются дела по твоему плану:\n\n"
            f"📌 Этап {current_stage_num}: {stage.title}\n\n"
            f"Отметь, что удалось сделать за эти дни 👇"
        )

    items_data = [
        {"id": item.id, "text": item.text, "checked": item.is_completed}
        for item in items
    ]

    for chunk in split_long_message(checkin_text):
        await bot.send_message(user.telegram_id, chunk)

    await bot.send_message(
        user.telegram_id,
        "Отметь выполненные пункты:",
        reply_markup=checkin_items_kb(items_data),
    )

    await save_message(
        session, user.telegram_id, MessageRole.assistant,
        checkin_text, MessageType.checkin,
    )
