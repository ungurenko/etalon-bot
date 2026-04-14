"""Сборка контекста для LLM-промптов на основе данных клиента."""

import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database.models import User, MessageRole
from etalon_bot.database.queries import (
    get_answers_by_user,
    get_etalon_for_user,
    get_active_strategy,
    get_stages_for_strategy,
    get_items_for_stage,
    get_stage_by_number,
    get_recent_messages,
    get_kb_items,
    get_sphere_name,
    get_intermediate_data,
)
from etalon_bot.utils.text_utils import truncate_text
from etalon_bot.utils.warm_address import get_warm_address

logger = logging.getLogger(__name__)


# ── Вспомогательные функции ──


async def _compress_point_a(
    session: AsyncSession, user: User, per_sphere_limit: int | None = 300
) -> str:
    """Сжимает ответы Точки А по сферам.

    per_sphere_limit: None = без обрезки (для стратегии), число = лимит в символах (для чата).
    """
    answers = await get_answers_by_user(session, user.telegram_id)
    if not answers:
        return "Данные Точки А ещё не заполнены."

    by_sphere: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for a in answers:
        if a.answer_text and not a.is_skipped:
            by_sphere[a.sphere_number].append((a.question_text or "", a.answer_text))

    parts: list[str] = []
    for sphere_num in sorted(by_sphere.keys()):
        sphere_name = await get_sphere_name(session, sphere_num)
        if per_sphere_limit is None:
            qa_lines = [
                f"    Q: {q}\n    A: {a}" for q, a in by_sphere[sphere_num]
            ]
            parts.append(f"  {sphere_name}:\n" + "\n".join(qa_lines))
        else:
            combined = " | ".join(a for _, a in by_sphere[sphere_num])
            compressed = truncate_text(combined, max_length=per_sphere_limit)
            parts.append(f"  {sphere_name}: {compressed}")

    return "\n".join(parts) if parts else "Данные Точки А ещё не заполнены."


async def _build_etalon_text(session: AsyncSession, user: User) -> str:
    """Собирает текст Эталонной версии из блоков."""
    blocks = await get_etalon_for_user(session, user.telegram_id)
    if not blocks:
        return "Эталонная версия ещё не сформирована."

    parts = [f"  {b.block_name}: {b.content}" for b in blocks if b.content]
    return "\n".join(parts) if parts else "Эталонная версия ещё не сформирована."


async def _build_strategy_text(session: AsyncSession, user: User) -> str:
    """Собирает краткое описание текущей стратегии."""
    strategy = await get_active_strategy(session, user.telegram_id)
    if not strategy:
        return "Стратегия ещё не создана."

    stages = await get_stages_for_strategy(session, strategy.id)
    if not stages:
        return truncate_text(strategy.full_text, max_length=500)

    parts: list[str] = []
    for s in stages:
        items = await get_items_for_stage(session, s.id)
        completed = sum(1 for i in items if i.is_completed)
        total = len(items)
        status = "завершён" if s.is_completed else f"{completed}/{total}"
        parts.append(f"  Этап {s.stage_number}. {s.title} [{status}]")

    return "\n".join(parts)


async def _build_current_stage_text(session: AsyncSession, user: User) -> str:
    """Описание текущего этапа стратегии с пунктами."""
    strategy = await get_active_strategy(session, user.telegram_id)
    if not strategy or not user.current_stage:
        return "Текущий этап не определён."

    stage = await get_stage_by_number(session, strategy.id, user.current_stage)
    if not stage:
        return "Текущий этап не определён."

    items = await get_items_for_stage(session, stage.id)
    lines = [f"Этап {stage.stage_number}: {stage.title}"]
    if stage.description:
        lines.append(stage.description)
    for item in items:
        mark = "✅" if item.is_completed else "⬜"
        lines.append(f"  {mark} {item.text}")

    return "\n".join(lines)


