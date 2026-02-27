"""create llm_task_config table for per-user model routing

Revision ID: 007
Revises: 006
Create Date: 2026-02-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

TABLE = "llm_task_config"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_category", sa.Text(), nullable=False),
        sa.Column("model_target", sa.Text(), nullable=False,
                  server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "task_category",
                            name="uq_llm_task_config_user_category"),
    )

    # RLS
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY user_isolation ON {TABLE} FOR ALL TO ttwatch_app "
        f"USING (user_id = current_setting('ttwatch.current_user_id')::UUID) "
        f"WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID)"
    )
    op.execute(
        f"CREATE POLICY worker_bypass ON {TABLE} FOR ALL TO ttwatch_worker "
        f"USING (true) WITH CHECK (true)"
    )

    # Grants
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO ttwatch_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO ttwatch_worker")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS worker_bypass ON {TABLE}")
    op.execute(f"DROP POLICY IF EXISTS user_isolation ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(TABLE)
