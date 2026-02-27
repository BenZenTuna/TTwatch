import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Boolean, Integer, Float, Text, Date,
    DateTime, ForeignKey, BigInteger, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class Topic(Base):
    __tablename__ = "topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    icon = Column(Text)
    config = Column(JSONB, default=dict)
    refresh_interval_minutes = Column(Integer, default=120)
    last_refreshed_at = Column(DateTime(timezone=True))
    next_refresh_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "name"),)

    user = relationship("User", back_populates="topics")
    clusters = relationship("Cluster", back_populates="topic", cascade="all, delete-orphan")
    articles = relationship("Article", back_populates="topic", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="topic", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    source_type = Column(Text, default="rss")
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    config = Column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("user_id", "topic_id", "url"),)

    topic = relationship("Topic", back_populates="sources")


class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(Text, nullable=False)
    color = Column(Text)
    article_count = Column(Integer, default=0)
    trend_score = Column(Float, default=0)
    velocity = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    topic = relationship("Topic", back_populates="clusters")
    articles = relationship("Article", back_populates="cluster")


class Article(Base):
    __tablename__ = "articles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    source_name = Column(Text)
    source_url = Column(Text)
    published_at = Column(DateTime(timezone=True))
    ingested_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    content_hash = Column(Text)
    raw_storage_key = Column(Text)
    summary = Column(Text)
    sentiment_score = Column(Float)
    relevance_score = Column(Float)
    key_quotes = Column(JSONB, default=list)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="SET NULL"))
    embedding_id = Column(Text)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of = Column(UUID(as_uuid=True), ForeignKey("articles.id"))

    __table_args__ = (UniqueConstraint("user_id", "topic_id", "url"),)

    topic = relationship("Topic", back_populates="articles")
    cluster = relationship("Cluster", back_populates="articles")


class Entity(Base):
    __tablename__ = "entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    first_seen = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "name", "type", "topic_id"),)


class EntityArticleMap(Base):
    __tablename__ = "entity_article_map"

    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    article_id = Column(UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class EntityClusterMap(Base):
    __tablename__ = "entity_cluster_map"

    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class SentimentHistory(Base):
    __tablename__ = "sentiment_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="SET NULL"))
    cluster_keyword = Column(Text)
    period_start = Column(Date, nullable=False)
    avg_sentiment = Column(Float)
    article_count = Column(Integer)

    __table_args__ = (UniqueConstraint("user_id", "cluster_id", "period_start"),)


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    query_text = Column(Text, nullable=False)
    schedule = Column(Text, default="on_refresh")
    last_run = Column(DateTime(timezone=True))
    last_result_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Briefing(Base):
    __tablename__ = "briefings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    summary = Column(Text)
    highlights = Column(JSONB, default=list)
    new_entities = Column(JSONB, default=list)
    watch_items = Column(JSONB, default=list)
    coverage_gaps = Column(JSONB, default=list)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    model_used = Column(Text)
