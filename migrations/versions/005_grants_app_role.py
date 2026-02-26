"""grant privileges to ttwatch_app role

Revision ID: 005
Revises: 004
Create Date: 2026-02-26
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

# User-scoped tables: full CRUD for app role
USER_SCOPED_TABLES = [
    "users",
    "api_keys",
    "refresh_tokens",
    "topics",
    "sources",
    "articles",
    "clusters",
    "entities",
    "entity_article_map",
    "entity_cluster_map",
    "sentiment_history",
    "saved_queries",
    "briefings",
    "asset_mappings",
    "investment_analyses",
    "watchlist_items",
    "price_alerts",
    "correlation_signals",
]

# Shared reference tables: READ-ONLY for app role
SHARED_TABLES = [
    "ticker_reference",
    "theme_etf_map",
    "market_data_cache",
    "price_history",
]


def upgrade() -> None:
    # Full CRUD on user-scoped tables (includes auth tables)
    tables_list = ", ".join(USER_SCOPED_TABLES)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables_list} TO ttwatch_app")

    # READ-ONLY on shared reference tables
    shared_list = ", ".join(SHARED_TABLES)
    op.execute(f"GRANT SELECT ON {shared_list} TO ttwatch_app")

    # Sequence access for auto-increment columns (e.g. sentiment_history.id)
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ttwatch_app")


def downgrade() -> None:
    tables_list = ", ".join(USER_SCOPED_TABLES)
    shared_list = ", ".join(SHARED_TABLES)
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {tables_list} FROM ttwatch_app")
    op.execute(f"REVOKE SELECT ON {shared_list} FROM ttwatch_app")
    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM ttwatch_app")
