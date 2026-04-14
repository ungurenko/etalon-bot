"""Утилиты для работы с текстом: разбивка длинных сообщений, обрезка, Markdown → Telegram HTML."""

import re


def markdown_to_telegram_html(text: str) -> str:
    """Конвертирует типичный Markdown-вывод LLM в Telegram HTML.

    Telegram поддерживает: <b>, <i>, <u>, <s>, <code>, <pre>, <blockquote>.
    Всё остальное (заголовки, списки) остаётся как plain-текст —
    Telegram рендерит их нормально, звёздочки/подчёркивания вокруг слов мешают.
    """
    if not text:
        return ""

    # 1. Эскейпим HTML-спецсимволы, чтобы случайные "<" или "&" из текста
    #    не ломали парсер Telegram.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 2. Конвертируем Markdown в безопасные HTML-теги (которые мы сами вставляем).
    # Блоки кода ``` ... ``` (многострочные) -> <pre>
    text = re.sub(
        r"```(?:\w+)?\n?(.+?)\n?```",
        lambda m: "<pre>" + m.group(1) + "</pre>",
        text,
        flags=re.DOTALL,
    )
    # Жирный: **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    # Подчёркнутый: __text__
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text, flags=re.DOTALL)
    # Курсив: *text* (одиночная звёздочка, не внутри слова)
    text = re.sub(r"(?<![\*\w])\*([^\*\n]+?)\*(?![\*\w])", r"<i>\1</i>", text)
    # Курсив: _text_ (одиночное подчёркивание, не внутри слова)
    text = re.sub(r"(?<![_\w])_([^_\n]+?)_(?![_\w])", r"<i>\1</i>", text)
    # Инлайн-код: `text`
    text = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", text)
    # Заголовки Markdown (### Header) — в Telegram нет заголовков, делаем жирным
    text = re.sub(r"^#{1,6}\s+(.+?)\s*$", r"<b>\1</b>", text, flags=re.MULTILINE)

    return text


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
