"""Investment analysis, watchlist, correlation signals, and price alert endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db, cache_redis
from app.models import (
    User, Topic, Entity, WatchlistItem, InvestmentAnalysis,
    CorrelationSignal, PriceAlert, AssetMapping, MarketDataCache,
)
from app.schemas.investment import (
    WatchlistItemCreate, WatchlistItemResponse,
    InvestmentAnalysisResponse,
    CorrelationSignalResponse,
    PriceAlertCreate, PriceAlertResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# === Watchlist ===

@router.get("/topics/{topic_id}/watchlist", response_model=list[WatchlistItemResponse])
async def list_watchlist(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List watchlist items for a topic."""
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.topic_id == topic_id,
        ).order_by(WatchlistItem.created_at.desc())
    )
    return result.scalars().all()


@router.post("/topics/{topic_id}/watchlist", response_model=WatchlistItemResponse, status_code=201)
async def add_watchlist_item(
    topic_id: uuid.UUID,
    req: WatchlistItemCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an item to the watchlist for a topic."""
    # Check for duplicate
    existing = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.symbol == req.symbol,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Symbol '{req.symbol}' already in watchlist")

    item = WatchlistItem(
        user_id=user.id,
        topic_id=topic_id,
        symbol=req.symbol,
        asset_type=req.asset_type,
        added_reason=req.added_reason,
        notes=req.notes,
        target_price=req.target_price,
        stop_loss=req.stop_loss,
    )
    db.add(item)
    await db.flush()
    return item


@router.delete("/watchlist/{item_id}", status_code=204)
async def remove_watchlist_item(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an item from the watchlist."""
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.id == item_id,
            WatchlistItem.user_id == user.id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Watchlist item not found")
    await db.delete(item)


# === Investment Analyses ===

@router.get("/topics/{topic_id}/analyses", response_model=list[InvestmentAnalysisResponse])
async def list_analyses(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List investment analyses for a topic."""
    result = await db.execute(
        select(InvestmentAnalysis).where(
            InvestmentAnalysis.user_id == user.id,
            InvestmentAnalysis.topic_id == topic_id,
        ).order_by(InvestmentAnalysis.generated_at.desc())
    )
    return result.scalars().all()


# === Correlation Signals ===

@router.get("/topics/{topic_id}/correlation-signals", response_model=list[CorrelationSignalResponse])
async def list_correlation_signals(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent correlation signals for a topic."""
    result = await db.execute(
        select(CorrelationSignal).where(
            CorrelationSignal.user_id == user.id,
            CorrelationSignal.topic_id == topic_id,
        ).order_by(CorrelationSignal.detected_at.desc()).limit(50)
    )
    return result.scalars().all()


# === Price Alerts ===

@router.post("/price-alerts", response_model=PriceAlertResponse, status_code=201)
async def create_price_alert(
    req: PriceAlertCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new price alert."""
    if req.condition not in ("above", "below", "crosses_above", "crosses_below"):
        raise HTTPException(400, "Invalid condition. Must be: above, below, crosses_above, crosses_below")

    alert = PriceAlert(
        user_id=user.id,
        symbol=req.symbol,
        condition=req.condition,
        threshold=req.threshold,
    )
    db.add(alert)
    await db.flush()
    return alert


@router.get("/price-alerts", response_model=list[PriceAlertResponse])
async def list_price_alerts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all active price alerts for the current user."""
    result = await db.execute(
        select(PriceAlert).where(
            PriceAlert.user_id == user.id,
            PriceAlert.is_active == True,
        ).order_by(PriceAlert.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/price-alerts/{alert_id}", status_code=204)
async def delete_price_alert(
    alert_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a price alert."""
    result = await db.execute(
        select(PriceAlert).where(
            PriceAlert.id == alert_id,
            PriceAlert.user_id == user.id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Price alert not found")
    await db.delete(alert)


# === Investment Pipeline Status & Trigger ===

RESOLVABLE_ENTITY_TYPES = ("org", "product", "technology")


@router.get("/topics/{topic_id}/investment/status")
async def investment_pipeline_status(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Diagnostic view of the investment pipeline for a topic."""
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    if not topic.scalar_one_or_none():
        raise HTTPException(404, "Topic not found")

    # 1. Entities by type
    rows = (await db.execute(
        select(Entity.type, func.count(Entity.id))
        .where(Entity.topic_id == topic_id, Entity.user_id == user.id)
        .group_by(Entity.type)
    )).all()
    entities_by_type = {r[0]: r[1] for r in rows}
    total_entities = sum(entities_by_type.values())
    resolvable_count = sum(entities_by_type.get(t, 0) for t in RESOLVABLE_ENTITY_TYPES)

    # 2. Asset mappings + resolved symbols
    mapping_rows = (await db.execute(
        select(AssetMapping.resolved_symbol)
        .where(AssetMapping.topic_id == topic_id, AssetMapping.user_id == user.id)
    )).all()
    mapping_count = len(mapping_rows)
    resolved_symbols = sorted({r[0] for r in mapping_rows if r[0]})

    # 3. Market data count
    market_data_count = 0
    if resolved_symbols:
        market_data_count = (await db.execute(
            select(func.count(MarketDataCache.id))
            .where(MarketDataCache.symbol.in_(resolved_symbols))
        )).scalar_one()

    # 4. Analyses count
    analysis_count = (await db.execute(
        select(func.count(InvestmentAnalysis.id))
        .where(InvestmentAnalysis.topic_id == topic_id, InvestmentAnalysis.user_id == user.id)
    )).scalar_one()

    # 5. Correlation signals count
    signal_count = (await db.execute(
        select(func.count(CorrelationSignal.id))
        .where(CorrelationSignal.topic_id == topic_id, CorrelationSignal.user_id == user.id)
    )).scalar_one()

    # Build pipeline steps
    pipeline_steps = [
        {
            "step": "entity_extraction",
            "status": "ok" if total_entities > 0 else "missing",
            "detail": f"{total_entities} entities ({resolvable_count} resolvable)",
        },
        {
            "step": "ticker_resolution",
            "status": "ok" if mapping_count > 0 else "missing",
            "detail": f"{mapping_count} mappings, {len(resolved_symbols)} symbols",
        },
        {
            "step": "market_data",
            "status": "ok" if market_data_count > 0 else "missing",
            "detail": f"{market_data_count} cached prices",
        },
        {
            "step": "analyses",
            "status": "ok" if analysis_count > 0 else "missing",
            "detail": f"{analysis_count} analyses",
        },
        {
            "step": "correlation_signals",
            "status": "ok" if signal_count > 0 else "missing",
            "detail": f"{signal_count} signals",
        },
    ]

    return {
        "topic_id": str(topic_id),
        "entities_by_type": entities_by_type,
        "resolved_symbols": resolved_symbols,
        "pipeline_steps": pipeline_steps,
        "ready": all(s["status"] == "ok" for s in pipeline_steps),
    }


@router.post("/topics/{topic_id}/investment/analyze", status_code=202)
async def trigger_investment_analysis(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the full investment analysis pipeline for a topic."""
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    if not topic.scalar_one_or_none():
        raise HTTPException(404, "Topic not found")

    # Rate limit: 5 minute cooldown
    lock_key = f"ttwatch:investment_lock:{topic_id}"
    if await cache_redis.exists(lock_key):
        return JSONResponse(
            status_code=429,
            content={"detail": "Investment analysis recently triggered. Please wait 5 minutes."},
        )
    await cache_redis.setex(lock_key, 300, "1")

    from app.celery_client import celery_app

    # 1. Find unresolved entities (org/product/technology not yet in asset_mappings)
    resolved_entity_ids = select(AssetMapping.entity_id).where(
        AssetMapping.topic_id == topic_id,
        AssetMapping.user_id == user.id,
    )
    unresolved = (await db.execute(
        select(Entity).where(
            Entity.topic_id == topic_id,
            Entity.user_id == user.id,
            Entity.type.in_(RESOLVABLE_ENTITY_TYPES),
            Entity.id.notin_(resolved_entity_ids),
        )
    )).scalars().all()

    ticker_tasks = 0
    for entity in unresolved:
        celery_app.send_task(
            "resolve_entity_ticker",
            args=[str(user.id), str(entity.id), str(topic_id)],
        )
        ticker_tasks += 1

    # 2. Gather already-resolved symbols from asset_mappings + watchlist_items
    mapping_symbols = (await db.execute(
        select(AssetMapping.resolved_symbol).where(
            AssetMapping.topic_id == topic_id,
            AssetMapping.user_id == user.id,
            AssetMapping.resolved_symbol.isnot(None),
        )
    )).scalars().all()

    watchlist_symbols = (await db.execute(
        select(WatchlistItem.symbol).where(
            WatchlistItem.topic_id == topic_id,
            WatchlistItem.user_id == user.id,
        )
    )).scalars().all()

    all_symbols = sorted(set(mapping_symbols) | set(watchlist_symbols))

    market_tasks = 0
    for symbol in all_symbols:
        celery_app.send_task("fetch_market_data", args=[symbol])
        market_tasks += 1

    # 3. Dispatch analysis + correlation with countdown to allow market data to arrive
    analysis_task = celery_app.send_task(
        "generate_investment_analyses",
        args=[str(user.id), str(topic_id)],
        countdown=30,
    )
    correlation_task = celery_app.send_task(
        "detect_correlation_signals",
        args=[str(user.id), str(topic_id)],
        countdown=30,
    )

    return {
        "status": "dispatched",
        "topic_id": str(topic_id),
        "ticker_resolution_tasks": ticker_tasks,
        "market_data_tasks": market_tasks,
        "symbols": all_symbols,
        "unresolved_entities": len(unresolved),
        "analysis_task_id": analysis_task.id,
        "correlation_task_id": correlation_task.id,
        "note": f"Resolving {ticker_tasks} entities, fetching {market_tasks} symbols. "
                f"Analyses will generate in ~30s.",
    }
