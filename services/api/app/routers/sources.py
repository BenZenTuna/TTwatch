"""Source management endpoints (RSS feeds, web sources)."""
from fastapi import APIRouter

router = APIRouter()

# TODO: GET /topics/{topic_id}/sources — list sources for a topic
# TODO: POST /topics/{topic_id}/sources — add source to topic
# TODO: PUT /sources/{source_id} — update source (enable/disable, config)
# TODO: DELETE /sources/{source_id} — remove source from topic
