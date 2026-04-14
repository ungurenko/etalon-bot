import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from etalon_bot.database.models import Base
from etalon_bot.config import DB_PATH

logger = logging.getLogger(__name__)

db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN gender VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN access_until DATETIME",
    "ALTER TABLE users ADD COLUMN etalon_voice_mode BOOLEAN NOT NULL DEFAULT 0",
]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for sql in _MIGRATIONS:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # column already exists
