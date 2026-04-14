from __future__ import annotations
"""
Handlers: onboarding flow — collecting Point A across 8 spheres.

State is persisted in DB (current_sphere / current_question) so the flow
survives bot restarts. FSM state is only used to route incoming messages
to the answer handler.
"""

import asyncio
import logging
from typing import Any

from aiogram import Router, F, Bot
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import OnboardingStatus
from etalon_bot.database import queries
from etalon_bot.keyboards.client_kb import (
    onboarding_question_kb,
    onboarding_sphere_complete_kb,
    onboarding_resume_kb,
    onboarding_start_kb,
)
from etalon_bot.services.whisper_service import (
    transcribe_voice,
    TranscriptionError,
    FileTooLargeError,
)

logger = logging.getLogger(__name__)

router = Router(name="onboarding")

TOTAL_SPHERES = 8
BATCH_DELAY_SECONDS = 3


# ── FSM ──────────────────────────────────────────────────────────────────────

class OnboardingFSM(StatesGroup):
    waiting_gender = State()
    waiting_answer = State()
    waiting_photo = State()


def _g(user, female: str, male: str, neutral: str = "") -> str:
    """Return gendered text based on user's gender field."""
    g = getattr(user, 'gender', None)
    if g == "male":
        return male
    if g == "neutral":
        return neutral or female
    return female  # default female


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _send_question(
    target: Message | CallbackQuery,
    session: AsyncSession,
    sphere: int,
    question_num: int,
) -> None:
    """Format and send the current onboarding question."""
    sphere_name = await queries.get_sphere_name(session, sphere)
    total = await queries.get_total_questions_in_sphere(session, sphere)
    questions = await queries.get_questions_for_sphere(session, sphere)

    q_obj = None
    for q in questions:
        if q.question_number == question_num:
            q_obj = q
            break

    if q_obj is None:
        logger.error(
            "Question not found: sphere=%s, question=%s", sphere, question_num
        )
        send = target.answer if isinstance(target, Message) else target.message.answer
        await send("Произошла ошибка при загрузке вопроса. Обратитесь к администратору.")
        return

    text = (
        f"📍 Сфера {sphere} из {TOTAL_SPHERES}: {sphere_name}\n"
        f"Вопрос {question_num} из {total}\n\n"
        f"{q_obj.question_text}"
    )

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=onboarding_question_kb())
        except Exception:
            await target.message.answer(text, reply_markup=onboarding_question_kb())
    else:
        await target.answer(text, reply_markup=onboarding_question_kb())


async def _advance_question(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user_id: int,
    state: FSMContext,
) -> None:
    """Move to the next question or complete the sphere/onboarding."""
    user = await queries.get_user(session, user_id)
    if user is None:
        return

    sphere = user.current_sphere or 1
    question = (user.current_question or 1) + 1
    total = await queries.get_total_questions_in_sphere(session, sphere)

    if question <= total:
        # Next question in the same sphere
        await queries.update_user_field(session, user_id, current_question=question)
        await _send_question(message, session, sphere, question)
        return

    # Sphere complete
    sphere_name = await queries.get_sphere_name(session, sphere)

    if sphere < TOTAL_SPHERES:
        await message.answer(
            f"✅ Сфера «{sphere_name}» — готово!\n\n"
            f"Ты {_g(user, 'прошла', 'прошёл', 'прошёл')} {sphere} из {TOTAL_SPHERES} сфер.",
            reply_markup=onboarding_sphere_complete_kb(),
        )
    else:
        # All spheres done
        await _complete_onboarding(message, bot, session, user, state)


async def _complete_onboarding(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: Any,
    state: FSMContext,
) -> None:
    """Ask for photo before finalizing onboarding."""
    name = user.display_name or user.full_name or ""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить", callback_data="onboarding_skip_photo")
    builder.adjust(1)

    await state.set_state(OnboardingFSM.waiting_photo)

    await message.answer(
        f"Отлично, {name}! Все 8 сфер пройдены! 🎉\n\n"
        "Последний шаг — отправь, пожалуйста, свою фотографию 📸\n"
        "Она нужна для работы над твоей эталонной версией.\n\n"
        "Можешь отправить любое фото, на котором тебя хорошо видно.",
        reply_markup=builder.as_markup(),
    )


