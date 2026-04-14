"""CRUD для базы знаний: категории, материалы, практики."""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database.models import User, UserRole, KBCategory
from etalon_bot.database.queries import (
    create_kb_item,
    get_kb_items,
    get_kb_item,
    update_kb_item,
    delete_kb_item,
)
from etalon_bot.keyboards.admin_kb import (
    kb_categories_kb,
    kb_item_kb,
    back_to_admin_kb,
    confirm_kb,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_knowledge")


# ── FSM ──


class KBAddFSM(StatesGroup):
    title = State()
    category = State()
    sphere = State()
    content = State()
    link = State()
    confirm = State()


class KBEditFSM(StatesGroup):
    content = State()


# ── Helpers ──


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin or user.telegram_id in ADMIN_IDS


_CATEGORY_NAMES = {
    KBCategory.procrastination: "🧘 Техники против прокрастинации",
    KBCategory.practice: "📝 Практики по сферам",
    KBCategory.material: "🔗 Ссылки на материалы",
}

_SPHERE_NAMES = {
    1: "Здоровье и тело",
    2: "Отношения",
    3: "Карьера и бизнес",
    4: "Финансы",
    5: "Личностный рост",
    6: "Эмоции и состояние",
    7: "Окружение и среда",
    8: "Смысл и миссия",
}


def _format_kb_item(item) -> str:
    status = "🟢 Активен" if item.is_active else "🔴 Неактивен"
    category = _CATEGORY_NAMES.get(item.category, str(item.category))
    sphere = _SPHERE_NAMES.get(item.sphere_tag, "—") if item.sphere_tag else "—"
    link = item.link_url or "—"

    return (
        f"📚 {item.title}\n\n"
        f"Категория: {category}\n"
        f"Сфера: {sphere}\n"
        f"Статус: {status}\n"
        f"Ссылка: {link}\n\n"
        f"{item.content}"
    )


def _items_list_kb(items: list, category: str, page: int = 0, per_page: int = 5):
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    for item in page_items:
        status = "🟢" if item.is_active else "🔴"
        builder.button(
            text=f"{status} {item.title}",
            callback_data=f"kb_view_{item.id}",
        )

    nav_buttons = 0
    if page > 0:
        builder.button(text="◀️", callback_data=f"kb_page_{category}_{page - 1}")
        nav_buttons += 1
    if end < len(items):
        builder.button(text="▶️", callback_data=f"kb_page_{category}_{page + 1}")
        nav_buttons += 1

    builder.button(text="➕ Добавить", callback_data="kb_add")
    builder.button(text="🔙 Категории", callback_data="admin_kb")

    rows = [1] * len(page_items)
    if nav_buttons:
        rows.append(nav_buttons)
    rows.extend([1, 1])
    builder.adjust(*rows)

    return builder.as_markup()


# ── Categories ──


@router.callback_query(F.data == "admin_kb")
async def cb_admin_kb(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "📚 База знаний — выберите категорию:",
        reply_markup=kb_categories_kb(),
    )
    await callback.answer()


# ── Category items list ──


@router.callback_query(F.data.regexp(r"^kb_cat_(procrastination|practice|material)$"))
async def cb_kb_category(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    category_str = callback.data.split("_")[-1]
    category = KBCategory(category_str)
    items = await get_kb_items(session, category=category, active_only=False)

    cat_name = _CATEGORY_NAMES.get(category, category_str)

    if not items:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Добавить", callback_data="kb_add")
        builder.button(text="🔙 Категории", callback_data="admin_kb")
        builder.adjust(1, 1)

        await callback.message.edit_text(
            f"{cat_name}\n\nМатериалов пока нет.",
            reply_markup=builder.as_markup(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"{cat_name} ({len(items)} шт.):",
        reply_markup=_items_list_kb(items, category_str, page=0),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^kb_page_\w+_\d+$"))
async def cb_kb_page(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    category_str = parts[2]
    page = int(parts[3])
    category = KBCategory(category_str)
    items = await get_kb_items(session, category=category, active_only=False)

    cat_name = _CATEGORY_NAMES.get(category, category_str)

    await callback.message.edit_text(
        f"{cat_name} ({len(items)} шт.):",
        reply_markup=_items_list_kb(items, category_str, page=page),
    )
    await callback.answer()


# ── View item ──


@router.callback_query(F.data.regexp(r"^kb_view_\d+$"))
async def cb_kb_view(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    item_id = int(callback.data.split("_")[-1])
    item = await get_kb_item(session, item_id)

    if not item:
        await callback.answer("Материал не найден", show_alert=True)
        return

    text = _format_kb_item(item)
    await callback.message.edit_text(
        text,
        reply_markup=kb_item_kb(item.id, item.is_active),
    )
    await callback.answer()


@router.callback_query(F.data == "kb_back")
async def cb_kb_back(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "📚 База знаний — выберите категорию:",
        reply_markup=kb_categories_kb(),
    )
    await callback.answer()


# ── Toggle active ──


@router.callback_query(F.data.regexp(r"^kb_toggle_\d+$"))
async def cb_kb_toggle(callback: CallbackQuery, user: User, session: AsyncSession):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    item_id = int(callback.data.split("_")[-1])
    item = await get_kb_item(session, item_id)
    if not item:
        await callback.answer("Материал не найден", show_alert=True)
        return

    new_status = not item.is_active
    await update_kb_item(session, item_id, is_active=new_status)

    # Перечитываем из БД
    item = await get_kb_item(session, item_id)
    text = _format_kb_item(item)
    await callback.message.edit_text(
        text,
        reply_markup=kb_item_kb(item.id, item.is_active),
    )
    await callback.answer("Статус обновлён")


# ── Edit item ──


@router.callback_query(F.data.regexp(r"^kb_edit_\d+$"))
async def cb_kb_edit(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    item_id = int(callback.data.split("_")[-1])
    await state.set_state(KBEditFSM.content)
    await state.update_data(editing_item_id=item_id)

    await callback.message.edit_text(
        "✏️ Отправьте новый текст для этого материала:"
    )
    await callback.answer()


@router.message(KBEditFSM.content, F.text)
async def on_kb_edit_content(message: Message, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    item_id = data.get("editing_item_id")
    if not item_id:
        await state.clear()
        return

    await update_kb_item(session, item_id, content=message.text.strip())
    await state.clear()

    item = await get_kb_item(session, item_id)
    if item:
        text = _format_kb_item(item)
        await message.answer(text, reply_markup=kb_item_kb(item.id, item.is_active))
    else:
        await message.answer("✅ Обновлено.", reply_markup=back_to_admin_kb())


# ── Delete item ──


@router.callback_query(F.data.regexp(r"^kb_delete_\d+$"))
async def cb_kb_delete(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    item_id = int(callback.data.split("_")[-1])
    await state.update_data(deleting_item_id=item_id)

    await callback.message.edit_text(
        "Вы уверены, что хотите удалить этот материал?",
        reply_markup=confirm_kb("kb_delete"),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_kb_delete")
async def cb_confirm_kb_delete(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    item_id = data.get("deleting_item_id")
    if not item_id:
        await callback.answer("Ошибка", show_alert=True)
        return

    await delete_kb_item(session, item_id)
    await state.clear()

    await callback.message.edit_text(
        "🗑 Материал удалён.",
        reply_markup=kb_categories_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_kb_delete")
async def cb_cancel_kb_delete(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    item_id = data.get("deleting_item_id")
    await state.clear()

    if item_id:
        item = await get_kb_item(session, item_id)
        if item:
            text = _format_kb_item(item)
            await callback.message.edit_text(
                text,
                reply_markup=kb_item_kb(item.id, item.is_active),
            )
            await callback.answer()
            return

    await callback.message.edit_text(
        "📚 База знаний — выберите категорию:",
        reply_markup=kb_categories_kb(),
    )
    await callback.answer()


# ── Add new item ──


@router.callback_query(F.data == "kb_add")
async def cb_kb_add(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(KBAddFSM.title)
    await state.update_data(new_kb={})

    await callback.message.edit_text(
        "📚 Добавление нового материала\n\n"
        "Шаг 1/5: Введите название материала:"
    )
    await callback.answer()


@router.message(KBAddFSM.title, F.text)
async def on_kb_add_title(message: Message, user: User, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    new_kb = data.get("new_kb", {})
    new_kb["title"] = message.text.strip()
    await state.update_data(new_kb=new_kb)
    await state.set_state(KBAddFSM.category)

    builder = InlineKeyboardBuilder()
    builder.button(text="🧘 Прокрастинация", callback_data="kbadd_cat_procrastination")
    builder.button(text="📝 Практики", callback_data="kbadd_cat_practice")
    builder.button(text="🔗 Материалы", callback_data="kbadd_cat_material")
    builder.adjust(1, 1, 1)

    await message.answer(
        "Шаг 2/5: Выберите категорию:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(KBAddFSM.category, F.data.regexp(r"^kbadd_cat_\w+$"))
async def on_kb_add_category(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    category_str = callback.data.split("_")[-1]
    data = await state.get_data()
    new_kb = data.get("new_kb", {})
    new_kb["category"] = category_str
    await state.update_data(new_kb=new_kb)
    await state.set_state(KBAddFSM.sphere)

    builder = InlineKeyboardBuilder()
    for num, name in _SPHERE_NAMES.items():
        builder.button(text=name, callback_data=f"kbadd_sphere_{num}")
    builder.button(text="⏭ Пропустить", callback_data="kbadd_sphere_skip")
    builder.adjust(2, 2, 2, 2, 1)

    await callback.message.edit_text(
        "Шаг 3/5: Выберите сферу (или пропустите):",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(KBAddFSM.sphere, F.data.regexp(r"^kbadd_sphere_"))
async def on_kb_add_sphere(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    sphere_part = callback.data.split("_")[-1]
    data = await state.get_data()
    new_kb = data.get("new_kb", {})

    if sphere_part == "skip":
        new_kb["sphere_tag"] = None
    else:
        new_kb["sphere_tag"] = int(sphere_part)

    await state.update_data(new_kb=new_kb)
    await state.set_state(KBAddFSM.content)

    await callback.message.edit_text(
        "Шаг 4/5: Введите содержание материала:"
    )
    await callback.answer()


@router.message(KBAddFSM.content, F.text)
async def on_kb_add_content(message: Message, user: User, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    new_kb = data.get("new_kb", {})
    new_kb["content"] = message.text.strip()
    await state.update_data(new_kb=new_kb)
    await state.set_state(KBAddFSM.link)

    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить", callback_data="kbadd_link_skip")
    builder.adjust(1)

    await message.answer(
        "Шаг 5/5: Отправьте ссылку или пропустите:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(KBAddFSM.link, F.data == "kbadd_link_skip")
async def on_kb_add_link_skip(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    new_kb = data.get("new_kb", {})
    new_kb["link_url"] = None
    await state.update_data(new_kb=new_kb)
    await state.set_state(KBAddFSM.confirm)

    await _show_kb_add_preview(callback.message, new_kb, edit=True)
    await callback.answer()


@router.message(KBAddFSM.link, F.text)
async def on_kb_add_link(message: Message, user: User, state: FSMContext):
    if not _is_admin(user):
        return

    data = await state.get_data()
    new_kb = data.get("new_kb", {})
    new_kb["link_url"] = message.text.strip()
    await state.update_data(new_kb=new_kb)
    await state.set_state(KBAddFSM.confirm)

    await _show_kb_add_preview(message, new_kb, edit=False)


async def _show_kb_add_preview(message, new_kb: dict, edit: bool = False):
    cat_name = _CATEGORY_NAMES.get(KBCategory(new_kb.get("category", "")), "—")
    sphere = _SPHERE_NAMES.get(new_kb.get("sphere_tag"), "—") if new_kb.get("sphere_tag") else "—"
    link = new_kb.get("link_url") or "—"

    text = (
        "📋 Превью нового материала:\n\n"
        f"📌 Название: {new_kb.get('title', '—')}\n"
        f"📂 Категория: {cat_name}\n"
        f"🔹 Сфера: {sphere}\n"
        f"🔗 Ссылка: {link}\n\n"
        f"{new_kb.get('content', '—')}\n\n"
        "Сохранить?"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data="kbadd_save")
    builder.button(text="❌ Отмена", callback_data="kbadd_cancel")
    builder.adjust(2)

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(KBAddFSM.confirm, F.data == "kbadd_save")
async def on_kb_add_save(callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    new_kb = data.get("new_kb", {})

    item = await create_kb_item(
        session,
        title=new_kb.get("title", ""),
        category=KBCategory(new_kb.get("category", "material")),
        content=new_kb.get("content", ""),
        sphere_tag=new_kb.get("sphere_tag"),
        link_url=new_kb.get("link_url"),
    )

    await state.clear()

    await callback.message.edit_text(
        f"✅ Материал «{item.title}» добавлен (ID: {item.id}).",
        reply_markup=kb_categories_kb(),
    )
    await callback.answer()


@router.callback_query(KBAddFSM.confirm, F.data == "kbadd_cancel")
async def on_kb_add_cancel(callback: CallbackQuery, user: User, state: FSMContext):
    if not _is_admin(user):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ Добавление отменено.",
        reply_markup=kb_categories_kb(),
    )
    await callback.answer()
