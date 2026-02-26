"""create intelligence tables

Revision ID: 002
Revises: 001
Create Date: 2026-02-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- topics ---
    op.create_table(
        "topics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("icon", sa.Text),
        sa.Column("config", JSONB, server_default=sa.text("'{}'")),
        sa.Column("refresh_interval_minutes", sa.Integer, server_default=sa.text("120")),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True)),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name"),
    )

    # --- sources ---
    op.create_table(
        "sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("source_type", sa.Text, server_default=sa.text("'rss'")),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("true")),
        sa.Column("is_builtin", sa.Boolean, server_default=sa.text("false")),
        sa.Column("config", JSONB, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("user_id", "topic_id", "url"),
    )

    # --- clusters ---
    op.create_table(
        "clusters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword", sa.Text, nullable=False),
        sa.Column("color", sa.Text),
        sa.Column("article_count", sa.Integer, server_default=sa.text("0")),
        sa.Column("trend_score", sa.Float, server_default=sa.text("0")),
        sa.Column("velocity", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- articles ---
    op.create_table(
        "articles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("source_name", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("content_hash", sa.Text),
        sa.Column("raw_storage_key", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("sentiment_score", sa.Float),
        sa.Column("relevance_score", sa.Float),
        sa.Column("key_quotes", JSONB, server_default=sa.text("'[]'")),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="SET NULL")),
        sa.Column("embedding_id", sa.Text),
        sa.Column("is_duplicate", sa.Boolean, server_default=sa.text("false")),
        sa.Column("duplicate_of", UUID(as_uuid=True), sa.ForeignKey("articles.id")),
        sa.UniqueConstraint("user_id", "topic_id", "url"),
    )

    # --- entities ---
    op.create_table(
        "entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name", "type", "topic_id"),
    )

    # --- entity_article_map ---
    op.create_table(
        "entity_article_map",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )

    # --- entity_cluster_map ---
    op.create_table(
        "entity_cluster_map",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )

    # --- sentiment_history ---
    op.create_table(
        "sentiment_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="SET NULL")),
        sa.Column("cluster_keyword", sa.Text),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("avg_sentiment", sa.Float),
        sa.Column("article_count", sa.Integer),
        sa.UniqueConstraint("user_id", "cluster_id", "period_start"),
    )

    # --- saved_queries ---
    op.create_table(
        "saved_queries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("schedule", sa.Text, server_default=sa.text("'on_refresh'")),
        sa.Column("last_run", sa.DateTime(timezone=True)),
        sa.Column("last_result_count", sa.Integer, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- briefings ---
    op.create_table(
        "briefings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("summary", sa.Text),
        sa.Column("highlights", JSONB, server_default=sa.text("'[]'")),
        sa.Column("new_entities", JSONB, server_default=sa.text("'[]'")),
        sa.Column("watch_items", JSONB, server_default=sa.text("'[]'")),
        sa.Column("coverage_gaps", JSONB, server_default=sa.text("'[]'")),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("model_used", sa.Text),
    )

    # --- intelligence indexes ---
    op.create_index("idx_topics_user", "topics", ["user_id"])
    op.create_index("idx_sources_user_topic", "sources", ["user_id", "topic_id"])
    op.create_index("idx_articles_user_topic", "articles", ["user_id", "topic_id", sa.text("ingested_at DESC")])
    op.create_index("idx_articles_user_cluster", "articles", ["user_id", "cluster_id"])
    op.create_index("idx_articles_user_hash", "articles", ["user_id", "content_hash"])
    op.create_index("idx_clusters_user_topic", "clusters", ["user_id", "topic_id"])
    op.create_index("idx_entities_user_topic", "entities", ["user_id", "topic_id", "type"])
    op.create_index("idx_entity_article_map_user", "entity_article_map", ["user_id"])
    op.create_index("idx_entity_cluster_map_user", "entity_cluster_map", ["user_id"])
    op.create_index("idx_sentiment_user_cluster", "sentiment_history", ["user_id", "cluster_id", "period_start"])
    op.create_index("idx_sentiment_user_topic", "sentiment_history", ["user_id", "topic_id", "period_start"])
    op.create_index("idx_queries_user_topic", "saved_queries", ["user_id", "topic_id"])
    op.create_index("idx_briefings_user_topic", "briefings", ["user_id", "topic_id", sa.text("generated_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_briefings_user_topic", table_name="briefings")
    op.drop_index("idx_queries_user_topic", table_name="saved_queries")
    op.drop_index("idx_sentiment_user_topic", table_name="sentiment_history")
    op.drop_index("idx_sentiment_user_cluster", table_name="sentiment_history")
    op.drop_index("idx_entity_cluster_map_user", table_name="entity_cluster_map")
    op.drop_index("idx_entity_article_map_user", table_name="entity_article_map")
    op.drop_index("idx_entities_user_topic", table_name="entities")
    op.drop_index("idx_clusters_user_topic", table_name="clusters")
    op.drop_index("idx_articles_user_hash", table_name="articles")
    op.drop_index("idx_articles_user_cluster", table_name="articles")
    op.drop_index("idx_articles_user_topic", table_name="articles")
    op.drop_index("idx_sources_user_topic", table_name="sources")
    op.drop_index("idx_topics_user", table_name="topics")
    op.drop_table("briefings")
    op.drop_table("saved_queries")
    op.drop_table("sentiment_history")
    op.drop_table("entity_cluster_map")
    op.drop_table("entity_article_map")
    op.drop_table("entities")
    op.drop_table("articles")
    op.drop_table("clusters")
    op.drop_table("sources")
    op.drop_table("topics")
