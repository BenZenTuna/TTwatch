"""Lightweight Celery client for dispatching tasks from the API.

Uses send_task() to enqueue jobs without importing worker task modules.
Only the broker URL is needed — the worker handles task execution.
"""
from celery import Celery
from app.config import settings

celery_app = Celery("ttwatch", broker=settings.REDIS_URL)
celery_app.conf.result_backend = settings.CELERY_RESULT_BACKEND
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.task_default_queue = "ttwatch:default"

# CPU-bound tasks route to the compute queue (must match worker config)
celery_app.conf.task_routes = {
    "recluster_topic": {"queue": "ttwatch:compute"},
    "update_trends": {"queue": "ttwatch:compute"},
    "compute_sentiment_history": {"queue": "ttwatch:compute"},
    "detect_coverage_gaps": {"queue": "ttwatch:compute"},
    "generate_briefing": {"queue": "ttwatch:compute"},
    "generate_investment_analyses": {"queue": "ttwatch:compute"},
    "detect_correlation_signals": {"queue": "ttwatch:compute"},
}
