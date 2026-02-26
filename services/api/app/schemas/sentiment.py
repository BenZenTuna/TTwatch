import uuid
from datetime import date
from pydantic import BaseModel


class SentimentPointResponse(BaseModel):
    cluster_id: uuid.UUID | None
    cluster_keyword: str | None
    period_start: date
    avg_sentiment: float | None
    article_count: int | None

    model_config = {"from_attributes": True}
