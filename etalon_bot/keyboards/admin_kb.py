from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from etalon_bot.database.models import User, UserStatus


_STATUS_EMOJI = {
    UserStatus.active: "✅",
    UserStatus.pending: "⏳",
    UserStatus.blocked: "🚫",
    UserStatus.inactive: "⚪",
}


def admin_panel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Клиенты", callback_data="admin_clients")
    builder.button(text="📝 Эталонные версии", callback_data="admin_etalons")
    builder.button(text="📚 База знаний", callback_data="admin_kb")
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="📨 Рассылка", callback_data="admin_broadcast")
    builder.button(text="⚙️ Настройки", callback_data="admin_settings")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def client_list_kb(
    clients: list[User],
    page: int,
    per_page: int = 5,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    start = page * per_page
    end = start + per_page
    page_clients = clients[start:end]

    for client in page_clients:
        emoji = _STATUS_EMOJI.get(client.status, "⚪")
        name = client.display_name or client.full_name
        username_part = f" (@{client.username})" if client.username else ""
        builder.button(
            text=f"{emoji} {name}{username_part}",
            callback_data=f"admin_client_{client.telegram_id}",
        )

    nav_buttons = 0
    if page > 0:
        builder.button(text="◀️", callback_data=f"admin_clients_page_{page - 1}")
        nav_buttons += 1
    if end < len(clients):
        builder.button(text="▶️", callback_data=f"admin_clients_page_{page + 1}")
        nav_buttons += 1

    builder.button(text="🔙 Админ-панель", callback_data="admin_back")

    rows = [1] * len(page_clients)
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(1)
    builder.adjust(*rows)

    return builder.as_markup()


def client_card_kb(user: User) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tid = user.telegram_id
    btn_count = 0

    if user.status == UserStatus.pending:
        builder.button(text="✅ Активировать", callback_data=f"admin_activate_{tid}")
        btn_count += 1
    elif user.status == UserStatus.active:
        builder.button(text="⛔ Деактивировать", callback_data=f"admin_deactivate_{tid}")
        btn_count += 1

    builder.button(text="📝 Загрузить эталонную версию", callback_data=f"admin_etalon_{tid}")
    builder.button(text="🔄 Сгенерировать стратегию", callback_data=f"admin_gen_strategy_{tid}")
    builder.button(text="📋 Просмотреть Точку А", callback_data=f"admin_view_pointa_{tid}")
    builder.button(text="📋 Просмотреть стратегию", callback_data=f"admin_view_strategy_{tid}")
    builder.button(text="🎯 Промежуточные данные", callback_data=f"admin_goals_{tid}")
    btn_count += 5

    builder.button(text="⏳ Продлить доступ", callback_data=f"admin_extend_{tid}")
    btn_count += 1

    if not user.bot_blocked and user.status != UserStatus.blocked:
        builder.button(text="🚫 Заблокировать", callback_data=f"admin_block_{tid}")
        btn_count += 1

    builder.button(text="🔙 Назад", callback_data="admin_clients")
    btn_count += 1

    builder.adjust(*([1] * btn_count))
    return builder.as_markup()


def activate_new_user_kb(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Активировать", callback_data=f"admin_activate_{telegram_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject_{telegram_id}")
    builder.adjust(2)
    return builder.as_markup()


def etalon_preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data="etalon_save")
    builder.button(text="✏️ Редактировать блок", callback_data="etalon_edit")
    builder.button(text="🗑 Отменить", callback_data="etalon_cancel")
    builder.adjust(2, 1)
    return builder.as_markup()


def etalon_edit_blocks_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for n in range(1, 8):
        builder.button(text=f"Блок {n}", callback_data=f"etalon_edit_block_{n}")
    builder.button(text="🔙 Назад к превью", callback_data="etalon_back_preview")
    builder.adjust(4, 3, 1)
    return builder.as_markup()


def strategy_preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить клиенту", callback_data="strategy_send")
    builder.button(text="✏️ Отредактировать", callback_data="strategy_edit")
    builder.button(text="🔄 Перегенерировать", callback_data="strategy_regen")
    builder.adjust(1, 2)
    return builder.as_markup()


def broadcast_preview_kb(count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Отправить ({count} чел.)", callback_data="broadcast_send")
    builder.button(text="✏️ Редактировать", callback_data="broadcast_edit")
    builder.button(text="❌ Отмена", callback_data="broadcast_cancel")
    builder.adjust(1, 2)
    return builder.as_markup()


def settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Расписание проверок", callback_data="settings_schedule")
    builder.button(text="⏰ Напоминания", callback_data="settings_reminders")
    builder.button(text="💬 Тон обращений", callback_data="settings_tone")
    builder.button(text="🔙 Админ-панель", callback_data="admin_back")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def kb_categories_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧘 Техники против прокрастинации", callback_data="kb_cat_procrastination")
    builder.button(text="📝 Практики по сферам", callback_data="kb_cat_practice")
    builder.button(text="🔗 Ссылки на материалы", callback_data="kb_cat_material")
    builder.button(text="➕ Добавить материал", callback_data="kb_add")
    builder.button(text="🔙 Админ-панель", callback_data="admin_back")
    builder.adjust(1, 1, 1, 1, 1)
    return builder.as_markup()


def kb_item_kb(item_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_active:
        builder.button(text="🔴 Деактивировать", callback_data=f"kb_toggle_{item_id}")
    else:
        builder.button(text="🟢 Активировать", callback_data=f"kb_toggle_{item_id}")
    builder.button(text="✏️ Редактировать", callback_data=f"kb_edit_{item_id}")
    builder.button(text="🗑 Удалить", callback_data=f"kb_delete_{item_id}")
    builder.button(text="🔙 Назад", callback_data="kb_back")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да", callback_data=f"confirm_{action}")
    builder.button(text="Нет", callback_data=f"cancel_{action}")
    builder.adjust(2)
    return builder.as_markup()


def etalon_method_kb(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎙 Голосом/текстом (свободная форма)",
        callback_data=f"etalon_freeform_{telegram_id}",
    )
    builder.button(
        text="📝 По блокам (7 шагов)",
        callback_data=f"etalon_blocks_{telegram_id}",
    )
    builder.button(text="🔙 Назад", callback_data="admin_clients")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Админ-панель", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()
