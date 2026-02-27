import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class LlmTaskConfig(Base):
    __tablename__ = "llm_task_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_category = Column(Text, nullable=False)
    model_target = Column(Text, nullable=False, default="auto")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "task_category", name="uq_llm_task_config_user_category"),
    )
