"""Periodic dispatch tasks — scheduled by Celery Beat.

Each task enumerates active users/topics and dispatches per-user work tasks.
These run WITHOUT RLS context (they query across all users to discover work).
The worker role has bypass RLS policies for this purpose.
"""
import logging

from sqlalchemy import select

from worker.celeryconfig import app
from worker.db import db_session
from app.models import User, Topic, WatchlistItem, AssetMapping

logger = logging.getLogger(__name__)


@app.task(name="schedule_searches")
def schedule_searches():
    """Beat task: enumerate active users + topics, dispatch run_topic_search for each."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_searches: dispatching {len(pairs)} search jobs")
    for user_id, topic_id in pairs:
        app.send_task("run_topic_search", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_reclustering")
def schedule_reclustering():
    """Beat task: enumerate active users and dispatch per-user recluster jobs."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_reclustering: dispatching {len(pairs)} recluster jobs")
    for user_id, topic_id in pairs:
        app.send_task("recluster_topic", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_trend_updates")
def schedule_trend_updates():
    """Beat task: dispatch trend update for each active user/topic pair."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_trend_updates: dispatching {len(pairs)} trend jobs")
    for user_id, topic_id in pairs:
        app.send_task("update_trends", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_briefings")
def schedule_briefings():
    """Beat task: dispatch briefing generation for each active user/topic pair."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_briefings: dispatching {len(pairs)} briefing jobs")
    for user_id, topic_id in pairs:
        app.send_task("generate_briefing", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_coverage_gaps")
def schedule_coverage_gaps():
    """Beat task: dispatch coverage gap detection for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_coverage_gaps: dispatching {len(pairs)} gap detection jobs")
    for user_id, topic_id in pairs:
        app.send_task("detect_coverage_gaps", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_sentiment_history")
def schedule_sentiment_history():
    """Beat task: dispatch sentiment history computation for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_sentiment_history: dispatching {len(pairs)} sentiment jobs")
    for user_id, topic_id in pairs:
        app.send_task("compute_sentiment_history", args=[str(user_id), str(topic_id)])


@app.task(name="refresh_market_data")
def refresh_market_data():
    """Beat task: refresh market data for all watched symbols across all users.

    Discovers symbols from BOTH watchlist_items (user-explicit) and
    asset_mappings (auto-resolved from entities). This ensures market data
    is available for generate_investment_analyses and detect_correlation_signals,
    not just for user-managed watchlists.
    """
    with db_session() as session:
        watchlist_symbols = set(
            session.execute(
                select(WatchlistItem.symbol).distinct()
            ).scalars().all()
        )
        mapping_symbols = set(
            session.execute(
                select(AssetMapping.resolved_symbol).where(
                    AssetMapping.resolved_symbol.isnot(None)
                ).distinct()
            ).scalars().all()
        )
        all_symbols = watchlist_symbols | mapping_symbols
    logger.info(f"refresh_market_data: dispatching {len(all_symbols)} symbol fetches")
    for symbol in all_symbols:
        app.send_task("fetch_market_data", args=[symbol])


@app.task(name="schedule_investment_analyses")
def schedule_investment_analyses():
    """Beat task (daily): dispatch investment analysis for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_investment_analyses: dispatching {len(pairs)} analysis jobs")
    for user_id, topic_id in pairs:
        app.send_task("generate_investment_analyses", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_correlation_signals")
def schedule_correlation_signals():
    """Beat task (every 4h): dispatch correlation signal detection for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    logger.info(f"schedule_correlation_signals: dispatching {len(pairs)} correlation jobs")
    for user_id, topic_id in pairs:
        app.send_task("detect_correlation_signals", args=[str(user_id), str(topic_id)])
