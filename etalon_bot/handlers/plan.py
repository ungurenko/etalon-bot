from __future__ import annotations
"""
Handlers: strategy plan viewing, item toggling, progress tracking.
"""

import logging

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from etalon_bot.config import ADMIN_IDS
from etalon_bot.database import queries
from etalon_bot.keyboards.client_kb import plan_view_kb

logger = logging.getLogger(__name__)

router = Router(name="plan")


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _render_plan(
    callback: CallbackQuery,
    session: AsyncSession,
    user,
    stage_number: int | None = None,
    edit: bool = True,
) -> None:
    """Render the plan view for a given stage."""
    strategy = await queries.get_active_strategy(session, user.telegram_id)
    if strategy is None:
        text = "У тебя пока нет стратегии. Она появится после завершения онбординга 🌿"
        if edit:
            await callback.message.edit_text(text)
        else:
            await callback.message.answer(text)
        return

    stages = await queries.get_stages_for_strategy(session, strategy.id)
    if not stages:
        await callback.message.edit_text(
            "Стратегия загружена, но этапы ещё не сформированы. Скоро всё появится 🌿"
        )
        return

    total_stages = len(stages)
    current = stage_number or user.current_stage or 1
    if current < 1:
        current = 1
    if current > total_stages:
        current = total_stages

    # Find the stage object
    stage = None
    for s in stages:
        if s.stage_number == current:
            stage = s
            break
    if stage is None:
        stage = stages[0]
        current = stage.stage_number

    items = await queries.get_items_for_stage(session, stage.id)

    # Build text
    completed_count = sum(1 for i in items if i.is_completed)
    total_items = len(items)

    text_lines = [
        f"📋 <b>Этап {current} из {total_stages}: {stage.title}</b>\n",
    ]
    if stage.description:
        text_lines.append(f"{stage.description}\n")
    text_lines.append(
        f"Прогресс: {completed_count}/{total_items} выполнено\n"
    )

    for item in items:
        mark = "✅" if item.is_completed else "⬜"
        text_lines.append(f"{mark} {item.text}")

    text = "\n".join(text_lines)
    kb = plan_view_kb(items, current, total_stages)

    if edit:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Callbacks ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_plan")
async def cb_menu_plan(
    callback: CallbackQuery, session: AsyncSession, **kwargs
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer("Напиши /start", show_alert=True)
        return

    await _render_plan(callback, session, user)
    await callback.answer()


@router.callback_query(F.data.startswith("plan_toggle_"))
async def cb_plan_toggle(
    callback: CallbackQuery, session: AsyncSession, **kwargs
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    item_id = int(callback.data.split("_")[-1])
    await queries.toggle_item(session, item_id)

    # Re-render the plan at the same stage
    await _render_plan(callback, session, user)
    await callback.answer()


@router.callback_query(F.data == "plan_save")
async def cb_plan_save(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    **kwargs,
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    strategy = await queries.get_active_strategy(session, user.telegram_id)
    if strategy is None:
        await callback.answer("Стратегия не найдена", show_alert=True)
        return

    current_stage_num = user.current_stage or 1
    stage = await queries.get_stage_by_number(session, strategy.id, current_stage_num)
    if stage is None:
        await callback.answer("Этап не найден", show_alert=True)
        return

    total, completed = await queries.get_stage_progress(session, stage.id)

    await queries.save_progress_check(
        session,
        user_id=user.telegram_id,
        stage_id=stage.id,
        items_total=total,
        items_completed=completed,
    )

    if total > 0 and completed >= total:
        # Stage completed
        name = user.display_name or user.full_name or ""
        username_str = f"@{user.username}" if user.username else "без username"

        stages = await queries.get_stages_for_strategy(session, strategy.id)
        total_stages = len(stages)

        if current_stage_num < total_stages:
            next_stage = current_stage_num + 1
            await queries.update_user_field(
                session, user.telegram_id, current_stage=next_stage
            )
            await callback.message.answer(
                f"🎉 Поздравляю! Этап {current_stage_num} полностью выполнен!\n\n"
                f"Переходим к этапу {next_stage}. Ты молодец! 💛"
            )
        else:
            await callback.message.answer(
                f"🎉 Невероятно, {name}! Ты выполнила все этапы стратегии!\n\n"
                "Это огромное достижение. Поздравляю! 🌟"
            )

        # Notify admins
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🏆 {name} ({username_str}) завершила этап {current_stage_num}. "
                    f"Прогресс: {completed}/{total}.",
                )
            except Exception as exc:
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)

        # Re-render plan for the new stage
        await _render_plan(callback, session, user, edit=False)
    else:
        await callback.message.answer("Прогресс сохранён! 💛")

    await callback.answer()


@router.callback_query(F.data == "plan_prev")
async def cb_plan_prev(
    callback: CallbackQuery, session: AsyncSession, **kwargs
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    current = (user.current_stage or 1) - 1
    if current < 1:
        await callback.answer("Это первый этап")
        return

    await queries.update_user_field(
        session, user.telegram_id, current_stage=current
    )
    await _render_plan(callback, session, user, stage_number=current)
    await callback.answer()


@router.callback_query(F.data == "plan_next")
async def cb_plan_next(
    callback: CallbackQuery, session: AsyncSession, **kwargs
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    strategy = await queries.get_active_strategy(session, user.telegram_id)
    if strategy is None:
        await callback.answer("Стратегия не найдена", show_alert=True)
        return

    stages = await queries.get_stages_for_strategy(session, strategy.id)
    total_stages = len(stages)
    current = (user.current_stage or 1) + 1

    if current > total_stages:
        await callback.answer("Это последний этап")
        return

    await queries.update_user_field(
        session, user.telegram_id, current_stage=current
    )
    await _render_plan(callback, session, user, stage_number=current)
    await callback.answer()


@router.callback_query(F.data == "menu_progress")
async def cb_menu_progress(
    callback: CallbackQuery, session: AsyncSession, **kwargs
):
    user = kwargs.get("user")
    if user is None:
        await callback.answer()
        return

    strategy = await queries.get_active_strategy(session, user.telegram_id)
    if strategy is None:
        await callback.message.answer(
            "У тебя пока нет стратегии. Она появится после завершения онбординга 🌿"
        )
        await callback.answer()
        return

    stages = await queries.get_stages_for_strategy(session, strategy.id)
    if not stages:
        await callback.message.answer("Этапы ещё не сформированы 🌿")
        await callback.answer()
        return

    current_stage = user.current_stage or 1
    lines = [
        f"📊 <b>Прогресс по стратегии</b>\n",
        f"Текущий этап: {current_stage} из {len(stages)}\n",
    ]

    for stage in stages:
        total, completed = await queries.get_stage_progress(session, stage.id)
        if stage.stage_number == current_stage:
            marker = "👉"
        elif stage.is_completed:
            marker = "✅"
        else:
            marker = "⬜"

        progress_str = f"{completed}/{total}" if total > 0 else "—"
        lines.append(f"{marker} Этап {stage.stage_number}: {stage.title} ({progress_str})")

    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()
