from __future__ import annotations
"""Обращение к LLM через OpenRouter API с ретраями."""

import asyncio
import logging
import re
import time

import aiohttp

from etalon_bot.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 30  # секунд


class LLMError(Exception):
    """Ошибка при обращении к LLM."""


def _format_openrouter_error(status: int, body: dict) -> str:
    """Делает ошибки OpenRouter понятнее для типовых случаев."""
    error_msg = body.get("error", {}).get("message", str(body))
    normalized = error_msg.lower()

    if status == 402 or any(
        marker in normalized
        for marker in ("insufficient", "credit", "balance", "payment required")
    ):
        return (
            "На OpenRouter не хватает баланса для платной модели "
            "или не включена оплата. Пополни баланс и повтори попытку."
        )

    return f"OpenRouter API error {status}: {error_msg}"


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
    started_at = time.perf_counter()

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
                            logger.error(
                                "LLM returned empty response: model=%s attempt=%d",
                                OPENROUTER_MODEL,
                                attempt + 1,
                            )
                            raise LLMError("LLM вернул пустой ответ")
                        elapsed_ms = (time.perf_counter() - started_at) * 1000
                        logger.info(
                            "LLM success: model=%s attempt=%d response_len=%d elapsed=%.1fms",
                            OPENROUTER_MODEL,
                            attempt + 1,
                            len(content),
                            elapsed_ms,
                        )
                        return _strip_markdown_wrapper(content)

                    # 429 — rate limit
                    if resp.status == 429:
                        wait = 5
                        logger.warning(
                            "LLM rate limit: model=%s status=429 attempt=%d wait=%ds error=%s",
                            OPENROUTER_MODEL,
                            attempt + 1,
                            wait,
                            body.get("error", {}).get("message", ""),
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
                            "LLM server error: model=%s status=%d attempt=%d wait=%ds error=%s",
                            OPENROUTER_MODEL,
                            resp.status,
                            attempt + 1,
                            wait,
                            body.get("error", {}).get("message", ""),
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
                    logger.error(
                        "LLM request failed: model=%s status=%d error=%s",
                        OPENROUTER_MODEL,
                        resp.status,
                        body.get("error", {}).get("message", str(body)),
                    )
                    raise LLMError(_format_openrouter_error(resp.status, body))

            except asyncio.TimeoutError:
                logger.warning(
                    "LLM timeout: model=%s timeout=%ds attempt=%d",
                    OPENROUTER_MODEL,
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
                logger.error(
                    "Ошибка сети при обращении к LLM: model=%s error=%s",
                    OPENROUTER_MODEL,
                    e,
                )
                raise LLMError(f"Ошибка сети: {e}") from e

    raise last_error or LLMError("Неизвестная ошибка LLM")
