from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.database import queries
from etalon_bot.database.models import OnboardingStatus, StrategyStatus, User


async def compute_menu_flags(session: AsyncSession, user: User | None) -> dict:
    """Compute which dynamic buttons should appear in the client main menu."""
    if user is None:
        return {
            "onboarding_completed": False,
            "can_gen_strategy": False,
            "can_make_period_plan": False,
        }
    completed = user.onboarding_status == OnboardingStatus.completed
    has_etalon = completed and await queries.has_etalon(session, user.telegram_id)
    can_gen = has_etalon and user.strategy_status != StrategyStatus.active
    return {
        "onboarding_completed": completed,
        "can_gen_strategy": can_gen,
        "can_make_period_plan": has_etalon,
    }
