"""Транскрипция голосовых сообщений через Groq Whisper API."""

import logging
from io import BytesIO

import aiohttp
from aiogram import Bot

from etalon_bot.config import GROQ_API_KEY

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


class TranscriptionError(Exception):
    """Ошибка транскрипции голосового сообщения."""


class FileTooLargeError(TranscriptionError):
    """Файл превышает допустимый размер (25 MB)."""


async def transcribe_voice(bot: Bot, file_id: str) -> str:
    """Скачивает голосовое сообщение и транскрибирует через Groq Whisper.

    Args:
        bot: Экземпляр aiogram Bot.
        file_id: file_id голосового сообщения из Telegram.

    Returns:
        Текст транскрипции.

    Raises:
        FileTooLargeError: Если файл > 25 MB.
        TranscriptionError: Если API вернул ошибку.
    """
    # Получаем информацию о файле
    file_info = await bot.get_file(file_id)

    if file_info.file_size and file_info.file_size > MAX_FILE_SIZE:
        raise FileTooLargeError(
            f"Файл слишком большой: {file_info.file_size} байт "
            f"(максимум {MAX_FILE_SIZE} байт)"
        )

    # Скачиваем файл в память
    buffer = BytesIO()
    await bot.download_file(file_info.file_path, destination=buffer)
    buffer.seek(0)

    file_bytes = buffer.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise FileTooLargeError(
            f"Файл слишком большой: {len(file_bytes)} байт "
            f"(максимум {MAX_FILE_SIZE} байт)"
        )

    # Отправляем в Groq API
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    form = aiohttp.FormData()
    form.add_field(
        "file",
        file_bytes,
        filename="voice.ogg",
        content_type="audio/ogg",
    )
    form.add_field("model", "whisper-large-v3")
    form.add_field("language", "ru")
    form.add_field("response_format", "text")

    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                GROQ_TRANSCRIPTION_URL,
                headers=headers,
                data=form,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                body = await resp.text()

                if resp.status != 200:
                    logger.error(
                        "Groq API error: status=%d, body=%s",
                        resp.status,
                        body[:500],
                    )
                    raise TranscriptionError(
                        f"Groq API вернул ошибку {resp.status}: {body[:200]}"
                    )

                text = body.strip()
                if not text:
                    raise TranscriptionError("Groq API вернул пустой ответ")

                logger.info("Транскрипция завершена: %d символов", len(text))
                return text

    except aiohttp.ClientError as e:
        logger.error("Ошибка сети при обращении к Groq: %s", e)
        raise TranscriptionError(f"Ошибка сети: {e}") from e
