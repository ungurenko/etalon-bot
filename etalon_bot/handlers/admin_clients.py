"""Управление клиентами: список, карточка, активация, блокировка, просмотр данных."""

import logging
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import (
    User,
    UserRole,
    UserStatus,
    OnboardingStatus,
    StrategyStatus,
)
from etalon_bot.database.queries import (
    get_all_users,
    get_user,
    update_user_status,
    update_user_field,
    get_answers_by_user,
    get_active_strategy,
)
from etalon_bot.keyboards.admin_kb import (
    client_list_kb,
    client_card_kb,
    back_to_admin_kb,
)
from etalon_bot.keyboards.client_kb import onboarding_start_kb
from etalon_bot.utils.text_utils import split_long_message

logger = logging.getLogger(__name__)

router = Router(name="admin_clients")


# ── FSM ──


class AdminActivationFSM(StatesGroup):
    waiting_name = State()


# ── Helpers ──

_STATUS_TEXT = {
    UserStatus.pending: "⏳ Ожидает",
    UserStatus.active: "✅ Активен",
    UserStatus.inactive: "⚪ Неактивен",
    UserStatus.blocked: "🚫 Заблокирован",
}

_ONBOARDING_TEXT = {
    OnboardingStatus.not_started: "Не начат",
    OnboardingStatus.in_progress: "В процессе",
    OnboardingStatus.completed: "Завершён",
}

_STRATEGY_TEXT = {
    StrategyStatus.none: "Нет",
    StrategyStatus.generated: "Сгенерирована",
    StrategyStatus.active: "Активна",
}


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


def _format_client_card(client: User) -> str:
    name = client.display_name or client.full_name or "Без имени"
    username = f"@{client.username}" if client.username else "нет"
    status = _STATUS_TEXT.get(client.status, str(client.status))
    onboarding = _ONBOARDING_TEXT.get(client.onboarding_status, str(client.onboarding_status))
    strategy = _STRATEGY_TEXT.get(client.strategy_status, str(client.strategy_status))
    last_active = client.last_activity_at.strftime("%d.%m.%Y %H:%M") if client.last_activity_at else "—"

    access_line = ""
    if client.access_until:
        access_line = f"\n⏳ Доступ до: {client.access_until.strftime('%d.%m.%Y')}"

    return (
        f"👤 {name} ({username})\n"
        f"📌 Статус: {status}\n"
        f"📊 Онбординг: {onboarding}\n"
        f"📋 Стратегия: {strategy}{access_line}\n"
        f"📅 Последняя активность: {last_active}"
    )


# ── Client list ──


