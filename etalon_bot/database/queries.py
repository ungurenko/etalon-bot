from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from etalon_bot.database.models import (
    User, UserRole, UserStatus, OnboardingStatus, StrategyStatus,
    SphereAnswer, EtalonVersion, Strategy, StrategyStage, StageItem,
    Conversation, MessageRole, MessageType, ProgressCheck, CheckType,
    KnowledgeBase, KBCategory, BotSetting, OnboardingQuestion,
    IntermediateData,
)


# ── Users ──

async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.get(User, telegram_id)


async def create_user(
    session: AsyncSession, telegram_id: int,
    username: str | None, full_name: str
) -> User:
    user = User(telegram_id=telegram_id, username=username, full_name=full_name)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user_status(session: AsyncSession, telegram_id: int, status: UserStatus):
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(status=status)
    )
    await session.commit()


async def update_user_field(session: AsyncSession, telegram_id: int, **kwargs):
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(**kwargs)
    )
    await session.commit()


async def get_all_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


async def get_users_by_status(session: AsyncSession, status: UserStatus) -> list[User]:
    result = await session.execute(
        select(User).where(User.status == status).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


async def get_active_clients(session: AsyncSession) -> list[User]:
    result = await session.execute(
        select(User).where(
            User.status == UserStatus.active,
            User.role == UserRole.client
        )
    )
    return list(result.scalars().all())


async def get_inactive_clients(session: AsyncSession, days: int) -> list[User]:
    threshold = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(User).where(
            User.status == UserStatus.active,
            User.role == UserRole.client,
            User.last_activity_at < threshold,
            User.bot_blocked == False,
        )
    )
    return list(result.scalars().all())


async def get_clients_count(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.client))
    active = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.client, User.status == UserStatus.active)
    )
    pending = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.client, User.status == UserStatus.pending)
    )
    blocked = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.client, User.status == UserStatus.blocked)
    )
    return {"total": total or 0, "active": active or 0, "pending": pending or 0, "blocked": blocked or 0}


# ── Sphere Answers ──

async def save_answer(
    session: AsyncSession, user_id: int, sphere_number: int,
    question_number: int, question_text: str, answer_text: str | None,
    is_voice: bool = False, is_skipped: bool = False
):
    existing = await session.execute(
        select(SphereAnswer).where(
            SphereAnswer.user_id == user_id,
            SphereAnswer.sphere_number == sphere_number,
            SphereAnswer.question_number == question_number,
        )
    )
    answer = existing.scalar_one_or_none()
    if answer:
        answer.answer_text = answer_text
        answer.is_voice = is_voice
        answer.is_skipped = is_skipped
        answer.question_text = question_text
    else:
        answer = SphereAnswer(
            user_id=user_id, sphere_number=sphere_number,
            question_number=question_number, question_text=question_text,
            answer_text=answer_text, is_voice=is_voice, is_skipped=is_skipped,
        )
        session.add(answer)
    await session.commit()


async def get_answers_by_user(session: AsyncSession, user_id: int) -> list[SphereAnswer]:
    result = await session.execute(
        select(SphereAnswer).where(SphereAnswer.user_id == user_id)
        .order_by(SphereAnswer.sphere_number, SphereAnswer.question_number)
    )
    return list(result.scalars().all())


async def get_answers_by_sphere(
    session: AsyncSession, user_id: int, sphere_number: int
) -> list[SphereAnswer]:
    result = await session.execute(
        select(SphereAnswer).where(
            SphereAnswer.user_id == user_id,
            SphereAnswer.sphere_number == sphere_number,
        ).order_by(SphereAnswer.question_number)
    )
    return list(result.scalars().all())


async def delete_answers_for_sphere(
    session: AsyncSession, user_id: int, sphere_number: int
):
    await session.execute(
        delete(SphereAnswer).where(
            SphereAnswer.user_id == user_id,
            SphereAnswer.sphere_number == sphere_number,
        )
    )
    await session.commit()


# ── Etalon Versions ──

