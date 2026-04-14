from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from etalon_bot.database.models import StageItem


def main_menu_kb(
    onboarding_completed: bool = False,
    can_gen_strategy: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Мой план", callback_data="menu_plan")
    builder.button(text="📊 Мой прогресс", callback_data="menu_progress")
    builder.button(text="💬 Написать помощнику", callback_data="menu_chat")
    builder.button(text="📚 Практики", callback_data="menu_practices")
    builder.button(text="🎯 Промежуточные цели", callback_data="menu_goals")
    builder.button(text="📝 Моя эталонная версия", callback_data="client_etalon_start")
    builder.button(text="🌟 Режим эталонной версии", callback_data="settings_voice_mode")
    if can_gen_strategy:
        builder.button(text="✨ Составить стратегию", callback_data="client_gen_strategy")
    if onboarding_completed:
        builder.button(text="📍 Моя Точка А", callback_data="pointa_open")
    builder.button(text="ℹ️ Помощь", callback_data="menu_help")

    rows = [2, 1, 2, 1, 1, 1]
    if can_gen_strategy:
        rows.append(1)
    if onboarding_completed:
        rows.append(1)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()


def onboarding_start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Начать анкету", callback_data="onboarding_start")
    builder.button(text="⏰ Позже", callback_data="onboarding_later")
    builder.adjust(2)
    return builder.as_markup()


def onboarding_question_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить вопрос", callback_data="onboarding_skip")
    builder.button(text="⏸ Пауза", callback_data="onboarding_pause")
    builder.adjust(2)
    return builder.as_markup()


def onboarding_sphere_complete_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="▶️ Следующая сфера", callback_data="onboarding_next_sphere")
    builder.button(text="⏸ Пауза", callback_data="onboarding_pause")
    builder.adjust(2)
    return builder.as_markup()


def onboarding_resume_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="▶️ Продолжить", callback_data="onboarding_resume")
    builder.button(text="🔄 Начать сферу заново", callback_data="onboarding_restart_sphere")
    builder.adjust(2)
    return builder.as_markup()


def plan_view_kb(
    items: list[StageItem],
    current_stage: int,
    total_stages: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for item in items:
        mark = "☑" if item.is_completed else "☐"
        builder.button(
            text=f"{mark} {item.text}",
            callback_data=f"plan_toggle_{item.id}",
        )

    builder.button(text="✅ Сохранить прогресс", callback_data="plan_save")

    nav_buttons = 0
    if current_stage > 1:
        builder.button(text="◀️ Предыдущий этап", callback_data="plan_prev")
        nav_buttons += 1
    if current_stage < total_stages:
        builder.button(text="▶️ Следующий этап", callback_data="plan_next")
        nav_buttons += 1

    builder.button(text="🔙 Главное меню", callback_data="menu_back")

    # Each item on its own row, then save button, then nav row, then back
    rows = [1] * len(items) + [1]
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(1)
    builder.adjust(*rows)

    return builder.as_markup()


def strategy_received_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Начнём!", callback_data="strategy_start")
    builder.button(text="📋 Мой план", callback_data="menu_plan")
    builder.button(text="💬 Задать вопрос", callback_data="menu_chat")
    builder.adjust(1, 2)
    return builder.as_markup()


def checkin_items_kb(items: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for item in items:
        mark = "☑" if item.get("checked") else "☐"
        builder.button(
            text=f"{mark} {item['text']}",
            callback_data=f"checkin_toggle_{item['id']}",
        )

    builder.button(text="✅ Готово — отправить", callback_data="checkin_submit")

    rows = [1] * len(items) + [1]
    builder.adjust(*rows)

    return builder.as_markup()


def gender_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👩 В женском роде", callback_data="gender_female")
    builder.button(text="👨 В мужском роде", callback_data="gender_male")
    builder.button(text="🤝 Нейтрально", callback_data="gender_neutral")
    builder.adjust(1)
    return builder.as_markup()


def procrastination_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Попробую", callback_data="procrast_try")
    builder.button(text="🔄 Другая техника", callback_data="procrast_other")
    builder.button(text="💬 Хочу поговорить", callback_data="procrast_talk")
    builder.adjust(2, 1)
    return builder.as_markup()
