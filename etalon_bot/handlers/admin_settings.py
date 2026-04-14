"""Управление настройками бота: расписание, напоминания, тон обращений."""

import json
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import User, UserRole
from etalon_bot.database.queries import get_setting_json, set_setting
from etalon_bot.keyboards.admin_kb import settings_kb, back_to_admin_kb

logger = logging.getLogger(__name__)

router = Router(name="admin_settings")


# ── FSM ──


class SettingsFSM(StatesGroup):
    editing_schedule = State()
    editing_reminders = State()
    editing_tone = State()


# ── Helpers ──


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


_DAY_NAMES = {
    "mon": "Пн",
    "tue": "Вт",
    "wed": "Ср",
    "thu": "Чт",
    "fri": "Пт",
    "sat": "Сб",
    "sun": "Вс",
}

_DAY_PARSE = {
    "пн": "mon",
    "вт": "tue",
    "ср": "wed",
    "чт": "thu",
    "пт": "fri",
    "сб": "sat",
    "вс": "sun",
}


# ── Settings menu ──


@router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "⚙️ Настройки бота",
        reply_markup=settings_kb(),
    )
    await callback.answer()


# ── Schedule ──


@router.callback_query(F.data == "settings_schedule")
async def cb_settings_schedule(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    schedule = await get_setting_json(session, "checkin_schedule")
    if schedule:
        days = schedule.get("days", [])
        time_str = schedule.get("time", "10:00")
        tz = schedule.get("timezone", "Europe/Moscow")
        days_text = ", ".join(_DAY_NAMES.get(d, d) for d in days)
    else:
        days_text = "Пн, Чт"
        time_str = "10:00"
        tz = "Europe/Moscow"

    await state.set_state(SettingsFSM.editing_schedule)

    await callback.message.edit_text(
        f"📅 Расписание проверок\n\n"
        f"Текущее расписание: {days_text} в {time_str} ({tz})\n\n"
        f"Введите новое расписание в формате:\n"
        f"<code>пн,чт 10:00</code>\n\n"
        f"Доступные дни: пн, вт, ср, чт, пт, сб, вс",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


@router.message(SettingsFSM.editing_schedule, F.text)
async def on_schedule_edit(message: Message, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        return

    text = message.text.strip().lower()

    # Парсим формат: "пн,чт 10:00"
    parts = text.split()
    if len(parts) != 2:
        await message.answer(
            "⚠️ Неверный формат. Используйте: <code>пн,чт 10:00</code>",
            parse_mode="HTML",
        )
        return

    days_raw = parts[0].split(",")
    time_str = parts[1]

    # Валидация дней
    days = []
    for d in days_raw:
        d = d.strip()
        if d not in _DAY_PARSE:
            await message.answer(
                f"⚠️ Неизвестный день: «{d}». "
                f"Доступные: пн, вт, ср, чт, пт, сб, вс"
            )
            return
        days.append(_DAY_PARSE[d])

    # Валидация времени
    try:
        h, m = time_str.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        time_str = f"{h:02d}:{m:02d}"
    except (ValueError, AttributeError):
        await message.answer("⚠️ Неверный формат времени. Используйте: HH:MM (например, 10:00)")
        return

    schedule = {
        "days": days,
        "time": time_str,
        "timezone": "Europe/Moscow",
    }
    await set_setting(session, "checkin_schedule", json.dumps(schedule, ensure_ascii=False))
    await state.clear()

    days_text = ", ".join(_DAY_NAMES.get(d, d) for d in days)
    await message.answer(
        f"✅ Расписание обновлено: {days_text} в {time_str}",
        reply_markup=settings_kb(),
    )


# ── Reminders ──


@router.callback_query(F.data == "settings_reminders")
async def cb_settings_reminders(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    thresholds = await get_setting_json(session, "reminder_thresholds")
    if thresholds:
        soft = thresholds.get("soft", 3)
        medium = thresholds.get("medium", 7)
        hard = thresholds.get("hard", 14)
    else:
        soft, medium, hard = 3, 7, 14

    await state.set_state(SettingsFSM.editing_reminders)

    await callback.message.edit_text(
        f"⏰ Пороги напоминаний (дни без активности)\n\n"
        f"Мягкое напоминание: {soft} дн.\n"
        f"Среднее напоминание: {medium} дн.\n"
        f"Жёсткое напоминание: {hard} дн.\n\n"
        f"Введите новые пороги через пробел:\n"
        f"<code>3 7 14</code>",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


@router.message(SettingsFSM.editing_reminders, F.text)
async def on_reminders_edit(message: Message, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        return

    parts = message.text.strip().split()
    if len(parts) != 3:
        await message.answer(
            "⚠️ Введите 3 числа через пробел: <code>3 7 14</code>",
            parse_mode="HTML",
        )
        return

    try:
        soft, medium, hard = int(parts[0]), int(parts[1]), int(parts[2])
        if not (0 < soft < medium < hard):
            raise ValueError("Числа должны идти по возрастанию")
    except ValueError as e:
        await message.answer(f"⚠️ Ошибка: {e}. Введите 3 целых числа по возрастанию.")
        return

    thresholds = {"soft": soft, "medium": medium, "hard": hard}
    await set_setting(session, "reminder_thresholds", json.dumps(thresholds))
    await state.clear()

    await message.answer(
        f"✅ Пороги обновлены: мягкое — {soft} дн., среднее — {medium} дн., жёсткое — {hard} дн.",
        reply_markup=settings_kb(),
    )


# ── Tone (warm addresses) ──


@router.callback_query(F.data == "settings_tone")
async def cb_settings_tone(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    addresses = await get_setting_json(session, "warm_addresses")
    if addresses and isinstance(addresses, list):
        addresses_text = ", ".join(addresses)
    else:
        addresses_text = "милая, невероятная, дорогая, прекрасная, чудесная"

    await state.set_state(SettingsFSM.editing_tone)

    await callback.message.edit_text(
        f"💬 Тон обращений\n\n"
        f"Текущие обращения: {addresses_text}\n\n"
        f"Отправьте новый список обращений через запятую:",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


@router.message(SettingsFSM.editing_tone, F.text)
async def on_tone_edit(message: Message, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        return

    raw = message.text.strip()
    addresses = [a.strip() for a in raw.split(",") if a.strip()]

    if not addresses:
        await message.answer("⚠️ Список не может быть пустым. Введите обращения через запятую.")
        return

    await set_setting(session, "warm_addresses", json.dumps(addresses, ensure_ascii=False))
    await state.clear()

    await message.answer(
        f"✅ Обращения обновлены: {', '.join(addresses)}",
        reply_markup=settings_kb(),
    )