async def save_etalon_block(
    session: AsyncSession, user_id: int, block_number: int,
    block_name: str, content: str
):
    existing = await session.execute(
        select(EtalonVersion).where(
            EtalonVersion.user_id == user_id,
            EtalonVersion.block_number == block_number,
        )
    )
    block = existing.scalar_one_or_none()
    if block:
        block.content = content
        block.block_name = block_name
    else:
        block = EtalonVersion(
            user_id=user_id, block_number=block_number,
            block_name=block_name, content=content,
        )
        session.add(block)
    await session.commit()


async def get_etalon_for_user(session: AsyncSession, user_id: int) -> list[EtalonVersion]:
    result = await session.execute(
        select(EtalonVersion).where(EtalonVersion.user_id == user_id)
        .order_by(EtalonVersion.block_number)
    )
    return list(result.scalars().all())


async def delete_etalon_for_user(session: AsyncSession, user_id: int):
    await session.execute(
        delete(EtalonVersion).where(EtalonVersion.user_id == user_id)
    )
    await session.commit()


async def has_etalon(session: AsyncSession, user_id: int) -> bool:
    result = await session.scalar(
        select(func.count()).select_from(EtalonVersion).where(
            EtalonVersion.user_id == user_id,
            EtalonVersion.content != "",
        )
    )
    return (result or 0) > 0


async def set_etalon_voice_mode(session: AsyncSession, user_id: int, enabled: bool):
    await session.execute(
        update(User).where(User.telegram_id == user_id).values(etalon_voice_mode=enabled)
    )
    await session.commit()


# ── Strategies ──

async def save_strategy(session: AsyncSession, user_id: int, full_text: str) -> Strategy:
    await session.execute(
        update(Strategy).where(
            Strategy.user_id == user_id, Strategy.is_active == True
        ).values(is_active=False)
    )
    strategy = Strategy(user_id=user_id, full_text=full_text)
    session.add(strategy)
    await session.commit()
    await session.refresh(strategy)
    return strategy


async def get_active_strategy(session: AsyncSession, user_id: int) -> Strategy | None:
    result = await session.execute(
        select(Strategy).where(Strategy.user_id == user_id, Strategy.is_active == True)
    )
    return result.scalar_one_or_none()


# ── Strategy Stages ──

async def save_stage(
    session: AsyncSession, strategy_id: int, stage_number: int,
    title: str, description: str, duration_months: int = 3
) -> StrategyStage:
    stage = StrategyStage(
        strategy_id=strategy_id, stage_number=stage_number,
        title=title, description=description, duration_months=duration_months,
    )
    session.add(stage)
    await session.commit()
    await session.refresh(stage)
    return stage


async def get_stages_for_strategy(session: AsyncSession, strategy_id: int) -> list[StrategyStage]:
    result = await session.execute(
        select(StrategyStage).where(StrategyStage.strategy_id == strategy_id)
        .order_by(StrategyStage.stage_number)
    )
    return list(result.scalars().all())


async def get_stage_by_number(
    session: AsyncSession, strategy_id: int, stage_number: int
) -> StrategyStage | None:
    result = await session.execute(
        select(StrategyStage).where(
            StrategyStage.strategy_id == strategy_id,
            StrategyStage.stage_number == stage_number,
        )
    )
    return result.scalar_one_or_none()


# ── Stage Items ──

async def save_item(
    session: AsyncSession, stage_id: int, item_number: int, text: str
) -> StageItem:
    item = StageItem(stage_id=stage_id, item_number=item_number, text=text)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_items_for_stage(session: AsyncSession, stage_id: int) -> list[StageItem]:
    result = await session.execute(
        select(StageItem).where(StageItem.stage_id == stage_id)
        .order_by(StageItem.item_number)
    )
    return list(result.scalars().all())


async def toggle_item(session: AsyncSession, item_id: int) -> bool:
    item = await session.get(StageItem, item_id)
    if not item:
        return False
    item.is_completed = not item.is_completed
    item.completed_at = datetime.utcnow() if item.is_completed else None
    await session.commit()
    return item.is_completed


async def get_stage_progress(session: AsyncSession, stage_id: int) -> tuple[int, int]:
    items = await get_items_for_stage(session, stage_id)
    total = len(items)
    completed = sum(1 for i in items if i.is_completed)
    return total, completed


