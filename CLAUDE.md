# EtalonBot — Telegram-бот «Эталонная Версия»

Стратегический ИИ-помощник для личностного развития. Ведёт клиента от Точки А к эталонной версии через онбординг, персональную стратегию и проактивные проверки прогресса.

## Commands

- `python -m etalon_bot.bot` — запуск бота
- `docker build -t etalon-bot etalon_bot/` — сборка Docker-образа
- Деплой: Railway с Volume `/data/` для SQLite

## Deployment

**Railway (production):**
- Project: `etalon-bot` — https://railway.com/project/11a28220-e5d9-4ba9-a564-4e72fa4baa65
- Service: `etalon-bot` (polling-режим, Dockerfile builder)
- Volume: `etalon-bot-volume` смонтирован на `/data/` (SQLite persistent)
- Bot: @test_ung1bot
- Конфиг: `Dockerfile` и `railway.json` в корне репо
- Auth для CLI: `export RAILWAY_API_TOKEN=<account-token>` (login через браузер ломается из-за бага keychain)
- Редеплой: `railway up --ci` из корня (после `railway link` к проекту etalon-bot)

**GitHub:** https://github.com/ungurenko/etalon-bot (private)

## Architecture

| Path | Purpose |
|------|---------|
| `etalon_bot/bot.py` | Точка входа, регистрация роутеров и middleware |
| `etalon_bot/config.py` | Env vars: BOT_TOKEN, ADMIN_IDS, API keys |
| `etalon_bot/database/` | SQLAlchemy 2.0 async модели, engine, CRUD |
| `etalon_bot/handlers/` | aiogram 3.x роутеры (admin_*, start, onboarding, chat, plan) |
| `etalon_bot/keyboards/` | InlineKeyboardBuilder — client_kb, admin_kb |
| `etalon_bot/middlewares/` | auth (роль/статус), rate_limit, logging |
| `etalon_bot/services/` | LLM (OpenRouter), Whisper (Groq), context_builder, strategy |
| `etalon_bot/scheduler/` | APScheduler — проактивные чекины и напоминания |
| `etalon_bot/data/` | init_questions.json — 45 вопросов онбординга |

## Key Patterns

**Router priority**: admin handlers → onboarding (FSM) → plan → start → chat (catch-all, последний).

**FSM + DB state**: онбординг хранит прогресс в БД (current_sphere/current_question), FSM только маршрутизирует сообщения. Переживает рестарт.

**Message batching**: asyncio.Task с задержкой 3с (онбординг) / 5с (чат). Новое сообщение отменяет предыдущий таск.

**Auth middleware**: инжектит `user` и `session` в data dict каждого хендлера. Проверяет роль для admin_* callbacks.

**Etalon upload**: два режима — свободная форма (голосовое → Whisper → LLM структурирует в 7 блоков) и поблочный ввод.

## Tech Stack

Python 3.11+, aiogram 3.x, SQLAlchemy 2.0 + aiosqlite, APScheduler, aiohttp, OpenRouter API (Gemma 4 31B), Groq Whisper, Docker + Railway

## Critical Constraints

- SQLite: файловая БД в `/data/etalon_bot.db`, Railway Volume для persistence
- Telegram message limit: 4096 символов — длинные тексты split по абзацам (utils/text_utils.py)
- Env vars обязательны: BOT_TOKEN, ADMIN_IDS. Без GROQ_API_KEY голосовые не работают
- Python 3.9 совместимость: `Optional[str]` вместо `str | None` в моделях SQLAlchemy (используется `from typing import Optional`)
