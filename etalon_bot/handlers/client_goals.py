"""Промежуточные цели и данные клиента."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database.models import User
from etalon_bot.database.queries import (
    save_intermediate_data,
    get_intermediate_data,
    delete_intermediate_data,
    get_intermediate_item,
)
from etalon_bot.services.whisper_service import transcribe_voice, TranscriptionError, FileTooLargeError
from etalon_bot.utils.text_utils import truncate_text

logger = logging.getLogger(__name__)

router = Router(name="client_goals")

_CATEGORIES = {
    "goal": "🎯 Цель",
    "insight": "💡 Инсайт",
    "course_notes": "📚 Из курса/обучения",
    "other": "📝 Другое",
}


class GoalsFSM(StatesGroup):
    choosing_category = State()
    entering_text = State()


# ── Keyboards ──

def _goals_menu_kb(has_items: bool = False):
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data="goals_add")
    if has_items:
        builder.button(text="📋 Мои записи", callback_data="goals_list")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    if has_items:
        builder.adjust(2, 1)
    else:
        builder.adjust(1, 1)
    return builder.as_markup()


def _category_kb():
    builder = InlineKeyboardBuilder()
    for key, label in _CATEGORIES.items():
        builder.button(text=label, callback_data=f"goals_cat_{key}")
    builder.button(text="🔙 Назад", callback_data="menu_goals")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _goals_list_kb(items, page: int = 0, per_page: int = 5):
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    for item in page_items:
        cat_emoji = _CATEGORIES.get(item.category, "📝").split()[0]
        preview = truncate_text(item.content, max_length=40)
        builder.button(
            text=f"{cat_emoji} {preview}",
            callback_data=f"goals_view_{item.id}",
        )

    nav = 0
    if page > 0:
        builder.button(text="◀️", callback_data=f"goals_page_{page - 1}")
        nav += 1
    if end < len(items):
        builder.button(text="▶️", callback_data=f"goals_page_{page + 1}")
        nav += 1

    builder.button(text="➕ Добавить", callback_data="goals_add")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")

    rows = [1] * len(page_items)
    if nav:
        rows.append(nav)
    rows.extend([1, 1])
    builder.adjust(*rows)
    return builder.as_markup()


def _goal_detail_kb(item_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"goals_delete_{item_id}")
    builder.button(text="🔙 К списку", callback_data="goals_list")
    builder.adjust(2)
    return builder.as_markup()


# ── Handlers ──

@router.callback_query(F.data == "menu_goals")
async def cb_goals_menu(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    await state.clear()
    items = await get_intermediate_data(session, user.telegram_id)
    count = len(items)

    text = "🎯 <b>Промежуточные цели и данные</b>\n\n"
    if count:
        text += f"У тебя {count} записей.\n"
    else:
        text += "Здесь ты можешь добавлять свои цели, инсайты из курсов и другие важные данные.\n"

    try:
        await callback.message.edit_text(
            text, reply_markup=_goals_menu_kb(has_items=count > 0), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=_goals_menu_kb(has_items=count > 0), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "goals_add")
async def cb_goals_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(GoalsFSM.choosing_category)
    try:
        await callback.message.edit_text(
            "Выбери категорию записи:", reply_markup=_category_kb()
        )
    except Exception:
        await callback.message.answer(
            "Выбери категорию записи:", reply_markup=_category_kb()
        )
    await callback.answer()


@router.callback_query(GoalsFSM.choosing_category, F.data.startswith("goals_cat_"))
async def cb_goals_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.replace("goals_cat_", "")
    cat_name = _CATEGORIES.get(category, category)
    await state.set_state(GoalsFSM.entering_text)
    await state.update_data(category=category)

    try:
        await callback.message.edit_text(
            f"{cat_name}\n\nОтправь текст или голосовое сообщение:"
        )
    except Exception:
        await callback.message.answer(
            f"{cat_name}\n\nОтправь текст или голосовое сообщение:"
        )
    await callback.answer()


@router.message(GoalsFSM.entering_text, F.content_type.in_({ContentType.TEXT, ContentType.VOICE}))
async def on_goal_text(message: Message, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    data = await state.get_data()
    category = data.get("category", "other")

    if message.content_type == ContentType.VOICE:
        try:
            text = await transcribe_voice(bot, message.voice.file_id)
        except FileTooLargeError:
            await message.answer("Голосовое слишком длинное. Попробуй покороче или текстом.")
            return
        except TranscriptionError:
            await message.answer("Не удалось распознать. Попробуй ещё раз или текстом.")
            return
    else:
        text = message.text or ""

    if not text.strip():
        await message.answer("Пустой текст — попробуй ещё раз.")
        return

    await save_intermediate_data(session, user.telegram_id, category, text.strip(), added_by="client")
    await state.clear()

    cat_name = _CATEGORIES.get(category, category)
    await message.answer(
        f"✅ Записано в «{cat_name}»!",
        reply_markup=_goals_menu_kb(has_items=True),
    )


@router.callback_query(F.data == "goals_list")
async def cb_goals_list(callback: CallbackQuery, user: User, session: AsyncSession):
    items = await get_intermediate_data(session, user.telegram_id)
    if not items:
        try:
            await callback.message.edit_text(
                "Записей пока нет.", reply_markup=_goals_menu_kb(has_items=False)
            )
        except Exception:
            await callback.message.answer(
                "Записей пока нет.", reply_markup=_goals_menu_kb(has_items=False)
            )
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            f"📋 Твои записи ({len(items)}):",
            reply_markup=_goals_list_kb(items, page=0),
        )
    except Exception:
        await callback.message.answer(
            f"📋 Твои записи ({len(items)}):",
            reply_markup=_goals_list_kb(items, page=0),
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^goals_page_\d+$"))
async def cb_goals_page(callback: CallbackQuery, user: User, session: AsyncSession):
    page = int(callback.data.split("_")[-1])
    items = await get_intermediate_data(session, user.telegram_id)
    try:
        await callback.message.edit_text(
            f"📋 Твои записи ({len(items)}):",
            reply_markup=_goals_list_kb(items, page=page),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.regexp(r"^goals_view_\d+$"))
async def cb_goals_view(callback: CallbackQuery, user: User, session: AsyncSession):
    item_id = int(callback.data.split("_")[-1])
    item = await get_intermediate_item(session, item_id)

    if not item or item.user_id != user.telegram_id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    cat_name = _CATEGORIES.get(item.category, item.category)
    date_str = item.created_at.strftime("%d.%m.%Y %H:%M") if item.created_at else ""
    added = "тобой" if item.added_by == "client" else "администратором"

    text = (
        f"{cat_name}\n"
        f"📅 {date_str} | Добавлено {added}\n\n"
        f"{item.content}"
    )

    try:
        await callback.message.edit_text(text, reply_markup=_goal_detail_kb(item_id))
    except Exception:
        await callback.message.answer(text, reply_markup=_goal_detail_kb(item_id))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^goals_delete_\d+$"))
async def cb_goals_delete(callback: CallbackQuery, user: User, session: AsyncSession):
    item_id = int(callback.data.split("_")[-1])
    item = await get_intermediate_item(session, item_id)

    if not item or item.user_id != user.telegram_id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    await delete_intermediate_data(session, item_id)
    await callback.answer("Удалено")

    # Refresh list
    items = await get_intermediate_data(session, user.telegram_id)
    if items:
        try:
            await callback.message.edit_text(
                f"📋 Твои записи ({len(items)}):",
                reply_markup=_goals_list_kb(items, page=0),
            )
        except Exception:
            pass
    else:
        try:
            await callback.message.edit_text(
                "Все записи удалены.",
                reply_markup=_goals_menu_kb(has_items=False),
            )
        except Exception:
            pass
