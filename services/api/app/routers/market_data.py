"""Market data endpoints (shared reference data, price history)."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, MarketDataCache, PriceHistory
from app.schemas.investment import MarketDataResponse, PriceHistoryResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/market-data/{symbol}", response_model=MarketDataResponse)
async def get_market_data(
    symbol: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest cached market data for a symbol."""
    result = await db.execute(
        select(MarketDataCache).where(
            MarketDataCache.symbol == symbol.upper(),
        ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
    )
    data = result.scalar_one_or_none()
    if not data:
        raise HTTPException(404, f"No market data found for symbol '{symbol}'")
    return data


@router.get("/market-data/{symbol}/history", response_model=list[PriceHistoryResponse])
async def get_price_history(
    symbol: str,
    limit: int = Query(default=90, le=365),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get OHLCV price history for a symbol."""
    result = await db.execute(
        select(PriceHistory).where(
            PriceHistory.symbol == symbol.upper(),
        ).order_by(PriceHistory.trade_date.desc()).limit(limit)
    )
    return result.scalars().all()
