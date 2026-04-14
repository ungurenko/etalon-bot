"""Рассылка сообщений активным клиентам."""

import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import User, UserRole
from etalon_bot.database.queries import (
    get_active_clients,
    update_user_field,
)
from etalon_bot.keyboards.admin_kb import (
    broadcast_preview_kb,
    back_to_admin_kb,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_broadcast")


# ── FSM ──


class BroadcastFSM(StatesGroup):
    compose = State()
    preview = State()


# ── Helpers ──


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


# ── Start broadcast ──


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BroadcastFSM.compose)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.button(text="❌ Отмена", callback_data="broadcast_cancel")
    cancel_kb.adjust(1)

    await callback.message.edit_text(
        "📨 Рассылка\n\n"
        "Введите текст рассылки. Поддерживается HTML-форматирование.\n\n"
        "Пример:\n"
        "<b>Жирный</b>, <i>курсив</i>, <a href='url'>ссылка</a>",
        reply_markup=cancel_kb.as_markup(),
    )
    await callback.answer()


# ── Compose ──


@router.message(BroadcastFSM.compose, F.text)
async def on_broadcast_compose(message: Message, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        return

    broadcast_text = message.text.strip()
    clients = await get_active_clients(session)
    count = len(clients)

    await state.update_data(broadcast_text=broadcast_text)
    await state.set_state(BroadcastFSM.preview)

    await message.answer(
        f"📨 Превью рассылки:\n\n{broadcast_text}\n\n"
        f"———\n"
        f"Отправить всем активным клиентам ({count} чел.)?",
        reply_markup=broadcast_preview_kb(count),
        parse_mode="HTML",
    )


# ── Send broadcast ──


@router.callback_query(F.data == "broadcast_send", BroadcastFSM.preview)
async def cb_broadcast_send(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text", "")

    if not broadcast_text:
        await callback.answer("Текст пустой", show_alert=True)
        return

    clients = await get_active_clients(session)
    total = len(clients)

    await callback.message.edit_text(
        f"⏳ Отправляю рассылку... 0/{total}"
    )
    await callback.answer()

    success = 0
    errors = 0

    for i, client in enumerate(clients):
        try:
            await bot.send_message(
                chat_id=client.telegram_id,
                text=broadcast_text,
                parse_mode="HTML",
            )
            success += 1
        except TelegramForbiddenError:
            # Бот заблокирован пользователем
            logger.warning("Клиент %d заблокировал бота", client.telegram_id)
            await update_user_field(session, client.telegram_id, bot_blocked=True)
            errors += 1
        except Exception as e:
            logger.error(
                "Ошибка отправки рассылки клиенту %d: %s (code=%s)",
                client.telegram_id,
                e,
                getattr(e, "code", "?"),
            )
            errors += 1

        # Задержка между отправками (Telegram rate limits)
        if i < total - 1:
            await asyncio.sleep(0.5)

        # Обновляем прогресс каждые 10 сообщений
        if (i + 1) % 10 == 0:
            try:
                await callback.message.edit_text(
                    f"⏳ Отправляю рассылку... {i + 1}/{total}"
                )
            except Exception:
                pass  # Telegram может отклонить edit, если текст не изменился

    await state.clear()

    await callback.message.edit_text(
        f"✅ Рассылка отправлена: {success} из {total}. Ошибок: {errors}.",
        reply_markup=back_to_admin_kb(),
    )


# ── Edit broadcast ──


@router.callback_query(F.data == "broadcast_edit", BroadcastFSM.preview)
async def cb_broadcast_edit(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(BroadcastFSM.compose)

    await callback.message.edit_text(
        "✏️ Введите новый текст рассылки:"
    )
    await callback.answer()


# ── Cancel broadcast ──


@router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ Рассылка отменена.",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()
