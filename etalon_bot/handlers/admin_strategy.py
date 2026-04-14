"""Генерация и управление стратегиями клиентов."""

import logging

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import (
    User,
    UserRole,
    OnboardingStatus,
    StrategyStatus,
)
from etalon_bot.database.queries import (
    get_user,
    get_etalon_for_user,
    get_intermediate_data,
    save_strategy,
    update_user_field,
)
from etalon_bot.keyboards.admin_kb import (
    strategy_preview_kb,
    back_to_admin_kb,
)
from etalon_bot.keyboards.client_kb import strategy_received_kb
from etalon_bot.services.strategy_service import (
    generate_strategy,
    parse_strategy_to_stages,
)
from etalon_bot.utils.text_utils import split_long_message, markdown_to_telegram_html

logger = logging.getLogger(__name__)

router = Router(name="admin_strategy")


# ── FSM ──


class StrategyFSM(StatesGroup):
    preview = State()
    editing = State()


# ── Helpers ──


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


# ── Generate strategy ──


@router.callback_query(F.data.regexp(r"^admin_gen_strategy_\d+$"))
async def cb_admin_gen_strategy(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    name = client.display_name or client.full_name or str(telegram_id)

    # Проверяем предусловия
    missing = []
    if client.onboarding_status != OnboardingStatus.completed:
        missing.append("онбординг не завершён")
    etalon = await get_etalon_for_user(session, telegram_id)
    if not etalon:
        missing.append("эталонная версия не загружена")

    if missing:
        await callback.message.edit_text(
            f"⚠️ Не хватает данных для генерации стратегии:\n"
            f"— " + "\n— ".join(missing),
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    # Предупреждение о промежуточных данных
    intermediate = await get_intermediate_data(session, telegram_id)
    warning = ""
    if not intermediate:
        warning = "\n⚠️ Промежуточные цели не заполнены — стратегия будет без них.\n"

    await callback.message.edit_text(
        f"⏳ Генерирую стратегию для «{name}»...{warning}\n"
        "Это может занять до минуты."
    )
    await callback.answer()

    # Typing action
    await bot.send_chat_action(chat_id=callback.message.chat.id, action="typing")

    try:
        strategy_text = await generate_strategy(session, client)
    except Exception as e:
        logger.error("Ошибка генерации стратегии для %d: %s", telegram_id, e)
        await callback.message.edit_text(
            f"❌ Ошибка генерации стратегии: {e}",
            reply_markup=back_to_admin_kb(),
        )
        return

    # Сохраняем в FSM для превью
    await state.set_state(StrategyFSM.preview)
    await state.update_data(
        target_id=telegram_id,
        client_name=name,
        strategy_text=strategy_text,
    )

    # Показываем превью
    chunks = split_long_message(f"📋 Стратегия для «{name}»:\n\n{strategy_text}")

    for i, chunk in enumerate(chunks):
        if i == 0:
            await callback.message.edit_text(chunk)
        else:
            await callback.message.answer(chunk)

    # Клавиатура с действиями
    await callback.message.answer(
        "Что сделать со стратегией?",
        reply_markup=strategy_preview_kb(),
    )


# ── Send strategy to client ──


@router.callback_query(F.data == "strategy_send", StrategyFSM.preview)
async def cb_strategy_send(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    client_name = data.get("client_name", "")
    strategy_text = data.get("strategy_text", "")

    if not target_id or not strategy_text:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        await state.clear()
        return

    # Сохраняем в БД
    strategy = await save_strategy(session, target_id, strategy_text)

    # Парсим в этапы
    await parse_strategy_to_stages(session, strategy.id, strategy_text)

    # Обновляем статус пользователя
    await update_user_field(
        session,
        target_id,
        strategy_status=StrategyStatus.active,
        current_stage=1,
    )

    await state.clear()

    # Отправляем клиенту
    formatted_strategy = markdown_to_telegram_html(strategy_text)
    client_message = (
        f"🌟 <b>{client_name}</b>, твоя персональная стратегия готова!\n\n"
        f"{formatted_strategy}\n\n"
        "———\n"
        "Это твоя дорожная карта. Мы будем идти по ней вместе 💫\n\n"
        "Начнём с первого этапа?"
    )

    try:
        chunks = split_long_message(client_message)
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                await bot.send_message(
                    chat_id=target_id,
                    text=chunk,
                    reply_markup=strategy_received_kb(),
                )
            else:
                await bot.send_message(chat_id=target_id, text=chunk)

        await callback.message.edit_text(
            f"✅ Стратегия отправлена клиенту «{client_name}».",
            reply_markup=back_to_admin_kb(),
        )
    except Exception as e:
        logger.error("Не удалось отправить стратегию клиенту %d: %s", target_id, e)
        await callback.message.edit_text(
            f"⚠️ Стратегия сохранена, но не удалось отправить клиенту: {e}",
            reply_markup=back_to_admin_kb(),
        )

    await callback.answer()


# ── Edit strategy ──


@router.callback_query(F.data == "strategy_edit", StrategyFSM.preview)
async def cb_strategy_edit(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(StrategyFSM.editing)
    await callback.message.edit_text(
        "✏️ Отправьте отредактированный текст стратегии целиком:"
    )
    await callback.answer()


@router.message(StrategyFSM.editing, F.text)
async def on_strategy_edit_text(message: Message, user: User, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    client_name = data.get("client_name", "")

    strategy_text = message.text.strip()
    await state.update_data(strategy_text=strategy_text)
    await state.set_state(StrategyFSM.preview)

    chunks = split_long_message(f"📋 Стратегия для «{client_name}» (отредактировано):\n\n{strategy_text}")

    for chunk in chunks:
        await message.answer(chunk)

    await message.answer(
        "Что сделать со стратегией?",
        reply_markup=strategy_preview_kb(),
    )


# ── Regenerate strategy ──


@router.callback_query(F.data == "strategy_regen", StrategyFSM.preview)
async def cb_strategy_regen(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    client_name = data.get("client_name", "")

    if not target_id:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        return

    client = await get_user(session, target_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"🔄 Перегенерирую стратегию для «{client_name}»...\n"
        "Это может занять до минуты."
    )
    await callback.answer()

    await bot.send_chat_action(chat_id=callback.message.chat.id, action="typing")

    try:
        strategy_text = await generate_strategy(session, client)
    except Exception as e:
        logger.error("Ошибка перегенерации стратегии для %d: %s", target_id, e)
        await callback.message.edit_text(
            f"❌ Ошибка генерации: {e}",
            reply_markup=back_to_admin_kb(),
        )
        return

    await state.update_data(strategy_text=strategy_text)

    chunks = split_long_message(f"📋 Стратегия для «{client_name}» (перегенерировано):\n\n{strategy_text}")

    await callback.message.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await callback.message.answer(chunk)

    await callback.message.answer(
        "Что сделать со стратегией?",
        reply_markup=strategy_preview_kb(),
    )
