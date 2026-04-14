"""Самостоятельная генерация стратегии клиентом (без участия админа)."""
from __future__ import annotations

import logging

from aiogram import Router, F, Bot
from aiogram.enums import ChatAction
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database import queries
from etalon_bot.database.models import (
    OnboardingStatus,
    StrategyStatus,
    User,
)
from etalon_bot.keyboards.client_kb import strategy_received_kb
from etalon_bot.services.strategy_service import (
    generate_strategy,
    parse_strategy_to_stages,
)
from etalon_bot.utils.text_utils import split_long_message

logger = logging.getLogger(__name__)

router = Router(name="client_strategy")


def _confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Составить сейчас", callback_data="client_gen_strategy_go")
    builder.button(text="🎯 Сначала добавить цели", callback_data="menu_goals")
    builder.button(text="🔙 Позже", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()


async def _can_gen_strategy(session: AsyncSession, user: User) -> tuple[bool, str]:
    """Возвращает (готов, причина если не готов)."""
    if user.onboarding_status != OnboardingStatus.completed:
        return False, "сначала пройди анкету Точки А"
    if not await queries.has_etalon(session, user.telegram_id):
        return False, "сначала загрузи свою эталонную версию"
    if user.strategy_status == StrategyStatus.active:
        return False, "у тебя уже есть активная стратегия"
    return True, ""


@router.callback_query(F.data == "client_gen_strategy")
async def cb_gen_strategy_prompt(
    callback: CallbackQuery, user: User, session: AsyncSession
):
    ok, reason = await _can_gen_strategy(session, user)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return

    intermediate = await queries.get_intermediate_data(session, user.telegram_id)
    hint = (
        "У тебя есть промежуточные цели — отлично, учту их."
        if intermediate
        else "💡 Совет: если у тебя есть промежуточные цели, курсы или инсайты, "
             "добавь их через «🎯 Промежуточные цели» — стратегия будет точнее. "
             "Без них я тоже могу составить, но опираясь только на Точку А и эталон."
    )

    text = (
        "✨ <b>Персональная стратегия</b>\n\n"
        "Я могу составить для тебя пошаговую стратегию развития на 1–3 года "
        "на основе твоей Точки А, эталонной версии и промежуточных целей.\n\n"
        f"{hint}\n\n"
        "Генерация занимает около минуты."
    )

    try:
        await callback.message.edit_text(
            text, reply_markup=_confirm_kb(), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=_confirm_kb(), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "client_gen_strategy_go")
async def cb_gen_strategy_run(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
):
    ok, reason = await _can_gen_strategy(session, user)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return

    name = user.display_name or user.full_name or "друг"

    try:
        await callback.message.edit_text(
            "⏳ Работаю над твоей стратегией... Это займёт около минуты 💫"
        )
    except Exception:
        await callback.message.answer(
            "⏳ Работаю над твоей стратегией... Это займёт около минуты 💫"
        )
    await callback.answer()

    try:
        await bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    try:
        strategy_text = await generate_strategy(session, user)
    except Exception as e:
        logger.exception("Client strategy gen failed for %d: %s", user.telegram_id, e)
        await callback.message.answer(
            "⚠️ Не удалось составить стратегию сейчас. Попробуй ещё раз через минуту 🙏"
        )
        return

    strategy = await queries.save_strategy(session, user.telegram_id, strategy_text)
    await parse_strategy_to_stages(session, strategy.id, strategy_text)
    await queries.update_user_field(
        session,
        user.telegram_id,
        strategy_status=StrategyStatus.active,
        current_stage=1,
    )

    client_message = (
        f"🌟 {name}, твоя персональная стратегия готова!\n\n"
        f"{strategy_text}\n\n"
        "———\n"
        "Это твоя дорожная карта. Мы будем идти по ней вместе 💫"
    )

    chunks = split_long_message(client_message)
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            await callback.message.answer(chunk, reply_markup=strategy_received_kb())
        else:
            await callback.message.answer(chunk)

    uname = f"@{user.username}" if user.username else ""
    admin_text = (
        f"✨ Клиент «{name}» {uname} самостоятельно сгенерировал стратегию."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception as exc:
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)
