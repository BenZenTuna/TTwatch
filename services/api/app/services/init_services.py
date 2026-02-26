"""One-time initialization for external services (Qdrant, MinIO)."""
import logging

from minio import Minio
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from app.config import settings

logger = logging.getLogger(__name__)


def init_qdrant():
    """Create the articles collection if it doesn't exist.

    If the collection already exists, validates that the vector dimension
    matches EMBEDDING_DIMENSION. Mismatches (e.g., switching from local
    BGE-M3 1024-dim to cloud OpenAI 3072-dim) would cause silent search
    failures — vectors would be stored but similarity scores would be
    meaningless across different dimensions.
    """
    client = QdrantClient(url=settings.QDRANT_URL, timeout=30)
    collections = [c.name for c in client.get_collections().collections]

    if "articles" not in collections:
        client.create_collection(
            collection_name="articles",
            vectors_config=VectorParams(
                size=settings.EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        client.create_payload_index("articles", "user_id", field_schema="keyword")
        client.create_payload_index("articles", "topic_id", field_schema="keyword")
        logger.info(
            f"Created Qdrant 'articles' collection (dim={settings.EMBEDDING_DIMENSION})"
        )
    else:
        # Validate dimension matches current configuration
        collection_info = client.get_collection("articles")
        existing_dim = collection_info.config.params.vectors.size
        if existing_dim != settings.EMBEDDING_DIMENSION:
            logger.error(
                f"DIMENSION MISMATCH: Qdrant 'articles' collection has dimension "
                f"{existing_dim} but EMBEDDING_DIMENSION is {settings.EMBEDDING_DIMENSION}. "
                f"This will cause incorrect search results. Either change "
                f"EMBEDDING_DIMENSION to {existing_dim} or delete and recreate "
                f"the collection (WARNING: deletes all vectors)."
            )
            raise RuntimeError(
                f"Qdrant dimension mismatch: collection={existing_dim}, "
                f"config={settings.EMBEDDING_DIMENSION}"
            )
        logger.info(
            f"Qdrant 'articles' collection verified (dim={existing_dim})"
        )


def init_minio():
    """Create the content bucket if it doesn't exist."""
    client = Minio(
        settings.MINIO_URL.replace("http://", "").replace("https://", ""),
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_URL.startswith("https"),
    )
    if not client.bucket_exists(settings.MINIO_BUCKET):
        client.make_bucket(settings.MINIO_BUCKET)
        logger.info(f"Created MinIO bucket '{settings.MINIO_BUCKET}'")
    else:
        logger.info(f"MinIO bucket '{settings.MINIO_BUCKET}' already exists")


def init_all():
    """Initialize all external services. Safe to call multiple times.

    Re-raises RuntimeError from dimension validation — this is a critical
    misconfiguration that must prevent startup. Other initialization failures
    are logged but don't prevent startup (services may become available later).
    """
    try:
        init_qdrant()
    except RuntimeError:
        # Dimension mismatch — MUST fail fast. Don't swallow this.
        raise
    except Exception as e:
        logger.error(f"Qdrant initialization failed: {e}")

    try:
        init_minio()
    except Exception as e:
        logger.error(f"MinIO initialization failed: {e}")
