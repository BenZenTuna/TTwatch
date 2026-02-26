import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


# === Watchlist ===

class WatchlistItemCreate(BaseModel):
    symbol: str
    asset_type: str
    added_reason: str | None = None
    notes: str | None = None
    target_price: Decimal | None = None
    stop_loss: Decimal | None = None


class WatchlistItemResponse(BaseModel):
    id: uuid.UUID
    symbol: str
    asset_type: str
    added_reason: str | None
    topic_id: uuid.UUID | None
    notes: str | None
    target_price: Decimal | None
    stop_loss: Decimal | None
    created_at: datetime

    model_config = {"from_attributes": True}


# === Investment Analysis ===

class InvestmentAnalysisResponse(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    analysis_scope: str
    scope_ref_id: uuid.UUID | None
    symbol: str | None
    analysis_text: str
    recommendation: str | None
    confidence: float | None
    key_signals: list
    risk_factors: list
    articles_considered: int
    sentiment_score: float | None
    generated_at: datetime

    model_config = {"from_attributes": True}


# === Correlation Signal ===

class CorrelationSignalResponse(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    cluster_id: uuid.UUID | None
    symbol: str
    signal_type: str
    signal_strength: float | None
    description: str | None
    detected_at: datetime

    model_config = {"from_attributes": True}


# === Price Alert ===

class PriceAlertCreate(BaseModel):
    symbol: str
    condition: str
    threshold: Decimal


class PriceAlertResponse(BaseModel):
    id: uuid.UUID
    symbol: str
    condition: str
    threshold: Decimal
    last_known_price: Decimal | None
    is_active: bool
    triggered_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# === Market Data ===

class MarketDataResponse(BaseModel):
    id: uuid.UUID
    symbol: str
    asset_type: str
    price: Decimal | None
    price_change_pct: Decimal | None
    volume: int | None
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    eps: Decimal | None
    dividend_yield: Decimal | None
    beta: Decimal | None
    fifty_two_week_high: Decimal | None
    fifty_two_week_low: Decimal | None
    data_source: str | None
    is_stale: bool
    fetched_at: datetime

    model_config = {"from_attributes": True}


class PriceHistoryResponse(BaseModel):
    symbol: str
    trade_date: datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adj_close: Decimal | None
    volume: int | None

    model_config = {"from_attributes": True}
