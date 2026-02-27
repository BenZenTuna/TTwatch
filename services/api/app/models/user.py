import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Boolean, Integer, Text, DateTime, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, unique=True, nullable=False)
    display_name = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    max_topics = Column(Integer, default=10)
    max_articles_per_topic = Column(Integer, default=5000)
    max_api_keys = Column(Integer, default=5)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login_at = Column(DateTime(timezone=True))

    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    topics = relationship("Topic", back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_prefix = Column(Text, nullable=False)
    key_hash = Column(Text, nullable=False)
    label = Column(Text, default="default")
    scopes = Column(JSONB, default=lambda: ["read", "write", "search"])
    rate_limit_per_minute = Column(Integer, default=60)
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="api_keys")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, nullable=False)
    device_info = Column(Text)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="refresh_tokens")
