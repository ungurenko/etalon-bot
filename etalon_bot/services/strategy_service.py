"""Генерация и парсинг стратегии развития клиента."""

import asyncio
import logging
import re

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database.models import StrategyStatus, User
from etalon_bot.database.queries import (
    get_user,
    save_item,
    save_stage,
    save_strategy,
    update_user_field,
)
from etalon_bot.services.context_builder import build_strategy_prompt
from etalon_bot.services.llm_service import call_llm
from etalon_bot.utils.text_utils import split_long_message

logger = logging.getLogger(__name__)

# ── Debounced strategy regeneration ──

REGEN_DEBOUNCE_SECONDS = 30
_pending_regen: dict[int, asyncio.Task] = {}


def schedule_strategy_regen(bot: Bot, user_id: int) -> None:
    """Планирует перегенерацию стратегии через REGEN_DEBOUNCE_SECONDS.

    Повторный вызов отменяет предыдущий таймер — это даёт клиенту возможность
    сделать несколько правок подряд, после чего запускается ровно одна регенерация.
    """
    existing = _pending_regen.get(user_id)
    if existing and not existing.done():
        existing.cancel()

    task = asyncio.create_task(_delayed_regen(bot, user_id))
    _pending_regen[user_id] = task


async def _delayed_regen(bot: Bot, user_id: int) -> None:
    """Ждёт debounce и перегенерирует стратегию клиента."""
    # Локальный импорт, чтобы избежать циклического импорта через database.engine → models
    from etalon_bot.database.engine import SessionFactory

    try:
        await asyncio.sleep(REGEN_DEBOUNCE_SECONDS)

        async with SessionFactory() as session:
            user = await get_user(session, user_id)
            if user is None:
                logger.warning("Regen skipped: user %d not found", user_id)
                return

            try:
                await bot.send_message(
                    user_id,
                    "🔄 Обновляю твою стратегию с учётом новых ответов…\n"
                    "Это займёт минуту."
                )
            except Exception as exc:
                logger.warning(
                    "Failed to send regen notice to %d: %s", user_id, exc
                )

            text = await generate_strategy(session, user)
            strategy = await save_strategy(session, user_id, text)
            await parse_strategy_to_stages(session, strategy.id, text)
            await update_user_field(
                session,
                user_id,
                strategy_status=StrategyStatus.active,
                current_stage=1,
            )

            name = user.display_name or user.full_name or ""
            header = f"🌟 {name}, твоя обновлённая стратегия готова!\n\n" if name else "🌟 Твоя обновлённая стратегия готова!\n\n"
            full_message = f"{header}{text}"
            for chunk in split_long_message(full_message):
                await bot.send_message(user_id, chunk)

            logger.info("Strategy regenerated for user %d", user_id)

    except asyncio.CancelledError:
        # Перебит новым таймером — это ожидаемое поведение debounce
        raise
    except Exception as exc:
        logger.exception(
            "Strategy regen failed for user %d: %s", user_id, exc
        )
        try:
            await bot.send_message(
                user_id,
                "⚠️ Не удалось обновить стратегию автоматически. "
                "Напиши администратору, он поможет."
            )
        except Exception:
            pass
    finally:
        _pending_regen.pop(user_id, None)


async def generate_strategy(session: AsyncSession, user: User) -> str:
    """Генерирует текст стратегии через LLM.

    Args:
        session: Асинхронная сессия БД.
        user: Объект пользователя.

    Returns:
        Полный текст стратегии от LLM.
    """
    system_prompt, user_prompt = await build_strategy_prompt(session, user)

    logger.info(
        "Генерация стратегии для пользователя %d", user.telegram_id
    )

    text = await call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=3000,
    )

    logger.info(
        "Стратегия сгенерирована для пользователя %d: %d символов",
        user.telegram_id,
        len(text),
    )

    return text


