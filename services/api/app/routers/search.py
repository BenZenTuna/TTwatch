"""Semantic search across articles using Qdrant vector similarity."""
import logging
from fastapi import APIRouter, Depends, Request
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from app.deps import get_current_user, get_db
from app.config import settings
from app.models import User, Article
from app.schemas.topics import SearchRequest, SearchResult, ArticleResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level async client — shared across all requests.
# NOT created per-request (avoids connection exhaustion under load).
_qdrant: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Lazy-initialize the async Qdrant client."""
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=settings.QDRANT_URL, timeout=30)
    return _qdrant


@router.post("/search", response_model=list[SearchResult])
async def semantic_search(
    req: SearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search articles by semantic similarity using the query embedding.

    1. Embeds the query text using the configured embedding provider.
    2. Searches Qdrant with user_id + topic_id filters (async, non-blocking).
    3. Fetches full article records from PostgreSQL for matching IDs.
    """
    embedder = request.app.state.embedder
    query_embedding = (await embedder.embed([req.query]))[0]

    qdrant = get_qdrant_client()
    results = await qdrant.search(
        collection_name="articles",
        query_vector=query_embedding,
        query_filter=Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=str(user.id))),
            FieldCondition(key="topic_id", match=MatchValue(value=str(req.topic_id))),
        ]),
        limit=req.limit,
    )

    if not results:
        return []

    # Fetch full article records from PostgreSQL
    article_ids = [hit.id for hit in results]
    score_map = {str(hit.id): hit.score for hit in results}

    articles = await db.execute(
        select(Article).where(Article.id.in_(article_ids))
    )
    article_map = {str(a.id): a for a in articles.scalars().all()}

    search_results = []
    for aid in article_ids:
        article = article_map.get(str(aid))
        if article:
            search_results.append(SearchResult(
                article=ArticleResponse.model_validate(article),
                score=score_map.get(str(aid), 0.0),
            ))

    return search_results
