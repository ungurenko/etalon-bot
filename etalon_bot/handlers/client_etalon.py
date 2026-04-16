"""Загрузка эталонной версии клиентом."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import User, OnboardingStatus, StrategyStatus, ImageMoment
from etalon_bot.database.queries import (
    get_etalon_for_user,
    save_etalon_block,
    delete_etalon_for_user,
)
from etalon_bot.services.etalon_service import (
    ETALON_BLOCKS,
    format_preview,
    get_block_prompt,
    parse_structured_etalon,
)
from etalon_bot.services.whisper_service import transcribe_voice
from etalon_bot.services.llm_service import call_llm, LLMError
from etalon_bot.services.context_builder import build_structure_etalon_prompt
from etalon_bot.services.image_service import fire_and_forget_moment

logger = logging.getLogger(__name__)

router = Router(name="client_etalon")


class ClientEtalonFSM(StatesGroup):
    waiting_freeform = State()
    entering_block = State()
    preview = State()
    editing_block = State()


# Freeform batching: Telegram (and the user's clipboard) may split a long
# etalon description into multiple messages within a few hundred ms. We
# accumulate fragments and trigger a single LLM call after a quiet period
# so we don't fire 5 parallel requests and hit the provider rate limit.
FREEFORM_BATCH_DELAY_SECONDS = 5
_freeform_batch_tasks: dict[int, asyncio.Task] = {}
_freeform_batches: dict[int, dict] = {}


# ── Keyboards ──


def _method_kb():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎙 Голосом/текстом (свободная форма)",
        callback_data="client_etalon_freeform",
    )
    builder.button(
        text="📝 По блокам (7 шагов)",
        callback_data="client_etalon_blocks",
    )
    builder.button(text="🔙 Назад", callback_data="menu_back")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def _preview_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data="client_etalon_save")
    builder.button(text="✏️ Редактировать блок", callback_data="client_etalon_edit")
    builder.button(text="🗑 Отменить", callback_data="client_etalon_cancel")
    builder.adjust(2, 1)
    return builder.as_markup()


def _edit_blocks_kb():
    builder = InlineKeyboardBuilder()
    for n in range(1, 8):
        builder.button(
            text=f"Блок {n}", callback_data=f"client_etalon_edit_block_{n}"
        )
    builder.button(text="🔙 Назад к превью", callback_data="client_etalon_back_preview")
    builder.adjust(4, 3, 1)
    return builder.as_markup()


# ── Entry point ──


@router.callback_query(F.data == "client_etalon_start")
async def cb_client_etalon_start(
    callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
):
    if user.onboarding_status != OnboardingStatus.completed:
        await callback.answer("Сначала пройди онбординг", show_alert=True)
        return

    existing = await get_etalon_for_user(session, user.telegram_id)
    if existing:
        builder = InlineKeyboardBuilder()
        builder.button(text="Да, перезаписать", callback_data="client_etalon_overwrite")
        builder.button(text="Нет, оставить", callback_data="menu_back")
        builder.adjust(2)
        await state.update_data(target_id=user.telegram_id)
        try:
            await callback.message.edit_text(
                "У тебя уже есть эталонная версия. Перезаписать?",
                reply_markup=builder.as_markup(),
            )
        except Exception:
            await callback.message.answer(
                "У тебя уже есть эталонная версия. Перезаписать?",
                reply_markup=builder.as_markup(),
            )
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            "📝 Загрузка эталонной версии\n\n" "Выбери способ загрузки:",
            reply_markup=_method_kb(),
        )
    except Exception:
        await callback.message.answer(
            "📝 Загрузка эталонной версии\n\n" "Выбери способ загрузки:",
            reply_markup=_method_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "client_etalon_overwrite")
async def cb_client_etalon_overwrite(
    callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
):
    await delete_etalon_for_user(session, user.telegram_id)
    try:
        await callback.message.edit_text(
            "📝 Загрузка эталонной версии\n\nВыбери способ загрузки:",
            reply_markup=_method_kb(),
        )
    except Exception:
        await callback.message.answer(
            "📝 Загрузка эталонной версии\n\nВыбери способ загрузки:",
            reply_markup=_method_kb(),
        )
    await callback.answer()


# ── Freeform mode ──


@router.callback_query(F.data == "client_etalon_freeform")
async def cb_freeform(
    callback: CallbackQuery, user: User, state: FSMContext
):
    name = user.display_name or user.full_name or "друг"
    await state.set_state(ClientEtalonFSM.waiting_freeform)
    await state.update_data(target_id=user.telegram_id, client_name=name, blocks={})
    try:
        await callback.message.edit_text(
            "🎙 Запиши голосовое или напиши текстом описание своей эталонной версии.\n\n"
            "Опиши свободно — я структурирую по 7 блокам.\n\n"
            "💡 Если текст длинный и ты отправишь его несколькими сообщениями — ничего страшного, я соберу всё вместе."
        )
    except Exception:
        await callback.message.answer(
            "🎙 Запиши голосовое или напиши текстом описание своей эталонной версии.\n\n"
            "Опиши свободно — я структурирую по 7 блокам.\n\n"
            "💡 Если текст длинный и ты отправишь его несколькими сообщениями — ничего страшного, я соберу всё вместе."
        )
    await callback.answer()


@router.message(ClientEtalonFSM.waiting_freeform, F.voice)
async def on_freeform_voice(
    message: Message, user: User, state: FSMContext, bot: Bot, **kwargs
):
    await message.answer("🎤 Транскрибирую...")
    try:
        raw_text = await transcribe_voice(bot, message.voice.file_id)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        await message.answer(
            f"⚠️ Ошибка: {e}\nПопробуй ещё раз или отправь текстом."
        )
        return
    await _structure_and_preview(message, state, raw_text, bot)


async def _flush_freeform_batch(user_id: int, state: FSMContext, bot: Bot):
    try:
        await asyncio.sleep(FREEFORM_BATCH_DELAY_SECONDS)
    except asyncio.CancelledError:
        return

    _freeform_batch_tasks.pop(user_id, None)
    batch = _freeform_batches.pop(user_id, None)
    if not batch or not batch.get("fragments"):
        return

    full_text = "\n".join(batch["fragments"]).strip()
    if not full_text:
        return

    last_message: Message = batch["last_message"]
    await _structure_and_preview(last_message, state, full_text, bot)


@router.message(ClientEtalonFSM.waiting_freeform, F.text)
async def on_freeform_text(
    message: Message, user: User, state: FSMContext, bot: Bot, **kwargs
):
    user_id = message.from_user.id
    batch = _freeform_batches.setdefault(
        user_id, {"fragments": [], "last_message": None}
    )
    batch["fragments"].append(message.text.strip())
    batch["last_message"] = message

    old_task = _freeform_batch_tasks.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()

    _freeform_batch_tasks[user_id] = asyncio.create_task(
        _flush_freeform_batch(user_id, state, bot)
    )


async def _structure_and_preview(
    message: Message, state: FSMContext, raw_text: str, bot: Bot
):
    data = await state.get_data()
    client_name = data.get("client_name", "")

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await message.answer("⏳ Структурирую по 7 блокам...")

    try:
        system_prompt, user_prompt = build_structure_etalon_prompt(raw_text)
        llm_response = await call_llm(
            system_prompt, user_prompt, max_tokens=2000, timeout=120
        )
    except LLMError as e:
        logger.error("LLM error: %s", e)
        await message.answer(
            "⚠️ Не удалось структурировать. Попробуй ещё раз "
            "или используй режим «По блокам»."
        )
        return

    blocks = parse_structured_etalon(llm_response)
    await state.update_data(blocks=blocks, raw_text=raw_text)
    await state.set_state(ClientEtalonFSM.preview)

    preview = format_preview(client_name, blocks)
    await message.answer(preview, reply_markup=_preview_kb())


# ── Block-by-block mode ──


@router.callback_query(F.data == "client_etalon_blocks")
async def cb_blocks(
    callback: CallbackQuery, user: User, state: FSMContext
):
    name = user.display_name or user.full_name or "друг"
    await state.set_state(ClientEtalonFSM.entering_block)
    await state.update_data(
        target_id=user.telegram_id, client_name=name, current_block=1, blocks={}
    )

    prompt = get_block_prompt(1, name)
    try:
        await callback.message.edit_text(prompt)
    except Exception:
        await callback.message.answer(prompt)
    await callback.answer()


@router.message(ClientEtalonFSM.entering_block, F.text)
async def on_block_text(message: Message, user: User, state: FSMContext, **kwargs):
    data = await state.get_data()
    blocks = data.get("blocks", {})
    current_block = data.get("current_block", 1)
    client_name = data.get("client_name", "")

    blocks[current_block] = message.text.strip()
    current_block += 1

    if current_block > 7:
        await state.update_data(blocks=blocks, current_block=current_block)
        await state.set_state(ClientEtalonFSM.preview)
        preview = format_preview(client_name, blocks)
        await message.answer(preview, reply_markup=_preview_kb())
    else:
        await state.update_data(blocks=blocks, current_block=current_block)
        prompt = get_block_prompt(current_block, client_name)
        await message.answer(prompt)


@router.message(ClientEtalonFSM.entering_block, F.voice)
async def on_block_voice(
    message: Message, user: User, state: FSMContext, bot: Bot, **kwargs
):
    await message.answer("🎤 Транскрибирую...")
    try:
        text = await transcribe_voice(bot, message.voice.file_id)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}\nОтправь текстом.")
        return

    data = await state.get_data()
    blocks = data.get("blocks", {})
    current_block = data.get("current_block", 1)
    client_name = data.get("client_name", "")

    blocks[current_block] = text
    current_block += 1

    if current_block > 7:
        await state.update_data(blocks=blocks, current_block=current_block)
        await state.set_state(ClientEtalonFSM.preview)
        preview = format_preview(client_name, blocks)
        await message.answer(preview, reply_markup=_preview_kb())
    else:
        await state.update_data(blocks=blocks, current_block=current_block)
        prompt = get_block_prompt(current_block, client_name)
        await message.answer(prompt)


# ── Preview actions ──


@router.callback_query(F.data == "client_etalon_save", ClientEtalonFSM.preview)
async def cb_save(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
):
    data = await state.get_data()
    blocks = data.get("blocks", {})
    client_name = data.get("client_name", "")

    if not blocks:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        await state.clear()
        return

    for block_num, content in blocks.items():
        num = int(block_num)
        _, block_name, _ = ETALON_BLOCKS[num - 1]
        await save_etalon_block(session, user.telegram_id, num, block_name, content)

    await state.clear()

    builder = InlineKeyboardBuilder()
    if user.strategy_status != StrategyStatus.active:
        builder.button(text="✨ Составить стратегию сейчас", callback_data="client_gen_strategy")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    builder.adjust(1)

    success_text = "✅ Эталонная версия сохранена!\n\n"
    if user.strategy_status != StrategyStatus.active:
        success_text += (
            "Теперь я могу составить для тебя персональную стратегию развития "
            "на основе твоей Точки А, эталона и промежуточных целей. "
            "Хочешь — прямо сейчас ✨"
        )
    else:
        success_text += "Администратор получит уведомление 💫"

    await callback.message.edit_text(
        success_text,
        reply_markup=builder.as_markup(),
    )

    # Вдохновляющая картинка-мудборд эталонной версии (в фоне, не блокирует UX)
    fire_and_forget_moment(
        bot,
        user,
        ImageMoment.etalon_ready,
        caption="✨ Образ твоей эталонной версии",
    )

    # Notify admins
    uname = f"@{user.username}" if user.username else ""
    admin_text = f"📝 Клиент «{client_name}» {uname} загрузил свою эталонную версию."
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception as exc:
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)

    await callback.answer()


@router.callback_query(F.data == "client_etalon_edit", ClientEtalonFSM.preview)
async def cb_edit(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.message.edit_text(
        "Выбери блок для редактирования:", reply_markup=_edit_blocks_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "client_etalon_back_preview", ClientEtalonFSM.preview)
async def cb_back_preview(callback: CallbackQuery, state: FSMContext, **kwargs):
    data = await state.get_data()
    client_name = data.get("client_name", "")
    blocks = data.get("blocks", {})
    preview = format_preview(client_name, blocks)
    await callback.message.edit_text(preview, reply_markup=_preview_kb())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^client_etalon_edit_block_\d+$"))
async def cb_edit_block(callback: CallbackQuery, state: FSMContext, **kwargs):
    block_num = int(callback.data.split("_")[-1])
    data = await state.get_data()
    blocks = data.get("blocks", {})

    current_text = blocks.get(block_num, blocks.get(str(block_num), "—"))
    _, block_name, _ = ETALON_BLOCKS[block_num - 1]

    await state.set_state(ClientEtalonFSM.editing_block)
    await state.update_data(editing_block_num=block_num)

    await callback.message.edit_text(
        f"✏️ Блок {block_num}: {block_name}\n\n"
        f"Текущий текст:\n{current_text}\n\n"
        "Отправь новый текст:"
    )
    await callback.answer()


@router.message(ClientEtalonFSM.editing_block, F.text)
async def on_edit_text(message: Message, state: FSMContext, **kwargs):
    data = await state.get_data()
    block_num = data.get("editing_block_num")
    blocks = data.get("blocks", {})
    client_name = data.get("client_name", "")

    if block_num is None:
        await state.clear()
        return

    blocks[block_num] = message.text.strip()
    await state.update_data(blocks=blocks)
    await state.set_state(ClientEtalonFSM.preview)

    preview = format_preview(client_name, blocks)
    await message.answer(preview, reply_markup=_preview_kb())


@router.message(ClientEtalonFSM.editing_block, F.voice)
async def on_edit_voice(message: Message, state: FSMContext, bot: Bot, **kwargs):
    await message.answer("🎤 Транскрибирую...")
    try:
        text = await transcribe_voice(bot, message.voice.file_id)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}\nОтправь текстом.")
        return

    data = await state.get_data()
    block_num = data.get("editing_block_num")
    blocks = data.get("blocks", {})
    client_name = data.get("client_name", "")

    if block_num is None:
        await state.clear()
        return

    blocks[block_num] = text
    await state.update_data(blocks=blocks)
    await state.set_state(ClientEtalonFSM.preview)

    preview = format_preview(client_name, blocks)
    await message.answer(preview, reply_markup=_preview_kb())


@router.callback_query(F.data == "client_etalon_cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    await callback.message.edit_text("🗑 Отменено.", reply_markup=builder.as_markup())
    await callback.answer()
