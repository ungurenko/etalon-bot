"""План на произвольный период с гарантированным охватом всех 8 сфер.

FSM-флоу:
1. menu_period_plan → выбор длительности (30 / 60 / 90 дней / Другой срок).
2. period_plan_<N> → сразу генерация.
3. period_plan_custom → запрос текста («45 дней», «3 месяца»); сохраняем в FSM
   и генерируем.

Результат сохраняется в историю разговора (MessageRole.assistant), чтобы
последующие чаты видели план в контексте.
"""
from __future__ import annotations

import logging

from aiogram import Router, F, Bot
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database import queries
from etalon_bot.database.models import (
    MessageRole,
    MessageType,
    OnboardingStatus,
    User,
)
from etalon_bot.services.strategy_service import generate_period_plan
from etalon_bot.utils.text_utils import (
    markdown_to_telegram_html,
    split_long_message,
)

logger = logging.getLogger(__name__)

router = Router(name="client_period_plan")

MAX_CUSTOM_PERIOD_LENGTH = 80


class PeriodPlanFSM(StatesGroup):
    entering_custom = State()


# ── Keyboards ─────────────────────────────────────────────────────────────


def _duration_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="30 дней", callback_data="period_plan_30")
    builder.button(text="60 дней", callback_data="period_plan_60")
    builder.button(text="90 дней", callback_data="period_plan_90")
    builder.button(text="✍️ Другой срок", callback_data="period_plan_custom")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def _after_plan_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Задать вопрос", callback_data="menu_chat")
    builder.button(text="📋 Мой план", callback_data="menu_plan")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    builder.adjust(2, 1)
    return builder.as_markup()


# ── Eligibility ───────────────────────────────────────────────────────────


async def _can_make_period_plan(session: AsyncSession, user: User) -> tuple[bool, str]:
    """Возвращает (готов, причина если не готов)."""
    if user.onboarding_status != OnboardingStatus.completed:
        return False, "сначала пройди анкету Точки А"
    if not await queries.has_etalon(session, user.telegram_id):
        return False, "сначала загрузи свою эталонную версию"
    return True, ""


# ── Handlers ──────────────────────────────────────────────────────────────


@router.callback_query(F.data == "menu_period_plan")
async def cb_period_plan_menu(
    callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
):
    await state.clear()

    ok, reason = await _can_make_period_plan(session, user)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return

    text = (
        "📅 <b>План на период</b>\n\n"
        "Я составлю план развития на выбранный срок так, чтобы он "
        "покрыл <b>все 8 сфер</b> твоей жизни, а не одну тему. "
        "Это хороший способ увидеть картину целиком и понять, "
        "с чего начать.\n\n"
        "На какой срок нужен план?"
    )

    try:
        await callback.message.edit_text(
            text, reply_markup=_duration_kb(), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=_duration_kb(), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^period_plan_\d+$"))
async def cb_period_plan_preset(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot,
    state: FSMContext,
):
    await state.clear()

    ok, reason = await _can_make_period_plan(session, user)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return

    days = int(callback.data.rsplit("_", 1)[-1])
    period_label = f"{days} дней"

    await _run_generation(callback.message, user, session, bot, period_label)
    await callback.answer()


@router.callback_query(F.data == "period_plan_custom")
async def cb_period_plan_custom_prompt(
    callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
):
    ok, reason = await _can_make_period_plan(session, user)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return

    await state.set_state(PeriodPlanFSM.entering_custom)

    text = (
        "Напиши свой срок одним сообщением.\n\n"
        "Примеры: «45 дней», «2 месяца», «полгода», «до 1 сентября»."
    )
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)
    await callback.answer()


@router.message(PeriodPlanFSM.entering_custom, F.text)
async def on_period_plan_custom_text(
    message: Message, user: User, session: AsyncSession, state: FSMContext,
    bot: Bot,
):
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пустой текст — попробуй ещё раз.")
        return

    period_label = raw[:MAX_CUSTOM_PERIOD_LENGTH]
    await state.clear()

    await _run_generation(message, user, session, bot, period_label)


@router.message(PeriodPlanFSM.entering_custom)
async def on_period_plan_custom_nontext(message: Message):
    """Если в этом FSM-состоянии прилетает не текст — просим прислать текстом."""
    await message.answer(
        "Жду срок текстом. Например: «45 дней» или «3 месяца».\n"
        "Если передумала — нажми /menu."
    )


# ── Generation ────────────────────────────────────────────────────────────


async def _run_generation(
    source_message: Message,
    user: User,
    session: AsyncSession,
    bot: Bot,
    period_label: str,
) -> None:
    """Общая часть: прогресс, LLM-вызов, отправка, сохранение в историю."""
    name = user.display_name or user.full_name or "друг"

    # Progress notice
    progress_text = f"⏳ Готовлю план на {period_label}... Это займёт около минуты 💫"
    try:
        await source_message.edit_text(progress_text)
    except Exception:
        await source_message.answer(progress_text)

    try:
        await bot.send_chat_action(source_message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    try:
        plan_text = await generate_period_plan(session, user, period_label)
    except Exception as exc:
        logger.exception(
            "Period plan generation failed for %d (%s): %s",
            user.telegram_id, period_label, exc,
        )
        await source_message.answer(
            "⚠️ Не удалось составить план сейчас. "
            "Попробуй ещё раз через минуту 🙏"
        )
        return

    # Сохраняем ответ бота в историю, чтобы чат-LLM видел план в контексте
    try:
        await queries.save_message(
            session,
            user.telegram_id,
            MessageRole.assistant,
            plan_text,
            MessageType.text,
        )
    except Exception as exc:
        logger.warning(
            "Failed to save period plan for %d in history: %s",
            user.telegram_id, exc,
        )

    header = f"📅 <b>{name}</b>, твой план на {period_label} готов.\n\n"
    body = markdown_to_telegram_html(plan_text)
    footer = (
        "\n\n———\n"
        "Это ориентир на период. Можно задать вопрос или посмотреть "
        "свой этап стратегии 💛"
    )

    full_message = f"{header}{body}{footer}"
    chunks = split_long_message(full_message)
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            await source_message.answer(chunk, reply_markup=_after_plan_kb())
        else:
            await source_message.answer(chunk)
