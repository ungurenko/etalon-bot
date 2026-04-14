"""
Handlers: клиент редактирует свою Точку А.

Доступно только клиентам с onboarding_status == completed.
Клиент может пересмотреть ответы по 8 сферам и перезаписать любой.
После правок с debounce 30 сек автоматически перегенерируется стратегия.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database import queries
from etalon_bot.database.models import OnboardingStatus, SphereAnswer, User
from etalon_bot.services.strategy_service import schedule_strategy_regen
from etalon_bot.services.whisper_service import (
    FileTooLargeError,
    TranscriptionError,
    transcribe_voice,
)
from etalon_bot.utils.text_utils import split_long_message, truncate_text

logger = logging.getLogger(__name__)

router = Router(name="client_pointa")

TOTAL_SPHERES = 8
BATCH_DELAY_SECONDS = 3
QUESTIONS_PER_PAGE = 5


# ── FSM ──────────────────────────────────────────────────────────────────────

class PointAFSM(StatesGroup):
    viewing_spheres = State()
    viewing_questions = State()
    viewing_answer = State()
    editing_answer = State()


# ── Batching state (per-user tasks) ──────────────────────────────────────────

_batch_tasks: dict[int, asyncio.Task] = {}


# ── Helpers: keyboards ───────────────────────────────────────────────────────

def _spheres_kb(sphere_names: dict[int, str]) -> InlineKeyboardMarkup:
    """Клавиатура с 8 сферами."""
    builder = InlineKeyboardBuilder()
    for n in range(1, TOTAL_SPHERES + 1):
        name = sphere_names.get(n, f"Сфера {n}")
        builder.button(
            text=f"{n}. {truncate_text(name, 40)}",
            callback_data=f"pointa_sphere_{n}",
        )
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    builder.adjust(*([1] * TOTAL_SPHERES + [1]))
    return builder.as_markup()


def _questions_kb(
    sphere_num: int,
    answers: list[SphereAnswer],
    page: int,
) -> InlineKeyboardMarkup:
    """Клавиатура со списком вопросов сферы (пагинация по 5)."""
    builder = InlineKeyboardBuilder()

    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE
    page_answers = answers[start:end]

    for answer in page_answers:
        if answer.is_skipped:
            mark = "⏭"
        elif answer.answer_text:
            mark = "✏️"
        else:
            mark = "⚪"
        preview = truncate_text(answer.question_text, 45)
        builder.button(
            text=f"{mark} {answer.question_number}. {preview}",
            callback_data=f"pointa_q_{sphere_num}_{answer.question_number}",
        )

    # Навигация
    nav_count = 0
    if page > 0:
        builder.button(
            text="◀️",
            callback_data=f"pointa_page_{sphere_num}_{page - 1}",
        )
        nav_count += 1
    if end < len(answers):
        builder.button(
            text="▶️",
            callback_data=f"pointa_page_{sphere_num}_{page + 1}",
        )
        nav_count += 1

    builder.button(text="🔙 К сферам", callback_data="pointa_back_spheres")
    builder.button(text="🏠 Главное меню", callback_data="menu_back")

    rows = [1] * len(page_answers)
    if nav_count:
        rows.append(nav_count)
    rows.extend([1, 1])
    builder.adjust(*rows)

    return builder.as_markup()


def _answer_view_kb(sphere_num: int) -> InlineKeyboardMarkup:
    """Клавиатура на экране просмотра одного ответа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить ответ", callback_data="pointa_edit")
    builder.button(
        text="🔙 К вопросам сферы",
        callback_data=f"pointa_back_questions_{sphere_num}",
    )
    builder.button(text="🏠 Главное меню", callback_data="menu_back")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def _editing_cancel_kb(sphere_num: int, question_num: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔙 Отмена",
        callback_data=f"pointa_q_{sphere_num}_{question_num}",
    )
    builder.adjust(1)
    return builder.as_markup()


# ── Helpers: data ────────────────────────────────────────────────────────────

async def _get_sphere_names(session: AsyncSession) -> dict[int, str]:
    """Собирает словарь {sphere_num: sphere_name} для всех 8 сфер."""
    names: dict[int, str] = {}
    for n in range(1, TOTAL_SPHERES + 1):
        names[n] = await queries.get_sphere_name(session, n)
    return names


async def _build_full_sphere_list(
    session: AsyncSession, user_id: int, sphere_num: int
) -> list[SphereAnswer]:
    """Возвращает список SphereAnswer для всех вопросов сферы.

    Если у клиента нет ответа на какой-то вопрос онбординга, создаёт
    in-memory объект SphereAnswer с answer_text=None — чтобы показать
    этот вопрос в списке с меткой ⚪.
    """
    questions = await queries.get_questions_for_sphere(session, sphere_num)
    existing = await queries.get_answers_by_sphere(session, user_id, sphere_num)
    existing_map = {a.question_number: a for a in existing}

    result: list[SphereAnswer] = []
    for q in questions:
        if q.question_number in existing_map:
            result.append(existing_map[q.question_number])
        else:
            # Плейсхолдер, не добавляется в сессию
            placeholder = SphereAnswer(
                user_id=user_id,
                sphere_number=sphere_num,
                question_number=q.question_number,
                question_text=q.question_text,
                answer_text=None,
                is_voice=False,
                is_skipped=False,
            )
            result.append(placeholder)
    return result


