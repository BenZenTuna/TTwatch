"""Article management endpoints."""
from fastapi import APIRouter

router = APIRouter()

# TODO: GET /articles/{article_id} — get single article with full details
# TODO: GET /topics/{topic_id}/articles — list articles for a topic (paginated)
# TODO: DELETE /articles/{article_id} — delete article (Qdrant cleanup deferred to GC)
