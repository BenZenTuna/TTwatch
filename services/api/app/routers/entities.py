"""Named entity endpoints."""
from fastapi import APIRouter

router = APIRouter()

# TODO: GET /topics/{topic_id}/entities — list entities for a topic
# TODO: GET /entities/{entity_id} — get entity with linked articles/clusters
# TODO: GET /entities/{entity_id}/articles — list articles mentioning entity
