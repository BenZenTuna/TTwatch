"""Shared utilities for worker tasks that need article content."""
import os
import logging

from minio import Minio

logger = logging.getLogger(__name__)

_minio = Minio(
    os.environ.get("MINIO_URL", "http://minio:9000")
    .replace("http://", "")
    .replace("https://", ""),
    access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
    secure=os.environ.get("MINIO_URL", "").startswith("https"),
)
_bucket = os.environ.get("MINIO_BUCKET", "ttwatch-content")


def fetch_article_text(raw_storage_key: str) -> str:
    """Fetch raw article text from MinIO by its storage key.

    Returns the full text string. Raises if key doesn't exist.
    Used by summarize, embed, extract_entities, classify_sentiment tasks.
    """
    response = _minio.get_object(_bucket, raw_storage_key)
    try:
        return response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()
