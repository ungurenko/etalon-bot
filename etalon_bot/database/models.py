import enum
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime, Enum, ForeignKey,
    UniqueConstraint, Index, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    client = "client"
    admin = "admin"


class UserStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    inactive = "inactive"
    blocked = "blocked"


class OnboardingStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    completed = "completed"


class StrategyStatus(str, enum.Enum):
    none = "none"
    generated = "generated"
    active = "active"


class MessageRole(str, enum.Enum):
    client = "client"
    assistant = "assistant"


class MessageType(str, enum.Enum):
    text = "text"
    voice = "voice"
    checkin = "checkin"
    reminder = "reminder"
    strategy = "strategy"


class CheckType(str, enum.Enum):
    proactive = "proactive"
    manual = "manual"


class KBCategory(str, enum.Enum):
    procrastination = "procrastination"
    practice = "practice"
    material = "material"


class ImageMoment(str, enum.Enum):
    etalon_ready = "etalon_ready"
    strategy_ready = "strategy_ready"
    strategy_completed = "strategy_completed"


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.client)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.pending)
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        Enum(OnboardingStatus), default=OnboardingStatus.not_started
    )
    current_sphere: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_question: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    strategy_status: Mapped[StrategyStatus] = mapped_column(
        Enum(StrategyStatus), default=StrategyStatus.none
    )
    current_stage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    etalon_voice_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    access_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    sphere_answers: Mapped[List["SphereAnswer"]] = relationship(back_populates="user")
    etalon_versions: Mapped[List["EtalonVersion"]] = relationship(back_populates="user")
    strategies: Mapped[List["Strategy"]] = relationship(back_populates="user")
    conversations: Mapped[List["Conversation"]] = relationship(back_populates="user")
    progress_checks: Mapped[List["ProgressCheck"]] = relationship(back_populates="user")
    intermediate_data: Mapped[List["IntermediateData"]] = relationship(back_populates="user")


class SphereAnswer(Base):
    __tablename__ = "sphere_answers"
    __table_args__ = (
        UniqueConstraint("user_id", "sphere_number", "question_number", name="uq_sphere_answer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    sphere_number: Mapped[int] = mapped_column(Integer)
    question_number: Mapped[int] = mapped_column(Integer)
    question_text: Mapped[str] = mapped_column(Text, default="")
    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    is_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sphere_answers")


class EtalonVersion(Base):
    __tablename__ = "etalon_versions"
    __table_args__ = (
        UniqueConstraint("user_id", "block_number", name="uq_etalon_block"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    block_number: Mapped[int] = mapped_column(Integer)
    block_name: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="etalon_versions")


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    full_text: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="strategies")
    stages: Mapped[List["StrategyStage"]] = relationship(back_populates="strategy")


class StrategyStage(Base):
    __tablename__ = "strategy_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, ForeignKey("strategies.id"))
    stage_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    duration_months: Mapped[int] = mapped_column(Integer, default=3)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="stages")
    items: Mapped[List["StageItem"]] = relationship(back_populates="stage")


class StageItem(Base):
    __tablename__ = "stage_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage_id: Mapped[int] = mapped_column(Integer, ForeignKey("strategy_stages.id"))
    item_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    stage: Mapped["StrategyStage"] = relationship(back_populates="items")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conv_user_date", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole))
    message_type: Mapped[MessageType] = mapped_column(Enum(MessageType), default=MessageType.text)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")


class ProgressCheck(Base):
    __tablename__ = "progress_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    stage_id: Mapped[int] = mapped_column(Integer, ForeignKey("strategy_stages.id"))
    items_total: Mapped[int] = mapped_column(Integer, default=0)
    items_completed: Mapped[int] = mapped_column(Integer, default=0)
    check_type: Mapped[CheckType] = mapped_column(Enum(CheckType), default=CheckType.manual)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="progress_checks")


class IntermediateData(Base):
    __tablename__ = "intermediate_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    category: Mapped[str] = mapped_column(String(50), default="goal")
    content: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[str] = mapped_column(String(20), default="client")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="intermediate_data")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[KBCategory] = mapped_column(Enum(KBCategory))
    sphere_tag: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    link_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class GeneratedImage(Base):
    __tablename__ = "generated_images"
    __table_args__ = (
        Index("ix_genimg_user_moment", "user_id", "moment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.telegram_id"))
    moment: Mapped[ImageMoment] = mapped_column(Enum(ImageMoment))
    file_id: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OnboardingQuestion(Base):
    __tablename__ = "onboarding_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sphere_number: Mapped[int] = mapped_column(Integer)
    sphere_name: Mapped[str] = mapped_column(String(200), default="")
    question_number: Mapped[int] = mapped_column(Integer)
    question_text: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
