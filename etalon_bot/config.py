import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable is required")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

KIE_API_KEY = os.environ.get("KIE_API_KEY", "")
KIE_IMAGE_MODEL = os.environ.get("KIE_IMAGE_MODEL", "gpt-image/1.5-text-to-image")
KIE_IMAGE_QUALITY = os.environ.get("KIE_IMAGE_QUALITY", "medium")

DB_PATH = os.environ.get("DB_PATH", "data/etalon_bot.db")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "5"))
