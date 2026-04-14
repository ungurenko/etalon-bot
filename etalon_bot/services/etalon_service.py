"""Shared etalon version logic for admin and client handlers."""
from __future__ import annotations

import re

ETALON_BLOCKS = [
    (1, "Ключевые способности и таланты", "Опишите таланты, сильные стороны, суперспособности:"),
    (2, "Идеальная реализация", "Опишите проекты, деятельность, миссию:"),
    (3, "Финансовая модель", "Опишите источники дохода, целевые суммы:"),
    (4, "Отношения и окружение", "Опишите отношения, партнёр, семья, круг общения:"),
    (5, "Тело и энергия", "Опишите здоровье, энергию, внешность:"),
    (6, "Внутреннее состояние", "Опишите самооценку, уверенность, мировосприятие:"),
    (7, "Образ жизни и среда", "Опишите организацию жизни, место, время:"),
]

BLOCK_SHORT_NAMES = [
    "Способности",
    "Реализация",
    "Финансы",
    "Отношения",
    "Тело",
    "Состояние",
    "Образ жизни",
]


def format_preview(name: str, blocks: dict) -> str:
    """Format etalon blocks into a preview message."""
    lines = [f"📋 Эталонная версия для {name}:\n"]
    for i, short_name in enumerate(BLOCK_SHORT_NAMES, start=1):
        content = blocks.get(i, blocks.get(str(i), "—"))
        preview = content[:100] + "..." if len(content) > 100 else content
        lines.append(f"🔹 {short_name}: {preview}")
    lines.append("\nВсё верно?")
    return "\n".join(lines)


def get_block_prompt(block_num: int, name: str) -> str:
    """Return prompt text for a given block number."""
    _, block_name, description = ETALON_BLOCKS[block_num - 1]
    return (
        f"📋 Эталонная версия: {name}\n\n"
        f"Блок {block_num} из 7: {block_name}\n"
        f"{description}"
    )


def parse_structured_etalon(llm_response: str) -> dict:
    """Parse LLM-structured response into dict {1: text, ..., 7: text}."""
    blocks = {}
    pattern = r"БЛОК\s*(\d)\s*:\s*(.+?)(?=БЛОК\s*\d|$)"
    matches = re.findall(pattern, llm_response, re.DOTALL | re.IGNORECASE)
    for num_str, content in matches:
        num = int(num_str)
        if 1 <= num <= 7:
            blocks[num] = content.strip()

    # Fallback: если парсинг не удался
    if len(blocks) < 3:
        blocks = {1: llm_response.strip()}
        for i in range(2, 8):
            blocks[i] = "Не указано"

    # Заполнить пропущенные блоки
    for i in range(1, 8):
        if i not in blocks:
            blocks[i] = "Не указано"

    return blocks
