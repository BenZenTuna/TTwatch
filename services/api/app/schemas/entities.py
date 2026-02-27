import uuid
from datetime import datetime
from pydantic import BaseModel


class EntityResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    topic_id: uuid.UUID
    first_seen: datetime

    model_config = {"from_attributes": True}


class EntityNodeResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    article_count: int


class EntityEdgeResponse(BaseModel):
    source: uuid.UUID
    target: uuid.UUID
    weight: int


class EntityGraphResponse(BaseModel):
    entities: list[EntityNodeResponse]
    edges: list[EntityEdgeResponse]
