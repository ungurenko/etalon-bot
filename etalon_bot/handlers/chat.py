"""
Handlers: free-form chat with the LLM for active users.

This router has the lowest priority and acts as a catch-all for text/voice
messages from users who are NOT in an FSM state.
"""

import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.enums import ChatAction, ContentType
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database.models import (
    UserStatus,
    OnboardingStatus,
    StrategyStatus,
    MessageRole,
    MessageType,
)
from etalon_bot.database import queries
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

BATCH_DELAY_SECONDS = 5
MAX_PROMPT_LENGTH = 3000

_batch_tasks: dict[int, asyncio.Task] = {}
_user_batches: dict[int, dict] = {}


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
    session: AsyncSession,
    fragments: list[str],
    last_message: Message,
    is_voice: bool,
) -> None:
    """Build context, call LLM, save messages, send response."""
    user = await queries.get_user(session, user_id)
    if user is None:
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
    except Exception as exc:
        logger.error("Unexpected error in chat for user %s: %s", user_id, exc)
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


async def _flush_chat_batch(user_id: int, bot: Bot, session: AsyncSession) -> None:
    """Wait for silence, then process the collected messages."""
    await asyncio.sleep(BATCH_DELAY_SECONDS)
    _batch_tasks.pop(user_id, None)

    batch = _user_batches.pop(user_id, None)
    if batch is None or not batch.get("fragments"):
        return

    await _process_chat(
        user_id,
        bot,
        session,
        batch["fragments"],
        batch["last_message"],
        batch["is_voice"],
    )


# ── Message handler ──────────────────────────────────────────────────────────

@router.message(F.content_type.in_({ContentType.TEXT, ContentType.VOICE}))
async def on_chat_message(
    message: Message,
    bot: Bot,
    session: AsyncSession,
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

    # Batching: accumulate fragments per user
    if user_id not in _user_batches:
        _user_batches[user_id] = {
            "fragments": [],
            "is_voice": False,
            "last_message": None,
        }

    batch = _user_batches[user_id]
    batch["fragments"].append(text)
    batch["last_message"] = message
    if is_voice:
        batch["is_voice"] = True

    # Cancel previous flush task
    old_task = _batch_tasks.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()

    # Schedule new flush
    _batch_tasks[user_id] = asyncio.create_task(
        _flush_chat_batch(user_id, bot, session)
    )