async def _build_progress_text(session: AsyncSession, user: User) -> str:
    """Краткая строка прогресса."""
    strategy = await get_active_strategy(session, user.telegram_id)
    if not strategy:
        return "Нет активной стратегии."

    stages = await get_stages_for_strategy(session, strategy.id)
    if not stages:
        return "Этапы ещё не определены."

    total_items = 0
    completed_items = 0
    completed_stages = 0

    for s in stages:
        if s.is_completed:
            completed_stages += 1
        items = await get_items_for_stage(session, s.id)
        total_items += len(items)
        completed_items += sum(1 for i in items if i.is_completed)

    return (
        f"Этапов: {completed_stages}/{len(stages)}, "
        f"пунктов: {completed_items}/{total_items}"
    )


async def _build_history(session: AsyncSession, user: User) -> str:
    """Последние 10 сообщений диалога."""
    messages = await get_recent_messages(session, user.telegram_id, limit=10)
    if not messages:
        return "История пуста — это первое взаимодействие."

    lines: list[str] = []
    for msg in messages:
        role_label = "Клиент" if msg.role == MessageRole.client else "Бот"
        content = truncate_text(msg.content, max_length=200)
        lines.append(f"[{role_label}]: {content}")

    return "\n".join(lines)


async def _build_kb_section(session: AsyncSession) -> str:
    """Подборка материалов из базы знаний."""
    items = await get_kb_items(session, active_only=True)
    if not items:
        return "База знаний пуста."

    lines: list[str] = []
    for item in items[:15]:  # не более 15 записей, чтобы не раздувать промпт
        entry = f"  [{item.category.value}] {item.title}"
        if item.content:
            entry += f": {truncate_text(item.content, max_length=150)}"
        lines.append(entry)

    return "\n".join(lines)


_INTERMEDIATE_LABELS = {
    "goal": "Цель",
    "insight": "Инсайт",
    "course_notes": "Из курса",
    "other": "Другое",
}


async def _build_intermediate_data_text(
    session: AsyncSession, user: User, item_limit: int | None = 200
) -> str:
    """Собирает промежуточные цели и данные клиента.

    item_limit: None = без обрезки, число = макс символов на запись.
    """
    items = await get_intermediate_data(session, user.telegram_id)
    if not items:
        return "Промежуточные цели и данные не добавлены."

    parts: list[str] = []
    for item in items if item_limit is None else items[:15]:
        label = _INTERMEDIATE_LABELS.get(item.category, item.category)
        content = item.content if item_limit is None else truncate_text(item.content, max_length=item_limit)
        parts.append(f"  [{label}] {content}")

    return "\n".join(parts)


# ── Публичные функции ──


SYSTEM_PROMPT_TEMPLATE_ETALON_VOICE = """\
Ты — эталонная версия {name}. Не ассистент, не помощник — это ты сам(а), только из той точки, где ты уже живёшь так, как описано ниже.

Сейчас ты разговариваешь со своей текущей версией — той, которой пока тяжело, которая ещё в пути. Твоя задача — поддержать её из своего опыта. Ты уже прошёл(ла) этот путь и знаешь, как.

КАК ТЫ ГОВОРИШЬ:
- От первого лица: «я знаю это состояние», «когда я была там, мне помогло…», «я помню, как это»
- НИКОГДА не говори «согласно твоему эталону», «по данным», «как ассистент» — это не данные, это твоя жизнь
- Содержимое блоков ниже — это твои реальные способности, отношения, тело, состояние. Ссылайся на них как на свой опыт
- Точка А — это где находится твоя младшая версия сейчас. Помни оттуда, но не застревай — ты уже дальше
- Тон: тёплый, мудрый, как старший друг из будущего. Не свысока, не поучая — рядом
- Используй обращение «{warm_address}» — так ты обращаешься к себе с любовью
- Эмодзи умеренно (1-2 на сообщение)
- Длина: 100-300 слов

ФОРМАТИРОВАНИЕ (Telegram):
- Markdown: **жирный** для ключевых идей, *курсив* для акцентов, __подчёркнутый__ для важного
- НЕ используй # и ### — в Telegram нет заголовков
- Списки: обычный текст с «— » или «1. »

ВАЖНО: если младшая версия в кризисе — мягко напомни, что специалист (психолог, врач) тоже может помочь. Не заменяй их.

ТВОЯ ЖИЗНЬ СЕЙЧАС (эталонная версия):
{etalon_text}

ГДЕ СЕЙЧАС ТВОЯ МЛАДШАЯ ВЕРСИЯ (Точка А):
{compressed_point_a}

ПРОМЕЖУТОЧНЫЕ ЦЕЛИ И ИНСАЙТЫ, КОТОРЫЕ У НЕЁ ЕСТЬ:
{intermediate_data}

КУДА ОНА ДВИЖЕТСЯ (стратегия):
{strategy_text}

ТЕКУЩИЙ ЭТАП:
{current_stage}

ПОСЛЕДНИЕ РАЗГОВОРЫ С НЕЙ:
{history}"""