# ── Conversations ──

async def save_message(
    session: AsyncSession, user_id: int, role: MessageRole,
    content: str, message_type: MessageType = MessageType.text
):
    msg = Conversation(
        user_id=user_id, role=role, content=content, message_type=message_type
    )
    session.add(msg)
    await session.commit()


async def get_recent_messages(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation).where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc()).limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return messages


# ── Progress Checks ──

async def save_progress_check(
    session: AsyncSession, user_id: int, stage_id: int,
    items_total: int, items_completed: int,
    check_type: CheckType = CheckType.manual
):
    check = ProgressCheck(
        user_id=user_id, stage_id=stage_id,
        items_total=items_total, items_completed=items_completed,
        check_type=check_type,
    )
    session.add(check)
    await session.commit()


# ── Knowledge Base ──

async def create_kb_item(
    session: AsyncSession, title: str, category: KBCategory,
    content: str, sphere_tag: int | None = None, link_url: str | None = None
) -> KnowledgeBase:
    item = KnowledgeBase(
        title=title, category=category, content=content,
        sphere_tag=sphere_tag, link_url=link_url,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_kb_items(
    session: AsyncSession, category: KBCategory | None = None,
    sphere_tag: int | None = None, active_only: bool = True
) -> list[KnowledgeBase]:
    q = select(KnowledgeBase)
    if active_only:
        q = q.where(KnowledgeBase.is_active == True)
    if category:
        q = q.where(KnowledgeBase.category == category)
    if sphere_tag is not None:
        q = q.where(KnowledgeBase.sphere_tag == sphere_tag)
    result = await session.execute(q.order_by(KnowledgeBase.id))
    return list(result.scalars().all())


async def get_kb_item(session: AsyncSession, item_id: int) -> KnowledgeBase | None:
    return await session.get(KnowledgeBase, item_id)


async def update_kb_item(session: AsyncSession, item_id: int, **kwargs):
    await session.execute(
        update(KnowledgeBase).where(KnowledgeBase.id == item_id).values(**kwargs)
    )
    await session.commit()


async def delete_kb_item(session: AsyncSession, item_id: int):
    await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == item_id))
    await session.commit()


# ── Bot Settings ──

async def get_setting(session: AsyncSession, key: str) -> str | None:
    setting = await session.get(BotSetting, key)
    return setting.value if setting else None


async def get_setting_json(session: AsyncSession, key: str):
    val = await get_setting(session, key)
    if not val:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid JSON in setting %r: %s", key, val)
        return None


async def set_setting(session: AsyncSession, key: str, value: str):
    existing = await session.get(BotSetting, key)
    if existing:
        existing.value = value
    else:
        session.add(BotSetting(key=key, value=value))
    await session.commit()


async def init_default_settings(session: AsyncSession):
    defaults = {
        "checkin_schedule": json.dumps({"days": ["mon", "thu"], "time": "10:00", "timezone": "Europe/Moscow"}),
        "reminder_thresholds": json.dumps({"soft": 3, "medium": 7, "hard": 14}),
        "warm_addresses": json.dumps(["милая", "невероятная", "дорогая", "прекрасная", "чудесная"]),
    }
    for key, value in defaults.items():
        existing = await session.get(BotSetting, key)
        if not existing:
            session.add(BotSetting(key=key, value=value))
    await session.commit()


# ── Onboarding Questions ──

async def get_questions_for_sphere(
    session: AsyncSession, sphere_number: int
) -> list[OnboardingQuestion]:
    result = await session.execute(
        select(OnboardingQuestion).where(
            OnboardingQuestion.sphere_number == sphere_number,
            OnboardingQuestion.is_active == True,
        ).order_by(OnboardingQuestion.question_number)
    )
    return list(result.scalars().all())


async def get_sphere_name(session: AsyncSession, sphere_number: int) -> str:
    result = await session.execute(
        select(OnboardingQuestion.sphere_name).where(
            OnboardingQuestion.sphere_number == sphere_number
        ).limit(1)
    )
    name = result.scalar_one_or_none()
    return name or f"Сфера {sphere_number}"