async def _finalize_onboarding(
    message_or_callback,
    bot: Bot,
    session: AsyncSession,
    user: Any,
    state: FSMContext,
) -> None:
    """Finalize the onboarding process after photo (or skip)."""
    name = user.display_name or user.full_name or ""
    username_str = f"@{user.username}" if user.username else "без username"

    await queries.update_user_field(
        session,
        user.telegram_id,
        onboarding_status=OnboardingStatus.completed,
        current_sphere=None,
        current_question=None,
    )
    await state.clear()

    send = (
        message_or_callback.answer
        if isinstance(message_or_callback, Message)
        else message_or_callback.message.answer
    )

    await send(
        f"🎉 {name}, ты {_g(user, 'прошла', 'прошёл', 'прошёл')} все 8 сфер!\n\n"
        "Твои ответы переданы. Как только эталонная версия будет загружена "
        "— я составлю для тебя персональную стратегию.\n\n"
        "Напишу тебе, как только всё будет готово! 💫"
    )

    # Notify admins
    admin_text = (
        f"✅ {name} ({username_str}) — онбординг завершён. "
        "Загрузите эталонную версию для этого клиента."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception as exc:
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)


# ── Message batching ─────────────────────────────────────────────────────────

_batch_tasks: dict[int, asyncio.Task] = {}


async def _flush_batch(
    user_id: int,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
    is_voice: bool,
) -> None:
    """Wait BATCH_DELAY_SECONDS, then join queued fragments and save."""
    await asyncio.sleep(BATCH_DELAY_SECONDS)

    data = await state.get_data()
    fragments: list[str] = data.pop("batch_fragments", [])
    last_message: Message | None = data.pop("batch_last_message", None)
    await state.update_data(batch_fragments=[], batch_last_message=None)

    if not fragments or last_message is None:
        return

    _batch_tasks.pop(user_id, None)

    combined = "\n".join(fragments)

    user = await queries.get_user(session, user_id)
    if user is None:
        return

    sphere = user.current_sphere or 1
    question = user.current_question or 1
    questions = await queries.get_questions_for_sphere(session, sphere)
    q_text = ""
    for q in questions:
        if q.question_number == question:
            q_text = q.question_text
            break

    await queries.save_answer(
        session,
        user_id=user_id,
        sphere_number=sphere,
        question_number=question,
        question_text=q_text,
        answer_text=combined,
        is_voice=is_voice,
    )

    await _advance_question(last_message, bot, session, user_id, state)


# ── Callbacks ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "onboarding_start")
async def cb_onboarding_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer("Ошибка. Напиши /start", show_alert=True)
        return

    await queries.update_user_field(
        session,
        user.telegram_id,
        onboarding_status=OnboardingStatus.in_progress,
        current_sphere=1,
        current_question=1,
    )

    # Ask gender if not set
    if not user.gender:
        await state.set_state(OnboardingFSM.waiting_gender)
        from etalon_bot.keyboards.client_kb import gender_choice_kb
        try:
            await callback.message.edit_text(
                "Перед началом — как к тебе лучше обращаться?",
                reply_markup=gender_choice_kb(),
            )
        except Exception:
            await callback.message.answer(
                "Перед началом — как к тебе лучше обращаться?",
                reply_markup=gender_choice_kb(),
            )
        await callback.answer()
        return

    await state.set_state(OnboardingFSM.waiting_answer)
    await state.update_data(batch_fragments=[], batch_last_message=None)

    await _send_question(callback, session, sphere=1, question_num=1)
    await callback.answer()


@router.callback_query(OnboardingFSM.waiting_gender, F.data.startswith("gender_"))
async def cb_gender_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    gender = callback.data.replace("gender_", "")  # female/male/neutral
    await queries.update_user_field(session, user.telegram_id, gender=gender)

    await state.set_state(OnboardingFSM.waiting_answer)
    await state.update_data(batch_fragments=[], batch_last_message=None)

    await _send_question(callback, session, sphere=1, question_num=1)
    await callback.answer()


@router.callback_query(F.data == "onboarding_later")
async def cb_onboarding_later(callback: CallbackQuery, **kwargs):
    await callback.message.edit_text(
        "Хорошо! Когда будешь на связи — просто нажми кнопку 💛",
        reply_markup=onboarding_start_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "onboarding_skip")
