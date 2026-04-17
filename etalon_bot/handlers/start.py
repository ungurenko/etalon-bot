from __future__ import annotations
"""
Handlers: /start, /help, /menu, admin activation, main menu callbacks.
"""

import logging
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import UserStatus, OnboardingStatus, StrategyStatus
from etalon_bot.database import queries


async def _menu_flags(session: AsyncSession, user) -> dict:
    """Compute which dynamic buttons should appear in main menu."""
    if user is None:
        return {
            "onboarding_completed": False,
            "can_gen_strategy": False,
            "can_make_period_plan": False,
        }
    completed = user.onboarding_status == OnboardingStatus.completed
    has_etalon = completed and await queries.has_etalon(session, user.telegram_id)
    can_gen = (
        has_etalon
        and user.strategy_status != StrategyStatus.active
    )
    return {
        "onboarding_completed": completed,
        "can_gen_strategy": can_gen,
        # Период-план доступен, когда есть Точка А и эталон — он дополняет
        # стратегию и не противоречит ей.
        "can_make_period_plan": has_etalon,
    }
from etalon_bot.keyboards.client_kb import main_menu_kb, onboarding_start_kb
from etalon_bot.keyboards.admin_kb import activate_new_user_kb

logger = logging.getLogger(__name__)

router = Router(name="start")


# ── FSM ──────────────────────────────────────────────────────────────────────

class ActivationFSM(StatesGroup):
    waiting_name = State()


# ── Helpers ──────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "📖 <b>Что я умею:</b>\n\n"
    "🌸 <b>Онбординг</b> — отвечаешь на вопросы по 8 сферам жизни, "
    "чтобы составить для тебя Точку А.\n\n"
    "📋 <b>Стратегия</b> — персональный план трансформации "
    "с этапами и чек-листами.\n\n"
    "💬 <b>Чат</b> — можешь написать мне в любой момент, "
    "и я отвечу с учётом твоей ситуации.\n\n"
    "📊 <b>Прогресс</b> — отслеживание выполнения стратегии.\n\n"
    "Команды:\n"
    "/menu — главное меню\n"
    "/help — эта справка"
)


async def _notify_admins_new_user(
    bot: Bot, full_name: str, username: str | None, telegram_id: int
) -> None:
    """Send notification to all admins about a new user."""
    uname = f"@{username}" if username else "без username"
    text = f"👤 Новый пользователь: {full_name} ({uname}). Активировать?"
    kb = activate_new_user_kb(telegram_id)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception as exc:
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)


# ── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    tg_id = message.from_user.id

    # New user — middleware may skip them
    if user is None:
        user = await queries.get_user(session, tg_id)
        if user is None:
            user = await queries.create_user(
                session,
                telegram_id=tg_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name or "",
            )

    if user.status == UserStatus.pending:
        await message.answer(
            "Привет! 🌸\n\n"
            "Я — твой стратегический помощник «Эталонная Версия».\n\n"
            "Я работаю по персональному приглашению. "
            "Если ты клиент — администратор активирует твой доступ.\n\n"
            "Если у тебя есть вопросы — напиши администратору."
        )
        await _notify_admins_new_user(
            bot, user.full_name, user.username, tg_id
        )
        return

    if user.status == UserStatus.blocked:
        await message.answer("Доступ ограничен.")
        return

    # Active user
    name = user.display_name or user.full_name or "друг"
    flags = await _menu_flags(session, user)
    await message.answer(
        f"С возвращением, {name}! 💛",
        reply_markup=main_menu_kb(**flags),
    )


# ── /help ────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message, **kwargs):
    await message.answer(HELP_TEXT, parse_mode="HTML")


# ── /menu ────────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    if user is None:
        await message.answer("Сначала напиши /start 🙏")
        return

    # If onboarding in progress — prompt to resume
    if user.onboarding_status == OnboardingStatus.in_progress:
        sphere_name = await queries.get_sphere_name(session, user.current_sphere or 1)
        from etalon_bot.keyboards.client_kb import onboarding_resume_kb
        name = user.display_name or user.full_name or ""
        await message.answer(
            f"С возвращением, {name}! 💛\n"
            f"Мы остановились на сфере {user.current_sphere}: {sphere_name}. Продолжим?",
            reply_markup=onboarding_resume_kb(),
        )
        return

    flags = await _menu_flags(session, user)
    await message.answer(
        "Главное меню 🌿",
        reply_markup=main_menu_kb(**flags),
    )


# ── Callback: admin_activate_{id} ───────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_activate_"))
async def cb_admin_activate(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, **kwargs
):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    target_id = int(callback.data.split("_")[-1])
    target_user = await queries.get_user(session, target_id)
    if target_user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await state.set_state(ActivationFSM.waiting_name)
    await state.update_data(target_user_id=target_id)

    name_hint = target_user.full_name or "неизвестно"
    await callback.message.answer(
        f"Введите отображаемое имя для пользователя "
        f"(Telegram-имя: {name_hint}):"
    )
    await callback.answer()


@router.message(ActivationFSM.waiting_name)
async def fsm_activation_name(
    message: Message, state: FSMContext, bot: Bot, session: AsyncSession, **kwargs
):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    data = await state.get_data()
    target_id = data["target_user_id"]
    display_name = message.text.strip()

    access_until = datetime.utcnow() + timedelta(days=365)
    await queries.update_user_field(
        session, target_id,
        display_name=display_name,
        status=UserStatus.active,
        access_until=access_until,
    )
    await state.clear()

    await message.answer(f"✅ Пользователь {display_name} активирован.")

    # Notify the client
    try:
        await bot.send_message(
            target_id,
            f"Привет, {display_name}! 🌸\n\n"
            "Твой доступ активирован. Давай начнём с небольшого знакомства — "
            "я задам тебе вопросы по 8 сферам жизни, чтобы понять, "
            "где ты сейчас.\n\n"
            "Это займёт около 15-20 минут. Можно делать паузы 💛",
            reply_markup=onboarding_start_kb(),
        )
    except Exception as exc:
        logger.warning("Failed to send welcome to %s: %s", target_id, exc)
        await message.answer(
            f"⚠️ Не удалось отправить приветствие клиенту: {exc}"
        )


# ── Callback: admin_reject_{id} ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_reject_"))
async def cb_admin_reject(
    callback: CallbackQuery, session: AsyncSession, **kwargs
):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    target_id = int(callback.data.split("_")[-1])
    await queries.update_user_status(session, target_id, UserStatus.blocked)
    await callback.message.edit_text("Отклонён ✅")
    await callback.answer()


# ── Callback: menu_back ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_back")
async def cb_menu_back(callback: CallbackQuery, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    flags = await _menu_flags(session, user)
    await callback.message.edit_text(
        "Главное меню 🌿",
        reply_markup=main_menu_kb(**flags),
    )
    await callback.answer()


# ── Callback: menu_help ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_help")
async def cb_menu_help(callback: CallbackQuery, **kwargs):
    await callback.message.answer(HELP_TEXT, parse_mode="HTML")
    await callback.answer()


# ── Callback: menu_chat ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_chat")
async def cb_menu_chat(callback: CallbackQuery, **kwargs):
    await callback.message.answer("Просто напиши мне сообщение, и я отвечу 💬")
    await callback.answer()


# ── Callback: menu_practices ─────────────────────────────────────────────────

@router.callback_query(F.data == "menu_practices")
async def cb_menu_practices(callback: CallbackQuery, **kwargs):
    await callback.message.answer(
        "🧘 Раздел практик в разработке. Скоро здесь появится что-то полезное!"
    )
    await callback.answer()
