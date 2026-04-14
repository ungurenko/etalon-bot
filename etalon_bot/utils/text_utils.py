"""Утилиты для работы с текстом: разбивка длинных сообщений, обрезка."""

import re


def split_long_message(text: str, max_length: int = 4096) -> list[str]:
    """Разбивает длинный текст на части <= max_length.

    Порядок приоритета разбивки:
    1. По абзацам (\\n\\n)
    2. По строкам (\\n)
    3. По предложениям (. ! ?)
    4. По словам (пробел)

    Никогда не разрезает слово пополам.
    """
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Ищем лучшую точку разбивки в пределах max_length
        cut_at = _find_split_point(remaining, max_length)
        chunk = remaining[:cut_at].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut_at:].lstrip("\n")

    return chunks if chunks else [""]


def _find_split_point(text: str, max_length: int) -> int:
    """Находит оптимальную точку разбивки в пределах max_length."""
    segment = text[:max_length]

    # 1. По абзацам
    pos = segment.rfind("\n\n")
    if pos > max_length // 4:
        return pos + 2

    # 2. По строкам
    pos = segment.rfind("\n")
    if pos > max_length // 4:
        return pos + 1

    # 3. По предложениям (ищем последний ". " или "! " или "? ")
    match = None
    for m in re.finditer(r'[.!?]\s', segment):
        match = m
    if match and match.end() > max_length // 4:
        return match.end()

    # 4. По пробелам (слова)
    pos = segment.rfind(" ")
    if pos > max_length // 4:
        return pos + 1

    # Крайний случай: жёсткая обрезка по max_length
    return max_length


def truncate_text(text: str, max_length: int = 300) -> str:
    """Обрезает текст до max_length символов, добавляя '...' если обрезано."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text

    # Обрезаем на 3 символа раньше, чтобы поместились "..."
    truncated = text[: max_length - 3]

    # Не обрезаем посреди слова
    last_space = truncated.rfind(" ")
    if last_space > max_length // 2:
        truncated = truncated[:last_space]

    return truncated.rstrip() + "..."
