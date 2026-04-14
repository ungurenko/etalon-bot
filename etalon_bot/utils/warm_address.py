"""Генерация тёплого обращения к клиенту."""

import random
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database.queries import get_setting_json


# Запасные варианты по полу
_DEFAULT_ADDRESSES_FEMALE = ["милая", "невероятная", "дорогая", "прекрасная", "чудесная"]
_DEFAULT_ADDRESSES_MALE = ["дорогой", "невероятный", "замечательный", "прекрасный"]
_DEFAULT_ADDRESSES_NEUTRAL = ["дорогой друг"]


async def get_warm_address(
    session: AsyncSession, name: str, gender: Optional[str] = None
) -> str:
    """Возвращает тёплое обращение вида 'милая Анна'.

    Берёт список прилагательных из bot_settings (ключ "warm_addresses").
    Если настройка отсутствует, использует список по умолчанию с учётом пола.

    Args:
        session: Асинхронная сессия БД.
        name: Имя клиента (display_name или full_name).
        gender: Пол клиента ('female', 'male', 'neutral'). По умолчанию female.

    Returns:
        Строка вида "прилагательное Имя", например "милая Анна".
    """
    addresses = await get_setting_json(session, "warm_addresses")

    if gender == "male":
        if not addresses or not isinstance(addresses, list):
            addresses = _DEFAULT_ADDRESSES_MALE
    elif gender == "neutral":
        addresses = _DEFAULT_ADDRESSES_NEUTRAL
    else:
        if not addresses or not isinstance(addresses, list):
            addresses = _DEFAULT_ADDRESSES_FEMALE

    adjective = random.choice(addresses)
    display = name.strip() if name else ""

    if display:
        return f"{adjective} {display}"
    return adjective
