"""Главный роутер админ-панели: команда /admin, навигация, статистика."""

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import User, UserRole
from etalon_bot.database.queries import (
    get_clients_count,
    get_onboarding_stats,
    get_strategy_stats,
    get_activity_stats,
)
from etalon_bot.keyboards.admin_kb import admin_panel_kb, back_to_admin_kb

logger = logging.getLogger(__name__)

router = Router(name="admin_main")


def _is_admin(user: User) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


# ── /admin command ──


@router.message(Command("admin"))
async def cmd_admin(message: Message, user: User, session: AsyncSession):
    if not _is_admin(user):
        await message.answer("У вас нет доступа к админ-панели.")
        return

    await message.answer(
        "🔧 Админ-панель",
        reply_markup=admin_panel_kb(),
    )


# ── Back to admin panel ──


@router.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🔧 Админ-панель",
        reply_markup=admin_panel_kb(),
    )
    await callback.answer()


# ── Statistics ──


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    clients = await get_clients_count(session)
    onboarding = await get_onboarding_stats(session)
    strategy = await get_strategy_stats(session)
    activity = await get_activity_stats(session, days=7)

    text = (
        "📊 Статистика бота\n"
        "\n"
        f"👥 Всего клиентов: {clients['total']}\n"
        f"✅ Активных: {clients['active']}\n"
        f"⏳ Ожидают активации: {clients['pending']}\n"
        f"🚫 Заблокированных: {clients['blocked']}\n"
        "\n"
        "📋 Онбординг:\n"
        f"— Не начат: {onboarding['not_started']}\n"
        f"— В процессе: {onboarding['in_progress']}\n"
        f"— Завершён: {onboarding['completed']}\n"
        "\n"
        "📌 Стратегии:\n"
        f"— Сгенерировано: {strategy['generated']}\n"
        f"— Активных: {strategy['active']}\n"
        "\n"
        "📅 Активность за 7 дней:\n"
        f"— Активных клиентов: {activity['active_users']}\n"
        f"— Сообщений от клиентов: {activity['client_messages']}\n"
        f"— Сообщений от бота: {activity['bot_messages']}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()
