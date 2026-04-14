"""Клиентские настройки: режим общения от лица эталонной версии."""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database import queries

logger = logging.getLogger(__name__)

router = Router(name="client_settings")


def _voice_mode_kb(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if enabled:
        builder.button(text="⚪ Выключить режим", callback_data="voice_mode_off")
    else:
        builder.button(text="✅ Включить режим", callback_data="voice_mode_on")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()


def _no_etalon_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Загрузить эталонную версию", callback_data="client_etalon_start")
    builder.button(text="🔙 Главное меню", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "settings_voice_mode")
async def cb_voice_mode(callback: CallbackQuery, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    has_et = await queries.has_etalon(session, user.telegram_id)
    if not has_et:
        await callback.message.edit_text(
            "🌟 <b>Режим эталонной версии</b>\n\n"
            "В этом режиме бот будет общаться с тобой <b>от лица твоей эталонной версии</b> — "
            "поддерживать тебя из той точки, где ты уже живёшь так, как хочешь.\n\n"
            "Сначала загрузи свою эталонную версию — без неё режим работать не сможет.",
            reply_markup=_no_etalon_kb(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    enabled = bool(user.etalon_voice_mode)
    status = "✅ Включён" if enabled else "⚪ Выключен"
    description = (
        "Бот общается с тобой <b>от лица твоей эталонной версии</b> — "
        "от первого лица, как старшая мудрая ты, которая уже прошла этот путь."
        if enabled
        else "Бот общается как обычный ассистент-наставник."
    )
    await callback.message.edit_text(
        f"🌟 <b>Режим эталонной версии</b>\n\n"
        f"Сейчас: {status}\n\n"
        f"{description}\n\n"
        f"Когда режим включён, бот говорит «я знаю это состояние», «когда я была там, мне помогло…» — "
        f"не как внешний помощник, а как ты сама из будущего.",
        reply_markup=_voice_mode_kb(enabled),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "voice_mode_on")
async def cb_voice_mode_on(callback: CallbackQuery, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    if not await queries.has_etalon(session, user.telegram_id):
        await callback.answer("Сначала загрузи эталонную версию", show_alert=True)
        return

    await queries.set_etalon_voice_mode(session, user.telegram_id, True)
    user.etalon_voice_mode = True
    await callback.message.edit_text(
        "🌟 <b>Режим эталонной версии включён</b>\n\n"
        "Теперь я говорю с тобой от лица твоей эталонной версии — из той точки, "
        "где ты уже живёшь так, как описала.\n\n"
        "Просто напиши мне сообщение — попробуем 💛",
        reply_markup=_voice_mode_kb(True),
        parse_mode="HTML",
    )
    await callback.answer("Включено")


@router.callback_query(F.data == "voice_mode_off")
async def cb_voice_mode_off(callback: CallbackQuery, session: AsyncSession, **kwargs):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    await queries.set_etalon_voice_mode(session, user.telegram_id, False)
    user.etalon_voice_mode = False
    await callback.message.edit_text(
        "⚪ <b>Режим эталонной версии выключен</b>\n\n"
        "Я снова отвечаю как обычный ассистент-наставник.",
        reply_markup=_voice_mode_kb(False),
        parse_mode="HTML",
    )
    await callback.answer("Выключено")