SYSTEM_PROMPT_TEMPLATE = """\
Ты — стратегический ИИ-помощник "Эталонная Версия". Ты ведёшь клиента от его текущего состояния к максимальной реализации способностей.

СТИЛЬ ОБЩЕНИЯ:
- Мягкий, поддерживающий, немного вдохновляющий
- Обращайся к клиенту: "{warm_address}"
- Не давай общих советов — только персонализированные на основе данных клиента
- Не ставь диагнозов, не заменяй психолога или врача
- Если клиент в кризисе — мягко рекомендуй обратиться к специалисту
- Используй тёплые эмодзи, но умеренно (не более 2-3 на сообщение)
- Говори "ты", не "Вы"
- Длина ответа: 150-400 слов

ФОРМАТИРОВАНИЕ (Telegram):
- Используй Markdown: **жирный** для ключевых идей, *курсив* для акцентов, __подчёркнутый__ для важного
- НЕ используй заголовки с # или ### — в Telegram их нет
- Списки пиши обычным текстом с буллетами «— » или нумерацией «1. »
- 1-2 эмодзи в ключевых местах для структуры, не больше

БАЗА ЗНАНИЙ:
{relevant_kb}

ДАННЫЕ КЛИЕНТА:
Имя: {name}
Точка А: {compressed_point_a}
Эталонная версия: {etalon_text}
Промежуточные цели и данные: {intermediate_data}
Текущая стратегия: {strategy_text}
Текущий этап: {current_stage}
Прогресс: {progress}

ИСТОРИЯ ПОСЛЕДНИХ ВЗАИМОДЕЙСТВИЙ:
{history}"""


async def build_chat_context(session: AsyncSession, user: User) -> str:
    """Собирает полный системный промпт для чата с клиентом.

    Args:
        session: Асинхронная сессия БД.
        user: Объект пользователя.

    Returns:
        Готовый системный промпт со всеми данными клиента.
    """
    name = user.display_name or user.full_name or "друг"
    warm = await get_warm_address(session, name, gender=user.gender)

    compressed_point_a = await _compress_point_a(session, user)
    etalon_text = await _build_etalon_text(session, user)
    intermediate_data = await _build_intermediate_data_text(session, user)
    strategy_text = await _build_strategy_text(session, user)
    current_stage = await _build_current_stage_text(session, user)
    progress = await _build_progress_text(session, user)
    history = await _build_history(session, user)
    relevant_kb = await _build_kb_section(session)

    if user.etalon_voice_mode:
        return SYSTEM_PROMPT_TEMPLATE_ETALON_VOICE.format(
            warm_address=warm,
            name=name,
            compressed_point_a=compressed_point_a,
            etalon_text=etalon_text,
            intermediate_data=intermediate_data,
            strategy_text=strategy_text,
            current_stage=current_stage,
            history=history,
        )

    return SYSTEM_PROMPT_TEMPLATE.format(
        warm_address=warm,
        name=name,
        compressed_point_a=compressed_point_a,
        etalon_text=etalon_text,
        intermediate_data=intermediate_data,
        strategy_text=strategy_text,
        current_stage=current_stage,
        progress=progress,
        history=history,
        relevant_kb=relevant_kb,
    )


