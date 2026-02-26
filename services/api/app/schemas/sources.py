import uuid
from pydantic import BaseModel


class SourceCreate(BaseModel):
    name: str
    url: str
    source_type: str = "rss"
    enabled: bool = True
    config: dict = {}


class SourceUpdate(BaseModel):
    """All fields optional for partial updates."""
    name: str | None = None
    enabled: bool | None = None
    config: dict | None = None


class SourceResponse(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    name: str
    url: str
    source_type: str
    enabled: bool
    is_builtin: bool
    config: dict

    model_config = {"from_attributes": True}
