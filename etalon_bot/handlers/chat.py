"""
Handlers: free-form chat with the LLM for active users.

This router has the lowest priority and acts as a catch-all for text/voice
messages from users who are NOT in an FSM state.
"""

import logging

from aiogram import Router, F, Bot
from aiogram.enums import ChatAction, ContentType
from aiogram.types import Message

from etalon_bot.database.models import (
    UserStatus,
    OnboardingStatus,
    StrategyStatus,
    MessageRole,
    MessageType,
)
from etalon_bot.database import queries
from etalon_bot.database.engine import SessionFactory
from etalon_bot.services.llm_service import call_llm, LLMError
from etalon_bot.services.context_builder import build_chat_context
from etalon_bot.services.whisper_service import (
    transcribe_voice,
    TranscriptionError,
    FileTooLargeError,
)
from etalon_bot.utils.text_utils import split_long_message, markdown_to_telegram_html

logger = logging.getLogger(__name__)

router = Router(name="chat")

MAX_PROMPT_LENGTH = 3000


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_chat_eligible(user) -> bool:
    """Check if the user can use the free chat."""
    if user is None:
        return False
    if user.status != UserStatus.active:
        return False
    if user.onboarding_status == OnboardingStatus.completed:
        return True
    if user.strategy_status in (StrategyStatus.generated, StrategyStatus.active):
        return True
    return False


async def _process_chat(
    user_id: int,
    bot: Bot,
    fragments: list[str],
    last_message: Message,
    is_voice: bool,
) -> None:
    """Build context, call LLM, save messages, send response."""
    async with SessionFactory() as session:
        logger.info(
            "Processing chat: user=%s fragments=%d is_voice=%s",
            user_id,
            len(fragments),
            is_voice,
        )
        user = await queries.get_user(session, user_id)
        if user is None:
            logger.warning("Chat skipped: user %s not found", user_id)
            return

        full_text = "\n".join(fragments)
        prompt_text = full_text[:MAX_PROMPT_LENGTH]

        # Save user message (full text)
        msg_type = MessageType.voice if is_voice else MessageType.text
        await queries.save_message(
            session, user_id, MessageRole.client, full_text, msg_type
        )

        # Show typing
        try:
            await bot.send_chat_action(user_id, ChatAction.TYPING)
        except Exception:
            pass

        # Build context and call LLM
        try:
            system_prompt = await build_chat_context(session, user)
            response = await call_llm(system_prompt, prompt_text)
        except LLMError as exc:
            logger.error("LLM error for user %s: %s", user_id, exc)
            await last_message.answer(
                "Мне нужно немного времени на размышления... "
                "Попробуй написать через минуту 🙏"
            )
            return
        except Exception:
            logger.exception("Unexpected error in chat for user %s", user_id)
            await last_message.answer(
                "Мне нужно немного времени на размышления... "
                "Попробуй написать через минуту 🙏"
            )
            return

        # Save assistant response
        await queries.save_message(
            session, user_id, MessageRole.assistant, response, MessageType.text
        )

        # Send response (convert Markdown → Telegram HTML, split if too long)
        formatted = markdown_to_telegram_html(response)
        chunks = split_long_message(formatted)
        for chunk in chunks:
            await last_message.answer(chunk)


# ── Message handler ──────────────────────────────────────────────────────────

@router.message(F.content_type.in_({ContentType.TEXT, ContentType.VOICE}))
async def on_chat_message(
    message: Message,
    bot: Bot,
    **kwargs,
):
    user = kwargs.get("user")
    if not _is_chat_eligible(user):
        return

    user_id = user.telegram_id
    is_voice = message.content_type == ContentType.VOICE

    # Extract text
    if is_voice:
        try:
            text = await transcribe_voice(bot, message.voice.file_id)
        except FileTooLargeError:
            await message.answer(
                "Голосовое сообщение слишком длинное. "
                "Попробуй записать покороче или напиши текстом 🙏"
            )
            return
        except TranscriptionError:
            await message.answer(
                "Не удалось разобрать голосовое сообщение. "
                "Попробуй записать ещё раз или напиши текстом 🙏"
            )
            return
    else:
        text = message.text or ""

    if not text.strip():
        return

    await _process_chat(user_id, bot, [text], message, is_voice)
