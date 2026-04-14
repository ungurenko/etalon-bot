from __future__ import annotations
"""Обращение к LLM через OpenRouter API с ретраями."""

import asyncio
import logging
import re

import aiohttp

from etalon_bot.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 30  # секунд


class LLMError(Exception):
    """Ошибка при обращении к LLM."""


def _strip_markdown_wrapper(text: str) -> str:
    """Убирает обёртку ```json ... ``` или ``` ... ``` из ответа LLM."""
    stripped = text.strip()
    pattern = r"^```(?:\w+)?\s*\n?(.*?)\n?\s*```$"
    match = re.match(pattern, stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
    timeout: int = REQUEST_TIMEOUT,
    reasoning_effort: str | None = None,
) -> str:
    """Отправляет запрос к LLM через OpenRouter.

    Args:
        system_prompt: Системный промпт.
        user_prompt: Пользовательский промпт.
        max_tokens: Максимальное количество токенов в ответе.

    Returns:
        Текст ответа LLM (без markdown-обёрток).

    Raises:
        LLMError: Если все попытки исчерпаны.
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/etalon_bot",
        "X-Title": "EtalonBot",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}

    last_error: Exception | None = None

    async with aiohttp.ClientSession() as client:
        for attempt in range(4):  # до 4 попыток (1 основная + 3 ретрая)
            try:
                async with client.post(
                    OPENROUTER_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    body = await resp.json()

                    if resp.status == 200:
                        choices = body.get("choices") or [{}]
                        content = (
                            choices[0]
                            .get("message", {})
                            .get("content", "")
                        )
                        if not content:
                            raise LLMError("LLM вернул пустой ответ")
                        return _strip_markdown_wrapper(content)

                    # 429 — rate limit
                    if resp.status == 429:
                        wait = 5
                        logger.warning(
                            "LLM rate limit (429), попытка %d/3, жду %ds",
                            attempt + 1,
                            wait,
                        )
                        last_error = LLMError(
                            f"Rate limit 429: {body.get('error', {}).get('message', '')}"
                        )
                        if attempt < 3:
                            await asyncio.sleep(wait)
                            continue
                        raise last_error

                    # 500/502/503 — серверные ошибки
                    if resp.status in (500, 502, 503):
                        wait = 10
                        logger.warning(
                            "LLM server error (%d), попытка %d/3, жду %ds",
                            resp.status,
                            attempt + 1,
                            wait,
                        )
                        last_error = LLMError(
                            f"Server error {resp.status}: "
                            f"{body.get('error', {}).get('message', '')}"
                        )
                        if attempt < 3:
                            await asyncio.sleep(wait)
                            continue
                        raise last_error

                    # Другие ошибки — не ретраим
                    error_msg = body.get("error", {}).get("message", str(body))
                    raise LLMError(
                        f"OpenRouter API error {resp.status}: {error_msg}"
                    )

            except asyncio.TimeoutError:
                logger.warning(
                    "LLM timeout (%ds), попытка %d/2",
                    timeout,
                    attempt + 1,
                )
                last_error = LLMError(
                    f"Таймаут запроса к LLM ({timeout}s)"
                )
                if attempt < 2:
                    continue
                raise last_error

            except aiohttp.ClientError as e:
                logger.error("Ошибка сети при обращении к LLM: %s", e)
                raise LLMError(f"Ошибка сети: {e}") from e

    raise last_error or LLMError("Неизвестная ошибка LLM")