@router.callback_query(F.data == "admin_clients")
async def cb_admin_clients(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    clients = await get_all_users(session)

    if not clients:
        await callback.message.edit_text(
            "Клиентов пока нет.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "👥 Список клиентов:",
        reply_markup=client_list_kb(clients, page=0),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_clients_page_"))
async def cb_admin_clients_page(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    page = int(callback.data.split("_")[-1])
    clients = await get_all_users(session)

    await callback.message.edit_text(
        "👥 Список клиентов:",
        reply_markup=client_list_kb(clients, page=page),
    )
    await callback.answer()


# ── Client card ──


@router.callback_query(F.data.regexp(r"^admin_client_\d+$"))
async def cb_admin_client(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)

    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    # Показать фото, если есть
    if client.photo_file_id:
        try:
            await callback.message.answer_photo(
                client.photo_file_id,
                caption=f"📸 Фото клиента: {client.display_name or client.full_name}",
            )
        except Exception as exc:
            logger.warning("Failed to send client photo: %s", exc)

    await callback.message.edit_text(
        _format_client_card(client),
        reply_markup=client_card_kb(client),
    )
    await callback.answer()


# ── Activate ──


@router.callback_query(F.data.regexp(r"^admin_activate_\d+$"))
async def cb_admin_activate(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await update_user_status(session, telegram_id, UserStatus.active)
    access_until = datetime.utcnow() + timedelta(days=365)
    await update_user_field(session, telegram_id, access_until=access_until)

    await state.set_state(AdminActivationFSM.waiting_name)
    await state.update_data(target_id=telegram_id)

    await callback.message.edit_text(
        f"✅ Клиент активирован.\n\n"
        f"Введите имя клиента для обращений (например: Анна, Настя):"
    )
    await callback.answer()


@router.message(AdminActivationFSM.waiting_name)
async def on_activation_name(message: Message, user: User, session: AsyncSession, state: FSMContext, bot: Bot):
    if not _is_admin(user):
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    if not target_id:
        await state.clear()
        return

    display_name = message.text.strip()
    await update_user_field(session, target_id, display_name=display_name)
    await state.clear()

    await message.answer(
        f"Имя «{display_name}» сохранено. Приветственное сообщение отправлено клиенту.",
        reply_markup=back_to_admin_kb(),
    )

    # Отправляем приветствие клиенту
    welcome_text = (
        f"Добро пожаловать, {display_name}! 🌟\n\n"
        "Я — твой персональный стратегический помощник. "
        "Я буду рядом на пути к твоей эталонной версии.\n\n"
        "Для начала мне нужно узнать тебя — пройдём вместе "
        "анкету по 8 сферам твоей жизни.\n\n"
        "Не торопись — можешь отвечать текстом или голосовыми сообщениями. 💛\n\n"
        "Начнём?"
    )

    try:
        await bot.send_message(
            chat_id=target_id,
            text=welcome_text,
            reply_markup=onboarding_start_kb(),
        )
    except Exception as e:
        logger.error("Не удалось отправить приветствие клиенту %d: %s", target_id, e)
        await message.answer(f"⚠️ Не удалось отправить сообщение клиенту: {e}")


# ── Deactivate ──


@router.callback_query(F.data.regexp(r"^admin_deactivate_\d+$"))
async def cb_admin_deactivate(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    await update_user_status(session, telegram_id, UserStatus.inactive)

    client = await get_user(session, telegram_id)
    name = client.display_name or client.full_name if client else str(telegram_id)

    await callback.message.edit_text(
        f"⚪ Клиент «{name}» деактивирован.",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


# ── Block ──


@router.callback_query(F.data.regexp(r"^admin_block_\d+$"))
async def cb_admin_block(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    await update_user_status(session, telegram_id, UserStatus.blocked)

    client = await get_user(session, telegram_id)
    name = client.display_name or client.full_name if client else str(telegram_id)

    await callback.message.edit_text(
        f"🚫 Клиент «{name}» заблокирован.",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


# ── Reject (pending → blocked) ──


@router.callback_query(F.data.regexp(r"^admin_reject_\d+$"))
async def cb_admin_reject(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    await update_user_status(session, telegram_id, UserStatus.blocked)

    await callback.message.edit_text(
        "❌ Заявка отклонена.",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


# ── View Point A (sphere answers) ──


@router.callback_query(F.data.regexp(r"^admin_view_pointa_\d+$"))
async def cb_admin_view_pointa(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    answers = await get_answers_by_user(session, telegram_id)

    if not answers:
        await callback.message.edit_text(
            "📋 Точка А ещё не заполнена.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    name = client.display_name or client.full_name or str(telegram_id)
    text = f"📋 Точка А — {name}:\n\n"

    current_sphere = None
    for answer in answers:
        if answer.sphere_number != current_sphere:
            current_sphere = answer.sphere_number
            text += f"\n🔹 Сфера {answer.sphere_number}\n"

        status = "⏭ пропущен" if answer.is_skipped else ""
        answer_text = answer.answer_text or "—"
        voice_mark = " 🎤" if answer.is_voice else ""

        text += f"  В: {answer.question_text}\n"
        text += f"  О: {answer_text}{voice_mark} {status}\n\n"

    chunks = split_long_message(text)

    # Первый чанк — edit, остальные — новые сообщения
    await callback.message.edit_text(chunks[0], reply_markup=None)
    for chunk in chunks[1:]:
        await callback.message.answer(chunk)

    # Кнопка назад отдельным сообщением (если несколько чанков)
    if len(chunks) > 1:
        await callback.message.answer("—", reply_markup=back_to_admin_kb())
    else:
        await callback.message.edit_reply_markup(reply_markup=back_to_admin_kb())

    await callback.answer()


# ── View Strategy ──


@router.callback_query(F.data.regexp(r"^admin_view_strategy_\d+$"))
async def cb_admin_view_strategy(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    strategy = await get_active_strategy(session, telegram_id)

    if not strategy:
        await callback.message.edit_text(
            "Стратегия ещё не сгенерирована.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    name = client.display_name or client.full_name or str(telegram_id)
    text = f"📋 Стратегия — {name}:\n\n{strategy.full_text}"

    chunks = split_long_message(text)

    await callback.message.edit_text(chunks[0], reply_markup=None)
    for chunk in chunks[1:]:
        await callback.message.answer(chunk)

    if len(chunks) > 1:
        await callback.message.answer("—", reply_markup=back_to_admin_kb())
    else:
        await callback.message.edit_reply_markup(reply_markup=back_to_admin_kb())

    await callback.answer()


# ── Extend Access ──


@router.callback_query(F.data.regexp(r"^admin_extend_\d+$"))
async def cb_admin_extend(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    name = client.display_name or client.full_name or str(telegram_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ 3 месяца", callback_data=f"admin_extend_{telegram_id}_90")
    builder.button(text="➕ 6 месяцев", callback_data=f"admin_extend_{telegram_id}_180")
    builder.button(text="➕ 1 год", callback_data=f"admin_extend_{telegram_id}_365")
    builder.button(text="🔙 Назад", callback_data=f"admin_client_{telegram_id}")
    builder.adjust(3, 1)

    current = ""
    if client.access_until:
        current = f"\nТекущий доступ до: {client.access_until.strftime('%d.%m.%Y')}"

    await callback.message.edit_text(
        f"⏳ Продление доступа для «{name}»{current}\n\nВыберите срок:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_extend_\d+_\d+$"))
async def cb_admin_extend_apply(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    telegram_id = int(parts[2])
    days = int(parts[3])

    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    # Extend from current access_until or from now
    base = client.access_until if client.access_until and client.access_until > datetime.utcnow() else datetime.utcnow()
    new_until = base + timedelta(days=days)
    await update_user_field(session, telegram_id, access_until=new_until)

    name = client.display_name or client.full_name or str(telegram_id)
    await callback.message.edit_text(
        f"✅ Доступ для «{name}» продлён до {new_until.strftime('%d.%m.%Y')}.",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


# ── View Intermediate Goals ──


@router.callback_query(F.data.regexp(r"^admin_goals_\d+$"))
async def cb_admin_goals(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    telegram_id = int(callback.data.split("_")[-1])
    client = await get_user(session, telegram_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    from etalon_bot.database.queries import get_intermediate_data
    items = await get_intermediate_data(session, telegram_id)
    name = client.display_name or client.full_name or str(telegram_id)

    if not items:
        await callback.message.edit_text(
            f"🎯 Промежуточные данные — {name}:\n\nЗаписей пока нет.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    _CAT_LABELS = {"goal": "🎯 Цель", "insight": "💡 Инсайт", "course_notes": "📚 Курс", "other": "📝 Другое"}
    text = f"🎯 Промежуточные данные — {name} ({len(items)} записей):\n\n"
    for item in items[:20]:
        cat = _CAT_LABELS.get(item.category, item.category)
        date_str = item.created_at.strftime("%d.%m") if item.created_at else ""
        added = "👤" if item.added_by == "client" else "👑"
        preview = item.content[:100] + "..." if len(item.content) > 100 else item.content
        text += f"{cat} [{date_str}] {added}\n{preview}\n\n"

    chunks = split_long_message(text)
    await callback.message.edit_text(chunks[0], reply_markup=None)
    for chunk in chunks[1:]:
        await callback.message.answer(chunk)

    if len(chunks) > 1:
        await callback.message.answer("—", reply_markup=back_to_admin_kb())
    else:
        await callback.message.edit_reply_markup(reply_markup=back_to_admin_kb())

    await callback.answer()
