"""
Handler routers — register in order of priority.

Admin routers go first (they filter by admin role).
start      → commands, admin activation, menu callbacks
onboarding → FSM-driven onboarding flow
plan       → strategy plan viewing and progress
chat       → catch-all free dialog (lowest priority, register LAST)
"""

from etalon_bot.handlers.admin_main import router as admin_main_router
from etalon_bot.handlers.admin_clients import router as admin_clients_router
from etalon_bot.handlers.admin_etalon import router as admin_etalon_router
from etalon_bot.handlers.admin_strategy import router as admin_strategy_router
from etalon_bot.handlers.admin_knowledge import router as admin_knowledge_router
from etalon_bot.handlers.admin_broadcast import router as admin_broadcast_router
from etalon_bot.handlers.admin_settings import router as admin_settings_router
from etalon_bot.handlers.start import router as start_router
from etalon_bot.handlers.onboarding import router as onboarding_router
from etalon_bot.handlers.plan import router as plan_router
from etalon_bot.handlers.client_goals import router as client_goals_router
from etalon_bot.handlers.client_etalon import router as client_etalon_router
from etalon_bot.handlers.client_pointa import router as client_pointa_router
from etalon_bot.handlers.client_settings import router as client_settings_router
from etalon_bot.handlers.client_strategy import router as client_strategy_router
from etalon_bot.handlers.client_period_plan import router as client_period_plan_router
from etalon_bot.handlers.chat import router as chat_router

__all__ = [
    "admin_main_router",
    "admin_clients_router",
    "admin_etalon_router",
    "admin_strategy_router",
    "admin_knowledge_router",
    "admin_broadcast_router",
    "admin_settings_router",
    "start_router",
    "onboarding_router",
    "plan_router",
    "client_goals_router",
    "client_etalon_router",
    "client_pointa_router",
    "client_settings_router",
    "client_strategy_router",
    "client_period_plan_router",
    "chat_router",
]
