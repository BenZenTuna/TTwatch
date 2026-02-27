import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Boolean, Integer, Float, Text, Date,
    DateTime, ForeignKey, BigInteger, Numeric, CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base import Base


class TickerReference(Base):
    """Shared reference table — no user_id, no RLS."""
    __tablename__ = "ticker_reference"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    exchange = Column(Text)
    asset_type = Column(Text, nullable=False)
    sector = Column(Text)
    industry = Column(Text)
    market_cap_tier = Column(Text)
    is_active = Column(Boolean, default=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("symbol", "exchange"),)


class ThemeEtfMap(Base):
    """Shared reference table — no user_id, no RLS."""
    __tablename__ = "theme_etf_map"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    theme = Column(Text, nullable=False)
    etf_symbol = Column(Text, nullable=False)
    relevance_score = Column(Float, default=1.0)

    __table_args__ = (UniqueConstraint("theme", "etf_symbol"),)


class MarketDataCache(Base):
    """Shared cache — no user_id, no RLS. Writes restricted to worker role."""
    __tablename__ = "market_data_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(Text, nullable=False)
    asset_type = Column(Text, nullable=False)
    price = Column(Numeric)
    price_change_pct = Column(Numeric)
    volume = Column(BigInteger)
    market_cap = Column(Numeric)
    pe_ratio = Column(Numeric)
    eps = Column(Numeric)
    dividend_yield = Column(Numeric)
    beta = Column(Numeric)
    fifty_two_week_high = Column(Numeric)
    fifty_two_week_low = Column(Numeric)
    data_source = Column(Text)
    is_stale = Column(Boolean, default=False)
    fetched_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PriceHistory(Base):
    """Shared OHLCV cache — no user_id, no RLS."""
    __tablename__ = "price_history"

    symbol = Column(Text, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open = Column(Numeric)
    high = Column(Numeric)
    low = Column(Numeric)
    close = Column(Numeric)
    adj_close = Column(Numeric)
    volume = Column(BigInteger)
    source = Column(Text, default="yfinance")


class AssetMapping(Base):
    __tablename__ = "asset_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    ticker_ref_id = Column(UUID(as_uuid=True), ForeignKey("ticker_reference.id"))
    entity_name = Column(Text, nullable=False)
    resolved_symbol = Column(Text)
    resolution_method = Column(Text)
    confidence = Column(Float, default=0)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "entity_id", "resolved_symbol"),)


class InvestmentAnalysis(Base):
    __tablename__ = "investment_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    analysis_scope = Column(Text, nullable=False)
    scope_ref_id = Column(UUID(as_uuid=True))
    symbol = Column(Text)
    analysis_text = Column(Text, nullable=False)
    recommendation = Column(Text)
    confidence = Column(Float)
    key_signals = Column(JSONB, default=list)
    risk_factors = Column(JSONB, default=list)
    articles_considered = Column(Integer, default=0)
    market_data_cache_id = Column(UUID(as_uuid=True), ForeignKey("market_data_cache.id", ondelete="SET NULL"))
    sentiment_score = Column(Float)
    technical_signals = Column(JSONB, default=dict)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    model_used = Column(Text)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    analysis_frequency = Column(Text, default="daily")
    next_analysis_at = Column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("analysis_scope IN ('asset', 'cluster', 'topic')"),)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(Text, nullable=False)
    asset_type = Column(Text, nullable=False)
    added_reason = Column(Text)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL"))
    notes = Column(Text)
    target_price = Column(Numeric)
    stop_loss = Column(Numeric)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "symbol"),)


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(Text, nullable=False)
    condition = Column(Text, nullable=False)
    threshold = Column(Numeric, nullable=False)
    last_known_price = Column(Numeric)
    is_active = Column(Boolean, default=True)
    triggered_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (CheckConstraint("condition IN ('above', 'below', 'crosses_above', 'crosses_below')"),)


class CorrelationSignal(Base):
    __tablename__ = "correlation_signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="SET NULL"))
    symbol = Column(Text, nullable=False)
    signal_type = Column(Text, nullable=False)
    signal_strength = Column(Float)
    description = Column(Text)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