async def _is_completed(user: Optional[User]) -> bool:
    return (
        user is not None
        and user.onboarding_status == OnboardingStatus.completed
    )


# ── Entry: open Point A section ──────────────────────────────────────────────

@router.callback_query(F.data == "pointa_open")
async def cb_pointa_open(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer(
            "Раздел доступен после завершения онбординга",
            show_alert=True,
        )
        return

    sphere_names = await _get_sphere_names(session)

    await state.set_state(PointAFSM.viewing_spheres)
    # Сохраняем флаг edited_any между перезаходами в раздел
    data = await state.get_data()
    edited_any = data.get("pointa_edited_any", False)
    await state.update_data(pointa_edited_any=edited_any)

    text = (
        "📍 <b>Моя Точка А</b>\n\n"
        "Здесь ты можешь пересмотреть и обновить свои ответы, данные во время онбординга.\n\n"
        "Выбери сферу, чтобы увидеть вопросы и ответы."
    )
    await callback.message.edit_text(
        text,
        reply_markup=_spheres_kb(sphere_names),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Back to spheres list ─────────────────────────────────────────────────────

@router.callback_query(F.data == "pointa_back_spheres")
async def cb_pointa_back_spheres(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    sphere_names = await _get_sphere_names(session)
    await state.set_state(PointAFSM.viewing_spheres)

    text = (
        "📍 <b>Моя Точка А</b>\n\n"
        "Выбери сферу, чтобы увидеть вопросы и ответы."
    )
    await callback.message.edit_text(
        text,
        reply_markup=_spheres_kb(sphere_names),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Open a sphere → list questions ───────────────────────────────────────────

@router.callback_query(F.data.regexp(r"^pointa_sphere_\d+$"))
async def cb_pointa_sphere(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    sphere_num = int(callback.data.split("_")[-1])
    await _show_sphere_questions(callback, state, session, user, sphere_num, page=0)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pointa_back_questions_\d+$"))
async def cb_pointa_back_questions(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    sphere_num = int(callback.data.split("_")[-1])
    await _show_sphere_questions(callback, state, session, user, sphere_num, page=0)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pointa_page_\d+_\d+$"))
async def cb_pointa_page(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    sphere_num = int(parts[2])
    page = int(parts[3])
    await _show_sphere_questions(callback, state, session, user, sphere_num, page=page)
    await callback.answer()


async def _show_sphere_questions(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    sphere_num: int,
    page: int,
) -> None:
    sphere_name = await queries.get_sphere_name(session, sphere_num)
    answers = await _build_full_sphere_list(session, user.telegram_id, sphere_num)

    await state.set_state(PointAFSM.viewing_questions)
    await state.update_data(current_sphere=sphere_num, current_page=page)

    if not answers:
        text = (
            f"📍 <b>Сфера {sphere_num}: {sphere_name}</b>\n\n"
            "В этой сфере пока нет вопросов."
        )
    else:
        total = len(answers)
        answered = sum(1 for a in answers if a.answer_text and not a.is_skipped)
        skipped = sum(1 for a in answers if a.is_skipped)
        empty = sum(1 for a in answers if not a.answer_text and not a.is_skipped)

        total_pages = max(1, (total + QUESTIONS_PER_PAGE - 1) // QUESTIONS_PER_PAGE)

        text = (
            f"📍 <b>Сфера {sphere_num}: {sphere_name}</b>\n\n"
            f"Вопросов: {total} • отвечено: {answered}"
        )
        if skipped:
            text += f" • пропущено: {skipped}"
        if empty:
            text += f" • без ответа: {empty}"
        text += (
            f"\n\n<i>Страница {page + 1} из {total_pages}</i>\n\n"
            "Выбери вопрос, чтобы посмотреть или изменить ответ:"
        )

    await callback.message.edit_text(
        text,
        reply_markup=_questions_kb(sphere_num, answers, page),
        parse_mode="HTML",
    )


# ── Open a question → show current answer ───────────────────────────────────

@router.callback_query(F.data.regexp(r"^pointa_q_\d+_\d+$"))
async def cb_pointa_question(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    sphere_num = int(parts[2])
    question_num = int(parts[3])

    answers = await _build_full_sphere_list(session, user.telegram_id, sphere_num)
    answer = next(
        (a for a in answers if a.question_number == question_num),
        None,
    )
    if answer is None:
        await callback.answer("Вопрос не найден", show_alert=True)
        return

    sphere_name = await queries.get_sphere_name(session, sphere_num)

    if answer.is_skipped:
        answer_block = "⏭ <i>Ты пропустил(а) этот вопрос.</i>"
    elif answer.answer_text:
        voice_mark = " 🎤" if answer.is_voice else ""
        answer_block = f"{answer.answer_text}{voice_mark}"
    else:
        answer_block = "<i>Ответа пока нет.</i>"

    text = (
        f"📍 <b>Сфера {sphere_num}: {sphere_name}</b>\n"
        f"Вопрос {question_num}\n\n"
        f"<b>❓ {answer.question_text}</b>\n\n"
        f"<b>Твой ответ:</b>\n{answer_block}"
    )

    await state.set_state(PointAFSM.viewing_answer)
    await state.update_data(
        current_sphere=sphere_num,
        current_question=question_num,
        question_text=answer.question_text,
    )

    # Длинный ответ может не влезть в 4096 символов — обрезаем по последней возможности
    if len(text) > 4000:
        text = text[:3997] + "..."

    await callback.message.edit_text(
        text,
        reply_markup=_answer_view_kb(sphere_num),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Start editing ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "pointa_edit", PointAFSM.viewing_answer)
async def cb_pointa_edit(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if not await _is_completed(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    sphere_num = data.get("current_sphere")
    question_num = data.get("current_question")
    if not sphere_num or not question_num:
        await callback.answer("Ошибка состояния", show_alert=True)
        return

    await state.set_state(PointAFSM.editing_answer)
    # Обнуляем батч перед новым вводом
    await state.update_data(batch_fragments=[], batch_last_message=None)

    await callback.message.answer(
        "✏️ Отправь новый ответ текстом или голосом.\n\n"
        "<i>Если пришлёшь несколько сообщений подряд — я объединю их в один ответ.</i>",
        reply_markup=_editing_cancel_kb(sphere_num, question_num),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Answer handler (text or voice) ───────────────────────────────────────────

@router.message(
    PointAFSM.editing_answer,
    F.content_type.in_({ContentType.TEXT, ContentType.VOICE}),
)
async def on_edit_input(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    **kwargs,
):
    user: Optional[User] = kwargs.get("user")
    if user is None:
        return
    if not await _is_completed(user):
        await message.answer("Нет доступа")
        await state.clear()
        return

    user_id = user.telegram_id
    is_voice = message.content_type == ContentType.VOICE

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

    # Батчинг: копим фрагменты, сбрасываем после паузы
    data = await state.get_data()
    fragments: list[str] = data.get("batch_fragments", [])
    fragments.append(text)
    await state.update_data(
        batch_fragments=fragments,
        batch_last_message=message,
        batch_is_voice=is_voice or data.get("batch_is_voice", False),
    )

    # Отменяем предыдущий таск
    old_task = _batch_tasks.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()

    _batch_tasks[user_id] = asyncio.create_task(
        _flush_batch(user_id, bot, session, state)
    )


async def _flush_batch(
    user_id: int,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Ждёт паузу, объединяет фрагменты и сохраняет новый ответ."""
    try:
        await asyncio.sleep(BATCH_DELAY_SECONDS)
    except asyncio.CancelledError:
        return

    data = await state.get_data()
    fragments: list[str] = data.get("batch_fragments", []) or []
    last_message: Optional[Message] = data.get("batch_last_message")
    is_voice: bool = data.get("batch_is_voice", False)
    sphere_num = data.get("current_sphere")
    question_num = data.get("current_question")
    question_text = data.get("question_text", "")

    await state.update_data(
        batch_fragments=[],
        batch_last_message=None,
        batch_is_voice=False,
    )
    _batch_tasks.pop(user_id, None)

    if not fragments or last_message is None:
        return
    if not sphere_num or not question_num:
        return

    combined = "\n".join(fragments).strip()
    if not combined:
        return

    try:
        await queries.save_answer(
            session,
            user_id=user_id,
            sphere_number=sphere_num,
            question_number=question_num,
            question_text=question_text,
            answer_text=combined,
            is_voice=is_voice,
            is_skipped=False,
        )
    except Exception as exc:
        logger.exception(
            "Failed to save edited answer for user %d: %s", user_id, exc
        )
        await last_message.answer(
            "⚠️ Не удалось сохранить ответ. Попробуй ещё раз."
        )
        return

    # Помечаем, что была хотя бы одна правка — и планируем регенерацию стратегии
    await state.update_data(pointa_edited_any=True)
    schedule_strategy_regen(bot, user_id)

    # Показываем клиенту обновлённый ответ с клавиатурой возврата
    sphere_name = await queries.get_sphere_name(session, sphere_num)
    display_text = (
        f"✅ Ответ обновлён.\n\n"
        f"📍 <b>Сфера {sphere_num}: {sphere_name}</b>\n"
        f"Вопрос {question_num}\n\n"
        f"<b>❓ {question_text}</b>\n\n"
        f"<b>Твой ответ:</b>\n{combined}"
    )
    if len(display_text) > 4000:
        display_text = display_text[:3997] + "..."

    await state.set_state(PointAFSM.viewing_answer)

    chunks = split_long_message(display_text)
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            await last_message.answer(
                chunk,
                reply_markup=_answer_view_kb(sphere_num),
                parse_mode="HTML",
            )
        else:
            await last_message.answer(chunk, parse_mode="HTML")