async def parse_strategy_to_stages(
    session: AsyncSession,
    strategy_id: int,
    full_text: str,
) -> None:
    """Парсит текст стратегии в этапы (StrategyStage) и пункты (StageItem).

    Ищет паттерны вида:
    - "Этап 1:" / "**Этап 1:**" / "### Этап 1:" и т.п.
    - Внутри каждого этапа буллеты (- или *) становятся пунктами

    Если парсинг не нашёл этапов, создаёт один этап с полным текстом.

    Args:
        session: Асинхронная сессия БД.
        strategy_id: ID стратегии в БД.
        full_text: Полный текст стратегии от LLM.
    """
    stages_data = _parse_stages(full_text)

    if not stages_data:
        logger.warning(
            "Не удалось распарсить этапы для стратегии %d, "
            "создаю один этап с полным текстом",
            strategy_id,
        )
        stage = await save_stage(
            session,
            strategy_id=strategy_id,
            stage_number=1,
            title="Общий план",
            description=full_text,
            duration_months=6,
        )
        await save_item(session, stage_id=stage.id, item_number=1, text="Следовать плану")
        return

    for stage_data in stages_data:
        stage = await save_stage(
            session,
            strategy_id=strategy_id,
            stage_number=stage_data["number"],
            title=stage_data["title"],
            description=stage_data["description"],
            duration_months=stage_data.get("duration", 3),
        )

        for idx, item_text in enumerate(stage_data["items"], start=1):
            await save_item(
                session,
                stage_id=stage.id,
                item_number=idx,
                text=item_text,
            )

    logger.info(
        "Стратегия %d: распарсено %d этапов",
        strategy_id,
        len(stages_data),
    )


def _parse_stages(text: str) -> list[dict]:
    """Извлекает этапы из текста стратегии.

    Returns:
        Список словарей с ключами: number, title, description, items, duration.
    """
    # Паттерн для заголовков этапов:
    # "Этап 1:", "**Этап 1:**", "### Этап 1:", "1. Этап:", "Этап 1." и т.п.
    stage_pattern = re.compile(
        r"(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*{1,2})?\s*"
        r"(?:Этап\s+(\d+)[\s.:—\-]+(.+?))"
        r"(?:\*{1,2})?\s*$",
        re.MULTILINE | re.IGNORECASE,
    )

    matches = list(stage_pattern.finditer(text))
    if not matches:
        # Альтернативный паттерн: "1. Название" или "1) Название"
        alt_pattern = re.compile(
            r"(?:^|\n)\s*(\d+)\s*[.)]\s*(?:\*{1,2})?(.+?)(?:\*{1,2})?\s*$",
            re.MULTILINE,
        )
        matches = list(alt_pattern.finditer(text))

    if not matches:
        return []

    stages: list[dict] = []

    for i, match in enumerate(matches):
        number = int(match.group(1))
        title = match.group(2).strip().rstrip(":").rstrip("*").strip()

        # Тело этапа — от конца текущего заголовка до начала следующего
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        # Извлекаем пункты (буллеты)
        items = _extract_items(body)

        # Попробуем найти длительность в тексте
        duration = _extract_duration(title + " " + body)

        stages.append({
            "number": number,
            "title": truncate_title(title),
            "description": body[:1000] if body else "",
            "items": items,
            "duration": duration,
        })

    return stages


def _extract_items(body: str) -> list[str]:
    """Извлекает буллет-пункты из тела этапа."""
    # Ищем строки, начинающиеся с -, *, •, или цифра с точкой внутри этапа
    item_pattern = re.compile(
        r"^\s*(?:[-•*]|\d+[.)]\s)\s*(.+)$",
        re.MULTILINE,
    )

    items: list[str] = []
    for match in item_pattern.finditer(body):
        item_text = match.group(1).strip()
        # Убираем markdown-жирность
        item_text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", item_text)
        item_text = item_text.strip()
        if item_text and len(item_text) > 2:
            items.append(item_text)

    return items


def _extract_duration(text: str) -> int:
    """Пытается извлечь длительность в месяцах из текста."""
    # "2-4 месяца", "3 месяца", "на 6 месяцев"
    match = re.search(r"(\d+)\s*[-–—]\s*(\d+)\s*месяц", text)
    if match:
        low = int(match.group(1))
        high = int(match.group(2))
        return (low + high) // 2

    match = re.search(r"(\d+)\s*месяц", text)
    if match:
        return int(match.group(1))

    return 3  # по умолчанию


def truncate_title(title: str, max_length: int = 280) -> str:
    """Обрезает заголовок этапа до допустимой длины."""
    if len(title) <= max_length:
        return title
    return title[: max_length - 3].rstrip() + "..."
