"""Генерация вдохновляющих картинок через kie.ai (gpt-image-1.5).

Используется в трёх ключевых моментах пути клиента:
- когда сохранена эталонная версия (moment=etalon_ready)
- когда готова персональная стратегия (moment=strategy_ready)
- когда клиент завершил все этапы стратегии (moment=strategy_completed)

Стиль всех картинок — мудборд/коллаж, мягкая вдохновляющая эстетика.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import KIE_API_KEY, KIE_IMAGE_MODEL, KIE_IMAGE_QUALITY
from etalon_bot.database import queries
from etalon_bot.database.models import (
    EtalonVersion,
    GeneratedImage,
    ImageMoment,
    Strategy,
    StrategyStage,
    User,
)
from etalon_bot.services.etalon_service import BLOCK_SHORT_NAMES

logger = logging.getLogger(__name__)

KIE_CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"
KIE_DETAIL_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"

# Поллинг: статус задачи опрашивается каждые POLL_INTERVAL секунд, максимум POLL_TIMEOUT
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 180

# Общий мудборд-стиль для всех картинок бота
MOODBOARD_STYLE = (
    "moodboard collage composition, soft pastel palette with warm golden accents, "
    "layered photographic clippings with torn paper edges, pressed flowers and "
    "botanical leaves, handwritten notes, subtle film grain, natural light, "
    "dreamy inspirational aesthetic, magazine tear-out vibe, no text, no letters, "
    "no words, no typography"
)


class ImageGenerationError(Exception):
    pass


# ── Public API ──────────────────────────────────────────────────────────────


async def send_image_for_moment(
    bot: Bot,
    session: AsyncSession,
    user: User,
    moment: ImageMoment,
    caption: Optional[str] = None,
    force_regenerate: bool = False,
) -> Optional[int]:
    """Генерирует (или переиспользует) картинку для момента и отправляет клиенту.

    По умолчанию идемпотентна: если для пользователя+момента уже есть file_id,
    отправляет его. При force_regenerate=True всегда генерирует заново
    (используется, например, при обновлении стратегии).
    Возвращает id message с фото или None, если что-то пошло не так.
    """
    if not KIE_API_KEY:
        logger.info("KIE_API_KEY не задан — пропускаю генерацию картинки для %d", user.telegram_id)
        return None

    chat_id = user.telegram_id

    if not force_regenerate:
        existing = await _get_existing(session, user.telegram_id, moment)
        if existing and existing.file_id:
            try:
                msg = await bot.send_photo(chat_id, existing.file_id, caption=caption)
                return msg.message_id
            except Exception as exc:
                logger.warning(
                    "Кешированный file_id для %d/%s не принят Telegram (%s) — регенерирую",
                    user.telegram_id, moment.value, exc,
                )

    try:
        prompt = await _build_prompt_for_moment(session, user, moment)
    except Exception as exc:
        logger.exception("Не удалось собрать prompt для %d/%s: %s", user.telegram_id, moment.value, exc)
        return None

    try:
        image_url = await _generate_image(prompt, aspect_ratio="1:1")
    except Exception as exc:
        logger.warning(
            "Генерация картинки для %d/%s не удалась: %s",
            user.telegram_id, moment.value, exc,
        )
        return None

    try:
        msg = await bot.send_photo(chat_id, image_url, caption=caption)
    except Exception as exc:
        logger.warning(
            "Не удалось отправить картинку по URL для %d/%s: %s — скачиваю и отправляю bytes",
            user.telegram_id, moment.value, exc,
        )
        try:
            image_bytes = await _download_image(image_url)
            msg = await bot.send_photo(
                chat_id,
                BufferedInputFile(image_bytes, filename="inspiration.png"),
                caption=caption,
            )
        except Exception as exc2:
            logger.exception(
                "Фатально: картинку отправить не удалось для %d/%s: %s",
                user.telegram_id, moment.value, exc2,
            )
            return None

    file_id = msg.photo[-1].file_id if msg.photo else None
    await _save_generated(
        session,
        user_id=user.telegram_id,
        moment=moment,
        file_id=file_id,
        source_url=image_url,
        prompt=prompt,
    )
    return msg.message_id


def fire_and_forget_moment(
    bot: Bot,
    user: User,
    moment: ImageMoment,
    caption: Optional[str] = None,
) -> None:
    """Запускает генерацию в фоне и не ждёт результата.

    Используется из хендлеров: основной текст уже отправлен, картинка догонит.
    Создаёт собственную сессию, т.к. сессия хендлера к моменту завершения
    корутины будет закрыта.
    """
    async def _runner() -> None:
        from etalon_bot.database.engine import SessionFactory

        async with SessionFactory() as session:
            try:
                await send_image_for_moment(bot, session, user, moment, caption=caption)
            except Exception as exc:
                logger.exception(
                    "fire_and_forget картинка упала для %d/%s: %s",
                    user.telegram_id, moment.value, exc,
                )

    asyncio.create_task(_runner())


# ── kie.ai client ───────────────────────────────────────────────────────────


async def _generate_image(prompt: str, aspect_ratio: str = "1:1") -> str:
    """Создаёт задачу в kie.ai, поллит статус, возвращает URL готовой картинки."""
    task_id = await _create_task(prompt, aspect_ratio)
    return await _poll_task(task_id)


async def _create_task(prompt: str, aspect_ratio: str) -> str:
    payload = {
        "model": KIE_IMAGE_MODEL,
        "input": {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "quality": KIE_IMAGE_QUALITY,
        },
    }
    headers = {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(KIE_CREATE_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise ImageGenerationError(
                    f"kie.ai createTask HTTP {resp.status}: {text[:300]}"
                )
            data = json.loads(text)

    if data.get("code") != 200:
        raise ImageGenerationError(
            f"kie.ai createTask code={data.get('code')} msg={data.get('msg')}"
        )

    task_id = (data.get("data") or {}).get("taskId")
    if not task_id:
        raise ImageGenerationError(f"kie.ai createTask без taskId: {data}")
    logger.info("kie.ai task created: %s", task_id)
    return task_id


async def _poll_task(task_id: str) -> str:
    headers = {"Authorization": f"Bearer {KIE_API_KEY}"}
    url = f"{KIE_DETAIL_URL}?taskId={task_id}"

    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT_SECONDS
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise ImageGenerationError(f"kie.ai poll timeout for {task_id}")

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

            try:
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        logger.warning(
                            "kie.ai recordInfo HTTP %s: %s", resp.status, text[:200]
                        )
                        continue
                    data = json.loads(text)
            except asyncio.TimeoutError:
                logger.warning("kie.ai recordInfo poll timed out, retrying")
                continue

            task_data = data.get("data") or {}
            state = task_data.get("state")
            if state == "success":
                result_json_raw = task_data.get("resultJson") or "{}"
                try:
                    result = json.loads(result_json_raw)
                except json.JSONDecodeError:
                    raise ImageGenerationError(
                        f"kie.ai resultJson не парсится: {result_json_raw[:300]}"
                    )
                urls = result.get("resultUrls") or []
                if not urls:
                    raise ImageGenerationError(f"kie.ai success без resultUrls: {result}")
                return urls[0]
            if state in ("fail", "failed"):
                raise ImageGenerationError(
                    f"kie.ai task {task_id} failed: "
                    f"{task_data.get('failCode')}/{task_data.get('failMsg')}"
                )
            # waiting / running — продолжаем поллить


async def _download_image(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise ImageGenerationError(
                    f"Не удалось скачать картинку: HTTP {resp.status}"
                )
            return await resp.read()


# ── DB helpers ──────────────────────────────────────────────────────────────


async def _get_existing(
    session: AsyncSession, user_id: int, moment: ImageMoment
) -> Optional[GeneratedImage]:
    result = await session.execute(
        select(GeneratedImage)
        .where(GeneratedImage.user_id == user_id, GeneratedImage.moment == moment)
        .order_by(GeneratedImage.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _save_generated(
    session: AsyncSession,
    *,
    user_id: int,
    moment: ImageMoment,
    file_id: Optional[str],
    source_url: str,
    prompt: str,
) -> None:
    row = GeneratedImage(
        user_id=user_id,
        moment=moment,
        file_id=file_id,
        source_url=source_url,
        prompt=prompt[:4000],
    )
    session.add(row)
    await session.commit()


# ── Prompt builders ─────────────────────────────────────────────────────────


async def _build_prompt_for_moment(
    session: AsyncSession, user: User, moment: ImageMoment
) -> str:
    name = user.display_name or user.full_name or "client"
    if moment == ImageMoment.etalon_ready:
        return await _build_etalon_prompt(session, user, name)
    if moment == ImageMoment.strategy_ready:
        return await _build_strategy_prompt(session, user, name)
    if moment == ImageMoment.strategy_completed:
        return await _build_completed_prompt(session, user, name)
    raise ValueError(f"Unknown image moment: {moment}")


async def _build_etalon_prompt(
    session: AsyncSession, user: User, name: str
) -> str:
    """Мудборд будущей «эталонной» версии — визуализация 7 сфер."""
    etalon_blocks = await queries.get_etalon_for_user(session, user.telegram_id)
    highlights = _etalon_highlights(etalon_blocks)

    body = (
        "An inspirational moodboard collage that visualises the ideal future self "
        "across seven life spheres: abilities and talents, purposeful work, "
        "financial abundance, meaningful relationships, body and vitality, "
        "inner calm and confidence, harmonious lifestyle and environment. "
        "The collage should feel personal, warm and aspirational — "
        "a visual dream of who this person is becoming."
    )
    if highlights:
        body += f" Subtle visual hints drawn from their vision: {highlights}."

    return f"{body} Style: {MOODBOARD_STYLE}."


async def _build_strategy_prompt(
    session: AsyncSession, user: User, name: str
) -> str:
    """Мудборд «дорожной карты» — метафора пути и этапов."""
    strategy = await queries.get_active_strategy(session, user.telegram_id)
    stage_titles: list[str] = []
    if strategy is not None:
        stages = await queries.get_stages_for_strategy(session, strategy.id)
        stage_titles = [s.title for s in stages if s.title][:5]

    body = (
        "An inspirational moodboard collage depicting a personal growth journey "
        "as a winding path through changing landscapes — early morning mist "
        "giving way to open horizons. Stepping stones, a vintage compass, a "
        "hand-drawn map fragment, a notebook with simple checkmarks, small "
        "mountain peaks in the background. The mood is hopeful, structured, "
        "gently epic — a roadmap of transformation."
    )
    if stage_titles:
        themes = ", ".join(stage_titles)
        body += f" Emotional themes of the stages to hint at: {themes}."

    return f"{body} Style: {MOODBOARD_STYLE}."


async def _build_completed_prompt(
    session: AsyncSession, user: User, name: str
) -> str:
    """Финальный мудборд — празднование достижения."""
    body = (
        "A celebratory moodboard collage marking the completion of a long "
        "personal transformation journey. A mountain summit at golden hour, "
        "open arms silhouette against a vast sunlit sky, a pressed flower "
        "bouquet, a vintage medal or ribbon, gentle confetti of light, "
        "a final page of a journal with a small heart drawn on it. "
        "The feeling is proud, grateful, luminous — arrival at a new chapter."
    )
    return f"{body} Style: {MOODBOARD_STYLE}."


def _etalon_highlights(blocks: list[EtalonVersion]) -> str:
    """Сжимает 7 блоков эталона в короткий список визуальных подсказок для prompt."""
    if not blocks:
        return ""
    parts: list[str] = []
    for b in blocks[:7]:
        idx = b.block_number - 1
        if 0 <= idx < len(BLOCK_SHORT_NAMES):
            theme = BLOCK_SHORT_NAMES[idx]
        else:
            theme = b.block_name or ""
        snippet = (b.content or "").strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:80].rstrip() + "…"
        if snippet:
            parts.append(f"{theme} — {snippet}")
    if not parts:
        return ""
    joined = "; ".join(parts)
    if len(joined) > 800:
        joined = joined[:800].rstrip() + "…"
    return joined