STRATEGY_SYSTEM_PROMPT = """\
Ты — стратегический ИИ-помощник "Эталонная Версия". \
Твоя задача — составить персональную пошаговую стратегию развития клиента \
на основе трёх источников: текущего состояния (Точка А), желаемого результата (Эталонная версия) \
и промежуточных целей/данных клиента.

Стратегия должна быть реалистичной, конкретной и учитывать все сферы жизни клиента. \
Обязательно учти промежуточные цели и инсайты клиента при формировании этапов.

ФОРМАТИРОВАНИЕ (строго для Telegram):
- Выделяй названия этапов и ключевые фразы через **жирный**
- Акценты — через *курсив*, важное — через __подчёркнутый__
- НЕ используй # и ### — заголовков в Telegram нет
- Списки — обычным текстом с «— » или «1. »
- 1-2 тематических эмодзи в начале разделов для визуальной структуры (не больше)"""


STRATEGY_USER_TEMPLATE = """\
На основе Точки А, Эталонной версии и промежуточных данных клиента составь пошаговую стратегию на 1-3 года.

Структура стратегии:
1. Общая картина: где клиент сейчас и куда идёт (2-3 предложения)
2. Этапы (3-6 этапов, каждый на 2-6 месяцев):
   - Название этапа
   - Фокус этапа (какая сфера / сферы)
   - Конкретные пункты действий (3-7 на этап)
   - Ожидаемый результат этапа
3. Приоритеты первого месяца: 3-5 конкретных действий для старта

ДАННЫЕ КЛИЕНТА:
Имя: {name}

ТОЧКА А (текущее состояние):
{compressed_point_a}

ЭТАЛОННАЯ ВЕРСИЯ (желаемый результат):
{etalon_text}

ПРОМЕЖУТОЧНЫЕ ЦЕЛИ И ДАННЫЕ:
{intermediate_data}"""


async def build_strategy_prompt(
    session: AsyncSession, user: User
) -> tuple[str, str]:
    """Собирает промпты для генерации стратегии.

    Returns:
        Кортеж (system_prompt, user_prompt).
    """
    name = user.display_name or user.full_name or "клиент"
    # Strategy generation uses FULL answers and intermediate data — we want
    # the LLM to see every detail. Gemini 3 Flash handles 1M tokens of context,
    # so there's no risk of overflow even for very talkative clients.
    compressed_point_a = await _compress_point_a(session, user, per_sphere_limit=None)
    etalon_text = await _build_etalon_text(session, user)
    intermediate_data = await _build_intermediate_data_text(session, user, item_limit=None)

    user_prompt = STRATEGY_USER_TEMPLATE.format(
        name=name,
        compressed_point_a=compressed_point_a,
        etalon_text=etalon_text,
        intermediate_data=intermediate_data,
    )

    return STRATEGY_SYSTEM_PROMPT, user_prompt


CHECKIN_SYSTEM_PROMPT = """\
Ты — стратегический ИИ-помощник "Эталонная Версия". \
Сейчас ты проводишь проактивный чек-ин с клиентом — мягко интересуешься прогрессом \
и помогаешь двигаться вперёд.

СТИЛЬ:
- Тёплый, поддерживающий тон
- Обращайся: "{warm_address}"
- Не давай длинных лекций, задай 1-2 конкретных вопроса
- Отметь прогресс, если он есть
- Если прогресса нет — не осуждай, помоги найти маленький следующий шаг
- Длина: 80-200 слов

ФОРМАТИРОВАНИЕ:
- Markdown: **жирный** для акцентов, *курсив* где уместно
- Без заголовков (# и ###). 1-2 эмодзи в тему"""


