import uuid
from datetime import datetime
from pydantic import BaseModel


class SavedQueryCreate(BaseModel):
    query_text: str
    schedule: str = "on_refresh"


class SavedQueryResponse(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    query_text: str
    schedule: str
    last_run: datetime | None
    last_result_count: int
    created_at: datetime

    model_config = {"from_attributes": True}