async def get_total_questions_in_sphere(session: AsyncSession, sphere_number: int) -> int:
    result = await session.scalar(
        select(func.count()).select_from(OnboardingQuestion).where(
            OnboardingQuestion.sphere_number == sphere_number,
            OnboardingQuestion.is_active == True,
        )
    )
    return result or 0


async def init_questions_from_json(session: AsyncSession, json_path: str):
    existing = await session.scalar(
        select(func.count()).select_from(OnboardingQuestion)
    )
    if existing and existing > 0:
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for sphere in data:
        for q in sphere["questions"]:
            session.add(OnboardingQuestion(
                sphere_number=sphere["sphere_number"],
                sphere_name=sphere["sphere_name"],
                question_number=q["number"],
                question_text=q["text"],
            ))
    await session.commit()


# ── Statistics ──

async def get_onboarding_stats(session: AsyncSession) -> dict:
    not_started = await session.scalar(
        select(func.count()).select_from(User).where(
            User.role == UserRole.client,
            User.onboarding_status == OnboardingStatus.not_started,
        )
    )
    in_progress = await session.scalar(
        select(func.count()).select_from(User).where(
            User.role == UserRole.client,
            User.onboarding_status == OnboardingStatus.in_progress,
        )
    )
    completed = await session.scalar(
        select(func.count()).select_from(User).where(
            User.role == UserRole.client,
            User.onboarding_status == OnboardingStatus.completed,
        )
    )
    return {
        "not_started": not_started or 0,
        "in_progress": in_progress or 0,
        "completed": completed or 0,
    }


async def get_strategy_stats(session: AsyncSession) -> dict:
    generated = await session.scalar(
        select(func.count()).select_from(Strategy)
    )
    active = await session.scalar(
        select(func.count()).select_from(Strategy).where(Strategy.is_active == True)
    )
    return {"generated": generated or 0, "active": active or 0}


# ── Intermediate Data ──

async def save_intermediate_data(
    session: AsyncSession, user_id: int, category: str,
    content: str, added_by: str = "client"
) -> IntermediateData:
    item = IntermediateData(
        user_id=user_id, category=category,
        content=content, added_by=added_by,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_intermediate_data(
    session: AsyncSession, user_id: int, category: str | None = None
) -> list[IntermediateData]:
    q = select(IntermediateData).where(IntermediateData.user_id == user_id)
    if category:
        q = q.where(IntermediateData.category == category)
    result = await session.execute(q.order_by(IntermediateData.created_at.desc()))
    return list(result.scalars().all())


async def delete_intermediate_data(session: AsyncSession, item_id: int):
    await session.execute(
        delete(IntermediateData).where(IntermediateData.id == item_id)
    )
    await session.commit()


async def get_intermediate_item(session: AsyncSession, item_id: int) -> IntermediateData | None:
    return await session.get(IntermediateData, item_id)


# ── Access Expiry ──

async def get_users_expiring_soon(session: AsyncSession, days_remaining: int) -> list[User]:
    now = datetime.utcnow()
    deadline = now + timedelta(days=days_remaining)
    result = await session.execute(
        select(User).where(
            User.role == UserRole.client,
            User.status == UserStatus.active,
            User.access_until.isnot(None),
            User.access_until > now,
            User.access_until <= deadline,
            User.bot_blocked == False,
        )
    )
    return list(result.scalars().all())


async def get_activity_stats(session: AsyncSession, days: int = 7) -> dict:
    threshold = datetime.utcnow() - timedelta(days=days)
    active_users = await session.scalar(
        select(func.count(func.distinct(Conversation.user_id))).where(
            Conversation.created_at > threshold
        )
    )
    client_msgs = await session.scalar(
        select(func.count()).select_from(Conversation).where(
            Conversation.created_at > threshold,
            Conversation.role == MessageRole.client,
        )
    )
    bot_msgs = await session.scalar(
        select(func.count()).select_from(Conversation).where(
            Conversation.created_at > threshold,
            Conversation.role == MessageRole.assistant,
        )
    )
    return {
        "active_users": active_users or 0,
        "client_messages": client_msgs or 0,
        "bot_messages": bot_msgs or 0,
    }
