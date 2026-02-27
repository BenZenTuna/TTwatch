import os

from celery import Celery
from celery.schedules import crontab

app = Celery("ttwatch")

app.conf.broker_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
app.conf.result_backend = os.environ.get(
    "CELERY_RESULT_BACKEND", "redis://redis:6379/1"
)
app.conf.result_expires = 3600

# Task routing: CPU-bound tasks to compute queue, everything else to default
app.conf.task_routes = {
    "recluster_topic": {"queue": "ttwatch:compute"},
    "update_trends": {"queue": "ttwatch:compute"},
    "compute_sentiment_history": {"queue": "ttwatch:compute"},
    "detect_coverage_gaps": {"queue": "ttwatch:compute"},
    "generate_briefing": {"queue": "ttwatch:compute"},
    "generate_investment_analyses": {"queue": "ttwatch:compute"},
    "detect_correlation_signals": {"queue": "ttwatch:compute"},
}

app.conf.task_default_queue = "ttwatch:default"

# Task serialization
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]

# Task discovery — explicitly include all task modules
app.conf.include = [
    "worker.tasks.briefing",
    "worker.tasks.cluster",
    "worker.tasks.correlation_signals",
    "worker.tasks.coverage_gaps",
    "worker.tasks.embed",
    "worker.tasks.entities",
    "worker.tasks.ingest",
    "worker.tasks.investment_analysis",
    "worker.tasks.maintenance",
    "worker.tasks.periodic",
    "worker.tasks.price_alerts",
    "worker.tasks.relevance",
    "worker.tasks.resolve_ticker",
    "worker.tasks.search",
    "worker.tasks.search_plan",
    "worker.tasks.sentiment",
    "worker.tasks.sentiment_agg",
    "worker.tasks.summarize",
    "worker.tasks.trends",
    "worker.tasks.version_check",
]

# Beat schedule — all periodic tasks
app.conf.beat_schedule = {
    "schedule-searches": {
        "task": "schedule_searches",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 120 min
    },
    "schedule-reclustering": {
        "task": "schedule_reclustering",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 120 min
    },
    "schedule-trend-updates": {
        "task": "schedule_trend_updates",
        "schedule": crontab(minute=0),  # Every 60 min
    },
    "schedule-briefings": {
        "task": "schedule_briefings",
        "schedule": crontab(minute=0, hour="*/6"),  # Every 360 min
    },
    "schedule-coverage-gaps": {
        "task": "schedule_coverage_gaps",
        "schedule": crontab(minute=0, hour="*/12"),  # Every 720 min
    },
    "schedule-sentiment-history": {
        "task": "schedule_sentiment_history",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 120 min
    },
    "refresh-market-data": {
        "task": "refresh_market_data",
        "schedule": crontab(minute="*/30"),  # Every 30 min
    },
    "schedule-investment-analyses": {
        "task": "schedule_investment_analyses",
        "schedule": crontab(hour=6, minute=0),  # Daily at 6:00 AM
    },
    "schedule-correlation-signals": {
        "task": "schedule_correlation_signals",
        "schedule": crontab(minute=0, hour="*/4"),  # Every 240 min
    },
    "check-price-alerts": {
        "task": "check_price_alerts",
        "schedule": crontab(minute="*/15"),  # Every 15 min
    },
    "cleanup-stale-market-data": {
        "task": "cleanup_stale_market_data",
        "schedule": crontab(hour=3, minute=0),  # Daily at 3:00 AM
    },
    "cleanup-stale-snapshots": {
        "task": "cleanup_stale_snapshots",
        "schedule": crontab(hour=3, minute=30),  # Daily at 3:30 AM
    },
    "cleanup-expired-refresh-tokens": {
        "task": "cleanup_expired_refresh_tokens",
        "schedule": crontab(hour=2, minute=30),  # Daily at 2:30 AM
    },
    "cleanup-orphaned-qdrant": {
        "task": "cleanup_orphaned_qdrant_points",
        "schedule": crontab(hour=4, minute=0),  # Daily at 4:00 AM
    },
    "check-service-versions": {
        "task": "check_service_versions",
        "schedule": crontab(hour=6, minute=30),  # Daily at 6:30 AM
    },
}