CHECKIN_SYSTEM_PROMPT_ETALON_VOICE = """\
Ты — эталонная версия {name}. Сейчас ты заглядываешь к своей младшей версии, чтобы поддержать её на пути.

КАК ТЫ ГОВОРИШЬ:
- От первого лица, как старший друг из будущего: «я помню этот этап», «когда я была здесь, мне помогало…»
- Никаких «согласно стратегии» или «по данным» — это твой пройденный путь
- Обращение «{warm_address}»
- Тон: мягкий, тёплый, без давления
- Задай 1-2 конкретных вопроса о том, как идёт у неё этап
- Если прогресс есть — порадуйся искренне; если нет — поддержи и помоги увидеть маленький следующий шаг
- Длина: 80-200 слов

ТВОЯ ЖИЗНЬ СЕЙЧАС (эталон):
{etalon_text}"""


CHECKIN_USER_TEMPLATE_ETALON_VOICE = """\
Загляни к младшей версии себя. Вот где она:

Текущий этап: {current_stage}
Прогресс: {progress}

Последние ваши разговоры:
{history}

Напиши ей короткое тёплое сообщение — поинтересуйся, как она движется."""


CHECKIN_USER_TEMPLATE = """\
Проведи проактивный чек-ин. Вот текущие данные:

Имя: {name}
Текущий этап: {current_stage}
Прогресс: {progress}

Последние взаимодействия:
{history}

Составь тёплое сообщение-чек-ин."""


async def build_checkin_prompt(
    session: AsyncSession, user: User
) -> tuple[str, str]:
    """Собирает промпты для проактивного чек-ина.

    Returns:
        Кортеж (system_prompt, user_prompt).
    """
    name = user.display_name or user.full_name or "друг"
    warm = await get_warm_address(session, name, gender=user.gender)

    current_stage = await _build_current_stage_text(session, user)
    progress = await _build_progress_text(session, user)
    history = await _build_history(session, user)

    if user.etalon_voice_mode:
        etalon_text = await _build_etalon_text(session, user)
        system_prompt = CHECKIN_SYSTEM_PROMPT_ETALON_VOICE.format(
            name=name, warm_address=warm, etalon_text=etalon_text,
        )
        user_prompt = CHECKIN_USER_TEMPLATE_ETALON_VOICE.format(
            current_stage=current_stage, progress=progress, history=history,
        )
        return system_prompt, user_prompt

    system_prompt = CHECKIN_SYSTEM_PROMPT.format(warm_address=warm)
    user_prompt = CHECKIN_USER_TEMPLATE.format(
        name=name,
        current_stage=current_stage,
        progress=progress,
        history=history,
    )

    return system_prompt, user_prompt


# ── Структурирование эталонной версии ─────────────────────────────────────


def build_structure_etalon_prompt(raw_text: str) -> tuple:
    """Промпт для структурирования свободного рассказа об эталонной версии."""
    system_prompt = (
        "Ты — помощник по структурированию текста. "
        "Тебе дан свободный рассказ о эталонной версии человека.\n"
        "Структурируй текст по 7 блокам. Используй ТОЛЬКО информацию из текста, не додумывай.\n"
        "Если для какого-то блока нет информации — напиши «Не указано».\n\n"
        "Блоки:\n"
        "1. Ключевые способности и таланты\n"
        "2. Идеальная реализация\n"
        "3. Финансовая модель\n"
        "4. Отношения и окружение\n"
        "5. Тело и энергия\n"
        "6. Внутреннее состояние\n"
        "7. Образ жизни и среда\n\n"
        "Формат ответа — строго:\n"
        "БЛОК 1: [текст]\n"
        "БЛОК 2: [текст]\n"
        "БЛОК 3: [текст]\n"
        "БЛОК 4: [текст]\n"
        "БЛОК 5: [текст]\n"
        "БЛОК 6: [текст]\n"
        "БЛОК 7: [текст]"
    )
    user_prompt = raw_text
    return system_prompt, user_prompt
