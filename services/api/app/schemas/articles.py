import uuid
from datetime import datetime
from pydantic import BaseModel


class ArticleDetailResponse(BaseModel):
    id: uuid.UUID
    url: str
    title: str
    source_name: str | None
    source_url: str | None
    published_at: datetime | None
    ingested_at: datetime
    summary: str | None
    sentiment_score: float | None
    relevance_score: float | None
    key_quotes: list
    cluster_id: uuid.UUID | None
    is_duplicate: bool
    duplicate_of: uuid.UUID | None

    model_config = {"from_attributes": True}
