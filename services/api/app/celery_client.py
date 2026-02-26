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
