"""Investment analysis, watchlist, correlation signals, and price alert endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import (
    User, WatchlistItem, InvestmentAnalysis,
    CorrelationSignal, PriceAlert,
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
