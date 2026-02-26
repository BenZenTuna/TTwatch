"""create investment tables

Revision ID: 003
Revises: 002
Create Date: 2026-02-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================================
    # SHARED REFERENCE TABLES (no user_id, no RLS)
    # ============================================================

    # --- ticker_reference ---
    op.create_table(
        "ticker_reference",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("exchange", sa.Text),
        sa.Column("asset_type", sa.Text, nullable=False),
        sa.Column("sector", sa.Text),
        sa.Column("industry", sa.Text),
        sa.Column("market_cap_tier", sa.Text),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("symbol", "exchange"),
    )

    # --- theme_etf_map ---
    op.create_table(
        "theme_etf_map",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("theme", sa.Text, nullable=False),
        sa.Column("etf_symbol", sa.Text, nullable=False),
        sa.Column("relevance_score", sa.Float, server_default=sa.text("1.0")),
        sa.UniqueConstraint("theme", "etf_symbol"),
    )

    # --- market_data_cache ---
    op.create_table(
        "market_data_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("asset_type", sa.Text, nullable=False),
        sa.Column("price", sa.Numeric),
        sa.Column("price_change_pct", sa.Numeric),
        sa.Column("volume", sa.BigInteger),
        sa.Column("market_cap", sa.Numeric),
        sa.Column("pe_ratio", sa.Numeric),
        sa.Column("eps", sa.Numeric),
        sa.Column("dividend_yield", sa.Numeric),
        sa.Column("beta", sa.Numeric),
        sa.Column("fifty_two_week_high", sa.Numeric),
        sa.Column("fifty_two_week_low", sa.Numeric),
        sa.Column("data_source", sa.Text),
        sa.Column("is_stale", sa.Boolean, server_default=sa.text("false")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- price_history (composite PK) ---
    op.create_table(
        "price_history",
        sa.Column("symbol", sa.Text, primary_key=True),
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("open", sa.Numeric),
        sa.Column("high", sa.Numeric),
        sa.Column("low", sa.Numeric),
        sa.Column("close", sa.Numeric),
        sa.Column("adj_close", sa.Numeric),
        sa.Column("volume", sa.BigInteger),
        sa.Column("source", sa.Text, server_default=sa.text("'yfinance'")),
    )

    # ============================================================
    # USER-SCOPED INVESTMENT TABLES
    # ============================================================

    # --- asset_mappings ---
    op.create_table(
        "asset_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker_ref_id", UUID(as_uuid=True), sa.ForeignKey("ticker_reference.id")),
        sa.Column("entity_name", sa.Text, nullable=False),
        sa.Column("resolved_symbol", sa.Text),
        sa.Column("resolution_method", sa.Text),
        sa.Column("confidence", sa.Float, server_default=sa.text("0")),
        sa.Column("is_verified", sa.Boolean, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "entity_id", "resolved_symbol"),
    )

    # --- investment_analyses ---
    op.create_table(
        "investment_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_scope", sa.Text, nullable=False),
        sa.Column("scope_ref_id", UUID(as_uuid=True)),
        sa.Column("symbol", sa.Text),
        sa.Column("analysis_text", sa.Text, nullable=False),
        sa.Column("recommendation", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("key_signals", JSONB, server_default=sa.text("'[]'")),
        sa.Column("risk_factors", JSONB, server_default=sa.text("'[]'")),
        sa.Column("articles_considered", sa.Integer, server_default=sa.text("0")),
        sa.Column("market_data_cache_id", UUID(as_uuid=True), sa.ForeignKey("market_data_cache.id", ondelete="SET NULL")),
        sa.Column("sentiment_score", sa.Float),
        sa.Column("technical_signals", JSONB, server_default=sa.text("'{}'")),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("model_used", sa.Text),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("analysis_frequency", sa.Text, server_default=sa.text("'daily'")),
        sa.Column("next_analysis_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("analysis_scope IN ('asset', 'cluster', 'topic')"),
    )

    # --- watchlist_items ---
    op.create_table(
        "watchlist_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("asset_type", sa.Text, nullable=False),
        sa.Column("added_reason", sa.Text),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text),
        sa.Column("target_price", sa.Numeric),
        sa.Column("stop_loss", sa.Numeric),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "symbol"),
    )

    # --- price_alerts ---
    op.create_table(
        "price_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("condition", sa.Text, nullable=False),
        sa.Column("threshold", sa.Numeric, nullable=False),
        sa.Column("last_known_price", sa.Numeric),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("triggered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("condition IN ('above', 'below', 'crosses_above', 'crosses_below')"),
    )

    # --- correlation_signals ---
    op.create_table(
        "correlation_signals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="SET NULL")),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("signal_type", sa.Text, nullable=False),
        sa.Column("signal_strength", sa.Float),
        sa.Column("description", sa.Text),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ============================================================
    # SHARED REFERENCE INDEXES
    # ============================================================
    op.create_index("idx_ticker_ref_symbol", "ticker_reference", ["symbol"])
    op.create_index("idx_ticker_ref_type", "ticker_reference", ["asset_type"])
    op.create_index(
        "idx_ticker_ref_sector", "ticker_reference", ["sector"],
        postgresql_where=sa.text("sector IS NOT NULL"),
    )
    op.create_index("idx_price_history_recent", "price_history", ["symbol", sa.text("trade_date DESC")])
    op.create_index("idx_market_data_cache_symbol", "market_data_cache", ["symbol", sa.text("fetched_at DESC")])

    # Deduplicate market data fetches: one snapshot per symbol per hour
    # Cast to timestamp (without tz) so date_trunc is IMMUTABLE
    op.execute(
        "CREATE UNIQUE INDEX idx_market_data_cache_dedup "
        "ON market_data_cache (symbol, date_trunc('hour', fetched_at AT TIME ZONE 'UTC'))"
    )

    # ============================================================
    # INVESTMENT INDEXES
    # ============================================================
    op.create_index("idx_asset_mappings_user_topic", "asset_mappings", ["user_id", "topic_id"])
    op.create_index(
        "idx_asset_mappings_symbol", "asset_mappings", ["user_id", "resolved_symbol"],
        postgresql_where=sa.text("resolved_symbol IS NOT NULL"),
    )
    op.create_index("idx_investment_analyses_user_topic", "investment_analyses", ["user_id", "topic_id", sa.text("generated_at DESC")])
    op.create_index("idx_investment_analyses_scope", "investment_analyses", ["user_id", "analysis_scope", "scope_ref_id"])
    op.create_index("idx_watchlist_user", "watchlist_items", ["user_id"])
    op.create_index(
        "idx_price_alerts_active", "price_alerts", ["user_id", "symbol"],
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("idx_correlation_signals_user", "correlation_signals", ["user_id", "topic_id", sa.text("detected_at DESC")])


def downgrade() -> None:
    # Investment indexes
    op.drop_index("idx_correlation_signals_user", table_name="correlation_signals")
    op.drop_index("idx_price_alerts_active", table_name="price_alerts")
    op.drop_index("idx_watchlist_user", table_name="watchlist_items")
    op.drop_index("idx_investment_analyses_scope", table_name="investment_analyses")
    op.drop_index("idx_investment_analyses_user_topic", table_name="investment_analyses")
    op.drop_index("idx_asset_mappings_symbol", table_name="asset_mappings")
    op.drop_index("idx_asset_mappings_user_topic", table_name="asset_mappings")

    # Shared reference indexes
    op.drop_index("idx_market_data_cache_dedup", table_name="market_data_cache")
    op.drop_index("idx_market_data_cache_symbol", table_name="market_data_cache")
    op.drop_index("idx_price_history_recent", table_name="price_history")
    op.drop_index("idx_ticker_ref_sector", table_name="ticker_reference")
    op.drop_index("idx_ticker_ref_type", table_name="ticker_reference")
    op.drop_index("idx_ticker_ref_symbol", table_name="ticker_reference")

    # Tables
    op.drop_table("correlation_signals")
    op.drop_table("price_alerts")
    op.drop_table("watchlist_items")
    op.drop_table("investment_analyses")
    op.drop_table("asset_mappings")
    op.drop_table("price_history")
    op.drop_table("market_data_cache")
    op.drop_table("theme_etf_map")
    op.drop_table("ticker_reference")
