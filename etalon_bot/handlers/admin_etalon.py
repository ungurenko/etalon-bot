"""Загрузка и редактирование эталонной версии клиента (admin).

Два режима:
  1. Свободная форма — админ записывает голосовое/текст, LLM структурирует в 7 блоков
  2. По блокам — поэтапный ввод 7 блоков (как было раньше)
"""
from __future__ import annotations

import logging

from aiogram import Router, F, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import User, UserRole, OnboardingStatus
from etalon_bot.database.queries import (
    get_user,
    get_all_users,
    get_etalon_for_user,
    save_etalon_block,
    delete_etalon_for_user,
)
from etalon_bot.keyboards.admin_kb import (
    etalon_preview_kb,
    etalon_edit_blocks_kb,
    etalon_method_kb,
    back_to_admin_kb,
    confirm_kb,
)
from etalon_bot.services.whisper_service import transcribe_voice
from etalon_bot.services.llm_service import call_llm, LLMError
from etalon_bot.services.context_builder import build_structure_etalon_prompt
from etalon_bot.services.etalon_service import (
    ETALON_BLOCKS,
    format_preview,
    parse_structured_etalon,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_etalon")


# ── FSM ──


class EtalonFSM(StatesGroup):
    waiting_freeform = State()   # ожидание голосового/текста в свободной форме
    entering_block = State()     # поблочный ввод (7 шагов)
    preview = State()            # превью перед сохранением
    editing_block = State()      # редактирование отдельного блока


# ── Helpers ──


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


_format_preview = format_preview
_parse_structured_etalon = parse_structured_etalon


def _get_block_prompt(block_num: int, client_name: str) -> str:
    _, block_name, description = ETALON_BLOCKS[block_num - 1]
    return (
        f"📋 Загрузка эталонной версии для: {client_name}\n\n"
        f"Блок {block_num} из 7: {block_name}\n"
        f"{description}"
    )


# ── Etalon list (from admin panel) ──


@router.callback_query(F.data == "admin_etalons")
async def cb_admin_etalons_list(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    """Показать список клиентов для загрузки/просмотра эталона."""
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    clients = await get_all_users(session)
    # Только не-админы
    clients = [c for c in clients if c.role != UserRole.admin]

    if not clients:
        await callback.message.edit_text(
            "Клиентов пока нет.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    lines = ["📝 <b>Эталонные версии</b>\n", "Выберите клиента:\n"]
    builder = InlineKeyboardBuilder()
    for c in clients:
        etalon = await get_etalon_for_user(session, c.id)
        status = "✅" if etalon else "❌"
        name = c.display_name or c.username or str(c.telegram_id)
        builder.button(
            text=f"{status} {name}",
            callback_data=f"admin_etalon_{c.telegram_id}",
        )
    builder.button(text="🔙 Админ-панель", callback_data="admin_back")
    builder.adjust(1)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Entry point: choose method ──


@router.callback_query(F.data.regexp(r"^admin_etalon_\d+$"))
async def cb_admin_etalon(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    if client.onboarding_status != OnboardingStatus.completed:
        await callback.message.edit_text(
            "⚠️ Клиент ещё не завершил онбординг.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    existing = await get_etalon_for_user(session, telegram_id)
    if existing:
        await state.update_data(target_id=telegram_id)
        name = client.display_name or client.full_name or str(telegram_id)
        await callback.message.edit_text(
            f"У клиента «{name}» уже есть эталонная версия. Перезаписать?",
            reply_markup=confirm_kb("etalon_overwrite"),
        )
        await callback.answer()
        return

    await _show_method_choice(callback, client, session)
    await callback.answer()


async def _show_method_choice(callback: CallbackQuery, client: User, session: AsyncSession):
    """Показать выбор метода загрузки + фото клиента."""
    name = client.display_name or client.full_name
    bot = callback.bot

    if client.photo_file_id:
        try:
            await bot.send_photo(
                callback.from_user.id,
                client.photo_file_id,
                caption=f"📸 Фото клиента: {name}",
            )
        except Exception as exc:
            logger.warning("Failed to send client photo: %s", exc)

    await callback.message.edit_text(
        f"Загрузка эталонной версии для: {name}\n\n"
        "Выберите способ загрузки:",
        reply_markup=etalon_method_kb(client.telegram_id),
    )


@router.callback_query(F.data == "confirm_etalon_overwrite")
async def cb_confirm_etalon_overwrite(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    if not target_id:
        await callback.answer("Ошибка", show_alert=True)
        return

    await delete_etalon_for_user(session, target_id)
    client = await get_user(session, target_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await _show_method_choice(callback, client, session)
    await callback.answer()


@router.callback_query(F.data == "cancel_etalon_overwrite")
async def cb_cancel_etalon_overwrite(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text("Отменено.", reply_markup=back_to_admin_kb())
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# МЕТОД 1: Свободная форма (голосовое / текст → LLM структурирует)
# ══════════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.regexp(r"^etalon_freeform_\d+$"))
async def cb_etalon_freeform(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    name = client.display_name or client.full_name
    await state.set_state(EtalonFSM.waiting_freeform)
    await state.update_data(target_id=telegram_id, client_name=name, blocks={})

    await callback.message.edit_text(
        f"🎙 Запишите голосовое сообщение с описанием эталонной версии {name}.\n\n"
        "Можно описать всё в свободной форме — я структурирую ваши мысли по 7 блокам.\n\n"
        "Или отправьте текстом."
    )
    await callback.answer()


@router.message(EtalonFSM.waiting_freeform, F.voice)
async def on_freeform_voice(message: Message, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        return

    await message.answer("🎤 Транскрибирую голосовое...")

    try:
        raw_text = await transcribe_voice(bot, message.voice.file_id)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        await message.answer(f"⚠️ Ошибка транскрипции: {e}\nПопробуйте ещё раз или отправьте текстом.")
        return

    await _structure_and_preview(message, state, raw_text, bot)


@router.message(EtalonFSM.waiting_freeform, F.text)
async def on_freeform_text(message: Message, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        return

    await _structure_and_preview(message, state, message.text.strip(), bot)


async def _structure_and_preview(message: Message, state: FSMContext, raw_text: str, bot: Bot):
    """Отправить текст в LLM для структурирования, показать превью."""
    data = await state.get_data()
    client_name = data.get("client_name", "")

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await message.answer("⏳ Структурирую по 7 блокам...")

    try:
        system_prompt, user_prompt = build_structure_etalon_prompt(raw_text)
        llm_response = await call_llm(system_prompt, user_prompt, max_tokens=2000)
    except LLMError as e:
        logger.error("LLM error structuring etalon: %s", e)
        await message.answer(
            "⚠️ Не удалось структурировать текст. Попробуйте ещё раз или используйте режим «По блокам»."
        )
        return

    blocks = _parse_structured_etalon(llm_response)
    await state.update_data(blocks=blocks, raw_text=raw_text)
    await state.set_state(EtalonFSM.preview)

    preview = _format_preview(client_name, blocks)
    await message.answer(preview, reply_markup=etalon_preview_kb())


# ══════════════════════════════════════════════════════════════════════════════
# МЕТОД 2: По блокам (7 шагов, как раньше)
# ══════════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.regexp(r"^etalon_blocks_\d+$"))
async def cb_etalon_blocks(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await _start_block_entry(callback.message, state, client)
    await callback.answer()


async def _start_block_entry(message, state: FSMContext, client: User):
    name = client.display_name or client.full_name or str(client.telegram_id)
    await state.set_state(EtalonFSM.entering_block)
    await state.update_data(
        target_id=client.telegram_id,
        client_name=name,
        current_block=1,
        blocks={},
    )

    prompt = _get_block_prompt(1, name)
    try:
        await message.edit_text(prompt)
    except Exception:
        await message.answer(prompt)


@router.message(EtalonFSM.entering_block, F.text)
async def on_block_text(message: Message, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    blocks = data.get("blocks", {})
    current_block = data.get("current_block", 1)
    client_name = data.get("client_name", "")

    blocks[current_block] = message.text.strip()
    current_block += 1

    if current_block > 7:
        await state.update_data(blocks=blocks, current_block=current_block)
        await state.set_state(EtalonFSM.preview)
        preview = _format_preview(client_name, blocks)
        await message.answer(preview, reply_markup=etalon_preview_kb())
    else:
        await state.update_data(blocks=blocks, current_block=current_block)
        prompt = _get_block_prompt(current_block, client_name)
        await message.answer(prompt)


@router.message(EtalonFSM.entering_block, F.voice)
async def on_block_voice(message: Message, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        return

    await message.answer("🎤 Транскрибирую голосовое...")

    try:
        text = await transcribe_voice(bot, message.voice.file_id)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        await message.answer(f"⚠️ Ошибка транскрипции: {e}\nОтправьте текстом.")
        return

    data = await state.get_data()
    blocks = data.get("blocks", {})
    current_block = data.get("current_block", 1)
    client_name = data.get("client_name", "")

    blocks[current_block] = text
    current_block += 1

    if current_block > 7:
        await state.update_data(blocks=blocks, current_block=current_block)
        await state.set_state(EtalonFSM.preview)
        preview = _format_preview(client_name, blocks)
        await message.answer(preview, reply_markup=etalon_preview_kb())
    else:
        await state.update_data(blocks=blocks, current_block=current_block)
        prompt = _get_block_prompt(current_block, client_name)
        await message.answer(prompt)


# ══════════════════════════════════════════════════════════════════════════════
# Preview actions (общие для обоих методов)
# ══════════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data == "etalon_save", EtalonFSM.preview)
async def cb_etalon_save(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    blocks = data.get("blocks", {})
    client_name = data.get("client_name", "")

    if not target_id or not blocks:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        await state.clear()
        return

    for block_num, content in blocks.items():
        num = int(block_num)
        _, block_name, _ = ETALON_BLOCKS[num - 1]
        await save_etalon_block(session, target_id, num, block_name, content)

    await state.clear()

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Сгенерировать стратегию",
        callback_data=f"admin_gen_strategy_{target_id}",
    )
    builder.button(text="🔙 Админ-панель", callback_data="admin_back")
    builder.adjust(1, 1)

    await callback.message.edit_text(
        f"✅ Эталонная версия для «{client_name}» сохранена.\n\n"
        f"Сгенерировать стратегию для {client_name}?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "etalon_edit", EtalonFSM.preview)
async def cb_etalon_edit(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите блок для редактирования:",
        reply_markup=etalon_edit_blocks_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "etalon_back_preview", EtalonFSM.preview)
async def cb_etalon_back_preview(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    client_name = data.get("client_name", "")
    blocks = data.get("blocks", {})

    preview = _format_preview(client_name, blocks)
    await callback.message.edit_text(preview, reply_markup=etalon_preview_kb())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^etalon_edit_block_\d+$"))
async def cb_etalon_edit_block(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    block_num = int(callback.data.split("_")[-1])
    data = await state.get_data()
    client_name = data.get("client_name", "")
    blocks = data.get("blocks", {})

    current_text = blocks.get(block_num, blocks.get(str(block_num), "—"))
    _, block_name, _ = ETALON_BLOCKS[block_num - 1]

    await state.set_state(EtalonFSM.editing_block)
    await state.update_data(editing_block_num=block_num)

    await callback.message.edit_text(
        f"✏️ Редактирование блока {block_num}: {block_name}\n\n"
        f"Текущий текст:\n{current_text}\n\n"
        f"Отправьте новый текст для этого блока:"
    )
    await callback.answer()


@router.message(EtalonFSM.editing_block, F.text)
async def on_edit_text(message: Message, user: User, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    block_num = data.get("editing_block_num")
    blocks = data.get("blocks", {})
    client_name = data.get("client_name", "")

    if block_num is None:
        await state.clear()
        return

    blocks[block_num] = message.text.strip()
    await state.update_data(blocks=blocks)
    await state.set_state(EtalonFSM.preview)

    preview = _format_preview(client_name, blocks)
    await message.answer(preview, reply_markup=etalon_preview_kb())


@router.message(EtalonFSM.editing_block, F.voice)
async def on_edit_voice(message: Message, user: User, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        return

    await message.answer("🎤 Транскрибирую голосовое...")

    try:
        text = await transcribe_voice(bot, message.voice.file_id)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        await message.answer(f"⚠️ Ошибка транскрипции: {e}\nОтправьте текстом.")
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
    await state.set_state(EtalonFSM.preview)

    preview = _format_preview(client_name, blocks)
    await message.answer(preview, reply_markup=etalon_preview_kb())


# ── Cancel ──


@router.callback_query(F.data == "etalon_cancel")
async def cb_etalon_cancel(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text("🗑 Отменено.", reply_markup=back_to_admin_kb())
    await callback.answer()