async def cb_onboarding_skip(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    sphere = user.current_sphere or 1
    question = user.current_question or 1
    questions = await queries.get_questions_for_sphere(session, sphere)
    q_text = ""
    for q in questions:
        if q.question_number == question:
            q_text = q.question_text
            break

    await queries.save_answer(
        session,
        user_id=user.telegram_id,
        sphere_number=sphere,
        question_number=question,
        question_text=q_text,
        answer_text=None,
        is_skipped=True,
    )

    await callback.answer("Пропущено")
    await _advance_question(
        callback.message, bot, session, user.telegram_id, state
    )


@router.callback_query(F.data == "onboarding_pause")
async def cb_onboarding_pause(
    callback: CallbackQuery, state: FSMContext, **kwargs
):
    await state.clear()
    await callback.message.edit_text(
        "Хорошо! Когда будешь на связи — напиши /menu 💛"
    )
    await callback.answer()


@router.callback_query(F.data == "onboarding_next_sphere")
async def cb_onboarding_next_sphere(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    next_sphere = (user.current_sphere or 1) + 1
    if next_sphere > TOTAL_SPHERES:
        await callback.answer("Все сферы пройдены")
        return

    await queries.update_user_field(
        session, user.telegram_id,
        current_sphere=next_sphere,
        current_question=1,
    )
    await state.set_state(OnboardingFSM.waiting_answer)
    await state.update_data(batch_fragments=[], batch_last_message=None)

    await _send_question(callback, session, next_sphere, 1)
    await callback.answer()


@router.callback_query(F.data == "onboarding_resume")
async def cb_onboarding_resume(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    sphere = user.current_sphere or 1
    question = user.current_question or 1

    await state.set_state(OnboardingFSM.waiting_answer)
    await state.update_data(batch_fragments=[], batch_last_message=None)

    await _send_question(callback, session, sphere, question)
    await callback.answer()


@router.callback_query(F.data == "onboarding_restart_sphere")
async def cb_onboarding_restart_sphere(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    sphere = user.current_sphere or 1
    await queries.delete_answers_for_sphere(session, user.telegram_id, sphere)
    await queries.update_user_field(
        session, user.telegram_id, current_question=1
    )

    await state.set_state(OnboardingFSM.waiting_answer)
    await state.update_data(batch_fragments=[], batch_last_message=None)

    await _send_question(callback, session, sphere, 1)
    await callback.answer()


# ── Message handler (waiting_answer) ─────────────────────────────────────────

@router.message(
    OnboardingFSM.waiting_answer,
    F.content_type.in_({ContentType.TEXT, ContentType.VOICE}),
)
async def on_answer_text_or_voice(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        return

    user_id = user.telegram_id
    is_voice = message.content_type == ContentType.VOICE

    # Extract text
    if is_voice:
        try:
            text = await transcribe_voice(bot, message.voice.file_id)
            await message.answer("Записано 💛")
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
        await message.answer("Пустой ответ — попробуй ещё раз 🙏")
        return

    # Batching: collect fragments, flush after BATCH_DELAY_SECONDS of silence
    data = await state.get_data()
    fragments: list[str] = data.get("batch_fragments", [])
    fragments.append(text)
    await state.update_data(batch_fragments=fragments, batch_last_message=message)

    # Cancel previous flush task
    old_task = _batch_tasks.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()

    # Schedule new flush
    _batch_tasks[user_id] = asyncio.create_task(
        _flush_batch(user_id, bot, session, state, is_voice)
    )


# ── Photo step (after all spheres) ──────────────────────────────────────────


@router.message(OnboardingFSM.waiting_photo, F.photo)
async def on_photo_received(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        return

    photo_file_id = message.photo[-1].file_id
    await queries.update_user_field(
        session, user.telegram_id, photo_file_id=photo_file_id,
    )
    await message.answer("Фото сохранено! 📸")

    user = await queries.get_user(session, user.telegram_id)
    await _finalize_onboarding(message, bot, session, user, state)


@router.callback_query(F.data == "onboarding_skip_photo")
async def cb_skip_photo(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    await callback.answer("Пропущено")
    await _finalize_onboarding(callback, bot, session, user, state)


@router.message(OnboardingFSM.waiting_photo)
async def on_photo_wrong_type(message: Message, **kwargs):
    await message.answer("Отправь, пожалуйста, фотографию 📸 Или нажми «Пропустить».")


# ── Unsupported content in answer state ─────────────────────────────────────


@router.message(
    OnboardingFSM.waiting_answer,
    F.content_type.in_({
        ContentType.PHOTO,
        ContentType.DOCUMENT,
        ContentType.STICKER,
        ContentType.VIDEO,
        ContentType.VIDEO_NOTE,
        ContentType.ANIMATION,
    }),
)
async def on_answer_unsupported(message: Message, **kwargs):
    await message.answer("Мне нужен текстовый или голосовой ответ 🙏")
