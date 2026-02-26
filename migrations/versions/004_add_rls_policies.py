"""add row-level security policies

Revision ID: 004
Revises: 003
Create Date: 2026-02-26
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

# 15 user-scoped tables that get RLS.
# Auth tables (users, api_keys, refresh_tokens) and shared reference tables
# (ticker_reference, theme_etf_map, market_data_cache, price_history) do NOT get RLS.
USER_SCOPED_TABLES = [
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


def upgrade() -> None:
    # Enable RLS on all user-scoped tables
    for table in USER_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    # FORCE ensures RLS applies even if the session role matches the table owner.
    # Without FORCE, the table owner (postgres, used by Alembic) bypasses RLS.
    # While app/worker roles are not owners, FORCE is defense-in-depth.
    for table in USER_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # User isolation policies for ttwatch_app role.
    # RLS context: SET LOCAL ttwatch.current_user_id = '<uuid>';
    # FOR ALL covers SELECT, INSERT, UPDATE, DELETE.
    # USING filters existing rows (SELECT/UPDATE/DELETE).
    # WITH CHECK validates new/modified rows (INSERT/UPDATE).
    # Policies restricted TO ttwatch_app to prevent UUID cast errors
    # when ttwatch_worker runs without RLS context.
    for table in USER_SCOPED_TABLES:
        op.execute(
            f"CREATE POLICY user_isolation ON {table} FOR ALL TO ttwatch_app "
            f"USING (user_id = current_setting('ttwatch.current_user_id')::UUID) "
            f"WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID)"
        )

    # Worker bypass policies.
    # The worker role needs to query across ALL users for periodic dispatch tasks.
    # Without these policies, those tasks would crash because
    # current_setting('ttwatch.current_user_id') is unset.
    # These are separate, additive policies (PostgreSQL ORs multiple policies
    # for the same role).
    for table in USER_SCOPED_TABLES:
        op.execute(
            f"CREATE POLICY worker_bypass ON {table} FOR ALL TO ttwatch_worker "
            f"USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for table in reversed(USER_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS worker_bypass ON {table}")
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
