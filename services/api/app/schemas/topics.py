import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class TopicCreate(BaseModel):
    name: str
    icon: str | None = None
    config: dict = {}
    refresh_interval_minutes: int = 120


class TopicUpdate(BaseModel):
    """All fields optional for partial updates."""
    name: str | None = None
    icon: str | None = None
    config: dict | None = None
    refresh_interval_minutes: int | None = None


class TopicResponse(BaseModel):
    id: uuid.UUID
    name: str
    icon: str | None
    config: dict
    refresh_interval_minutes: int
    last_refreshed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClusterResponse(BaseModel):
    id: uuid.UUID
    keyword: str
    color: str | None
    article_count: int
    trend_score: float
    velocity: str | None

    model_config = {"from_attributes": True}


class ArticleResponse(BaseModel):
    id: uuid.UUID
    url: str
    title: str
    source_name: str | None
    published_at: datetime | None
    ingested_at: datetime
    summary: str | None
    sentiment_score: float | None
    relevance_score: float | None
    cluster_id: uuid.UUID | None
    is_duplicate: bool

    model_config = {"from_attributes": True}


class BriefingResponse(BaseModel):
    id: uuid.UUID
    generated_at: datetime
    summary: str | None = None
    highlights: list = Field(default_factory=list)
    new_entities: list = Field(default_factory=list)
    watch_items: list = Field(default_factory=list)
    coverage_gaps: list = Field(default_factory=list)

    model_config = {"from_attributes": True}


class SearchRequest(BaseModel):
    query: str
    topic_id: uuid.UUID
    limit: int = 20


class SearchResult(BaseModel):
    article: ArticleResponse
    score: float
