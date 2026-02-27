#!/usr/bin/env python3
"""One-time cleanup: remove irrelevant articles and fix bad summaries.

Designed to run inside the `api` container which has psycopg2, qdrant-client,
minio, and celery packages available.

Usage:
    # Dry run (log what would happen, no changes):
    docker compose exec api python scripts/cleanup_bad_data.py --dry-run

    # Clean all topics:
    docker compose exec api python scripts/cleanup_bad_data.py

    # Clean a specific topic:
    docker compose exec api python scripts/cleanup_bad_data.py --topic-id <uuid>

    # Via Makefile:
    make cleanup-data
    make cleanup-data-dry
"""
import argparse
import os
import re
import sys

import psycopg2
import psycopg2.extras

# ── External service clients ──

import redis as redis_lib
from qdrant_client import QdrantClient
from minio import Minio
from celery import Celery

# ── Configuration (same env vars as api container) ──

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ttwatch_app:changeme@postgres:5432/ttwatch",
)
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
MINIO_URL = os.environ.get("MINIO_URL", "http://minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "ttwatch-content")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

# ── Irrelevant URL domain patterns ──

IRRELEVANT_DOMAINS = [
    "dictionary.com",
    "yourdictionary.com",
    "merriam-webster.com",
    "cambridge.org/dictionary",
    "wiktionary.org",
]

# ── Chain-of-thought cleanup (duplicated from worker/tasks/summarize.py) ──

_COT_PREFIXES = re.compile(
    r"^(?:Okay|OK|Alright|Sure|So|First|Well|Let me|I need to|I'll|Hmm|"
    r"Let's|The user|I should|I will|Looking at|Reading|After reading)[,.]?\s",
    re.IGNORECASE,
)

_COT_PHRASES = re.compile(
    r"(?:let me (?:read|summarize|think|look)|"
    r"i need to (?:summarize|read|find)|"
    r"the user (?:wants|asked|is asking)|"
    r"i (?:should|will|need to) (?:provide|write|create)|"
    r"here(?:'s| is) (?:the|my|a) summary)",
    re.IGNORECASE,
)


def clean_summary(text: str) -> str:
    """Strip chain-of-thought leakage from LLM summary output."""
    cleaned = text.strip()
    if not cleaned:
        return cleaned

    if _COT_PHRASES.search(cleaned):
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        content_sentences = []
        for s in sentences:
            if _COT_PREFIXES.match(s) or _COT_PHRASES.search(s):
                continue
            content_sentences.append(s)
        if content_sentences:
            candidate = " ".join(content_sentences[-3:])
            if len(candidate) >= 20:
                cleaned = candidate

    for _ in range(3):
        match = _COT_PREFIXES.match(cleaned)
        if match:
            cleaned = cleaned[match.end() :].strip()
        else:
            break

    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()

    if len(cleaned) < 20:
        cleaned = text.strip()

    return cleaned


# ── Helpers ──

REDIS_DEDUP_URL = os.environ.get("REDIS_DEDUP_URL", "redis://redis:6379/2")
_dedup_redis = redis_lib.from_url(REDIS_DEDUP_URL)


def print_action(dry_run: bool, msg: str):
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"  {prefix}{msg}")


# ── Main cleanup logic ──

def main():
    parser = argparse.ArgumentParser(
        description="Clean up irrelevant articles and fix bad summaries."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes.",
    )
    parser.add_argument(
        "--topic-id",
        type=str,
        default=None,
        help="Target a specific topic (UUID). Omit for all topics.",
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    print("=" * 60)
    print("  TTwatch — Data Cleanup")
    if dry_run:
        print("  MODE: DRY RUN (no changes will be made)")
    print("=" * 60)
    print()

    # ── Connect to services ──
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    except psycopg2.Error as e:
        print(f"  Database connection failed: {e}")
        sys.exit(1)

    qdrant = QdrantClient(url=QDRANT_URL)
    minio_client = Minio(
        MINIO_URL.replace("http://", "").replace("https://", ""),
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_URL.startswith("https"),
    )
    celery_app = Celery("ttwatch", broker=REDIS_URL)
    celery_app.conf.result_backend = CELERY_RESULT_BACKEND
    celery_app.conf.task_serializer = "json"
    celery_app.conf.result_serializer = "json"

    # ── Determine scope ──
    topic_filter = ""
    topic_params = []
    if args.topic_id:
        topic_filter = "AND a.topic_id = %s"
        topic_params = [args.topic_id]
        print(f"  Scope: topic {args.topic_id}")
    else:
        print("  Scope: all topics")
    print()

    # ── Counters ──
    deleted_irrelevant = 0
    cleaned_summaries = 0
    cleared_summaries = 0
    dispatched_summarize = 0
    dispatched_sentiment = 0
    dispatched_relevance = 0
    affected_topic_ids = set()

    # ════════════════════════════════════════════
    # Phase 1: Remove irrelevant articles
    # ════════════════════════════════════════════
    print("Phase 1: Removing irrelevant articles")
    print("-" * 40)

    # 1a: Articles from dictionary/definition sites
    domain_conditions = " OR ".join(
        ["a.url ILIKE %s" for _ in IRRELEVANT_DOMAINS]
    )
    domain_params = [f"%{d}%" for d in IRRELEVANT_DOMAINS]

    cur.execute(
        f"""
        SELECT a.id, a.title, a.url, a.raw_storage_key, a.topic_id, a.user_id
        FROM articles a
        WHERE ({domain_conditions})
        {topic_filter}
        """,
        domain_params + topic_params,
    )
    domain_articles = cur.fetchall()

    for row in domain_articles:
        article_id = str(row["id"])
        print_action(dry_run, f"Removing irrelevant article: {row['title'][:80]} ({row['url'][:60]})")
        affected_topic_ids.add(str(row["topic_id"]))

        if not dry_run:
            _delete_article(
                cur, qdrant, minio_client,
                article_id, row["raw_storage_key"], str(row["user_id"]),
                row["url"],
            )
        deleted_irrelevant += 1

    # 1b: Articles with "Definition" in title (that aren't about topic entities)
    cur.execute(
        f"""
        SELECT a.id, a.title, a.url, a.raw_storage_key, a.topic_id, a.user_id
        FROM articles a
        WHERE a.title ILIKE '%%definition%%'
        {topic_filter}
        """,
        topic_params,
    )
    definition_articles = cur.fetchall()

    for row in definition_articles:
        # Skip if already marked for deletion above
        if any(str(row["id"]) == str(d["id"]) for d in domain_articles):
            continue

        article_id = str(row["id"])
        print_action(dry_run, f"Removing definition article: {row['title'][:80]} ({row['url'][:60]})")
        affected_topic_ids.add(str(row["topic_id"]))

        if not dry_run:
            _delete_article(
                cur, qdrant, minio_client,
                article_id, row["raw_storage_key"], str(row["user_id"]),
                row["url"],
            )
        deleted_irrelevant += 1

    if deleted_irrelevant == 0:
        print("  No irrelevant articles found.")
    print()

    # ════════════════════════════════════════════
    # Phase 2: Clean chain-of-thought summaries
    # ════════════════════════════════════════════
    print("Phase 2: Cleaning chain-of-thought summaries")
    print("-" * 40)

    cot_patterns = [
        "Okay%", "OK,%", "OK %", "Let me%", "I need to%", "So,%", "So %",
        "First,%", "Alright%", "Sure%", "Here is%", "Here's%",
        "Well,%", "Looking at%", "Hmm%", "I'll%", "I should%",
    ]
    cot_conditions = " OR ".join(["a.summary LIKE %s" for _ in cot_patterns])

    cur.execute(
        f"""
        SELECT a.id, a.title, a.summary, a.user_id, a.topic_id
        FROM articles a
        WHERE a.summary IS NOT NULL
        AND ({cot_conditions})
        {topic_filter}
        """,
        cot_patterns + topic_params,
    )
    dirty_articles = cur.fetchall()

    for row in dirty_articles:
        article_id = str(row["id"])
        original = row["summary"]
        cleaned = clean_summary(original)
        affected_topic_ids.add(str(row["topic_id"]))

        if cleaned != original.strip() and len(cleaned) >= 20:
            print_action(dry_run, f"Cleaned summary for: {row['title'][:80]}")
            if not dry_run:
                cur.execute(
                    "UPDATE articles SET summary = %s WHERE id = %s",
                    (cleaned, row["id"]),
                )
            cleaned_summaries += 1
        else:
            print_action(dry_run, f"Cleared bad summary for: {row['title'][:80]}")
            if not dry_run:
                cur.execute(
                    "UPDATE articles SET summary = NULL WHERE id = %s",
                    (row["id"],),
                )
            cleared_summaries += 1

    if cleaned_summaries == 0 and cleared_summaries == 0:
        print("  No chain-of-thought summaries found.")
    print()

    # ════════════════════════════════════════════
    # Phase 3: Trigger re-processing for gaps
    # ════════════════════════════════════════════
    print("Phase 3: Dispatching re-processing tasks")
    print("-" * 40)

    # 3a: Articles with NULL summary
    cur.execute(
        f"""
        SELECT a.id, a.user_id, a.title
        FROM articles a
        WHERE a.summary IS NULL
        AND a.is_duplicate = false
        {topic_filter}
        """,
        topic_params,
    )
    for row in cur.fetchall():
        print_action(dry_run, f"Dispatch summarize: {row['title'][:60]}")
        if not dry_run:
            celery_app.send_task(
                "summarize_article",
                args=[str(row["user_id"]), str(row["id"])],
            )
        dispatched_summarize += 1

    # 3b: Articles with NULL sentiment_score
    cur.execute(
        f"""
        SELECT a.id, a.user_id, a.title
        FROM articles a
        WHERE a.sentiment_score IS NULL
        AND a.is_duplicate = false
        {topic_filter}
        """,
        topic_params,
    )
    for row in cur.fetchall():
        print_action(dry_run, f"Dispatch sentiment: {row['title'][:60]}")
        if not dry_run:
            celery_app.send_task(
                "classify_sentiment",
                args=[str(row["user_id"]), str(row["id"])],
            )
        dispatched_sentiment += 1

    # 3c: Articles with NULL relevance_score
    cur.execute(
        f"""
        SELECT a.id, a.user_id, a.title
        FROM articles a
        WHERE a.relevance_score IS NULL
        AND a.is_duplicate = false
        {topic_filter}
        """,
        topic_params,
    )
    for row in cur.fetchall():
        print_action(dry_run, f"Dispatch relevance: {row['title'][:60]}")
        if not dry_run:
            celery_app.send_task(
                "score_relevance",
                args=[str(row["user_id"]), str(row["id"])],
            )
        dispatched_relevance += 1

    if dispatched_summarize + dispatched_sentiment + dispatched_relevance == 0:
        print("  No re-processing needed.")
    print()

    # ════════════════════════════════════════════
    # Phase 4: Trigger re-clustering
    # ════════════════════════════════════════════
    print("Phase 4: Triggering re-clustering")
    print("-" * 40)

    if not affected_topic_ids:
        print("  No topics affected — skipping re-cluster.")
    else:
        for topic_id in affected_topic_ids:
            # Look up the user_id for this topic
            cur.execute(
                "SELECT user_id FROM topics WHERE id = %s",
                (topic_id,),
            )
            topic_row = cur.fetchone()
            if not topic_row:
                continue

            user_id = str(topic_row["user_id"])
            print_action(dry_run, f"Dispatch recluster_topic for topic {topic_id}")
            if not dry_run:
                celery_app.send_task(
                    "recluster_topic",
                    args=[user_id, topic_id],
                )

    print()

    # ════════════════════════════════════════════
    # Commit and report
    # ════════════════════════════════════════════
    if not dry_run:
        conn.commit()
    else:
        conn.rollback()

    cur.close()
    conn.close()

    print("=" * 60)
    print("  Summary")
    print("=" * 60)
    print(f"  Irrelevant articles removed:   {deleted_irrelevant}")
    print(f"  Summaries cleaned in-place:    {cleaned_summaries}")
    print(f"  Summaries cleared (for re-LLM): {cleared_summaries}")
    print(f"  Summarize tasks dispatched:    {dispatched_summarize}")
    print(f"  Sentiment tasks dispatched:    {dispatched_sentiment}")
    print(f"  Relevance tasks dispatched:    {dispatched_relevance}")
    print(f"  Topics to re-cluster:          {len(affected_topic_ids)}")
    if dry_run:
        print()
        print("  ** DRY RUN — no changes were made **")
    print()


def _delete_article(cur, qdrant, minio_client, article_id, raw_storage_key, user_id, url):
    """Delete an article and its associated data from all stores."""
    # 1. Delete from entity_article_map (junction table)
    cur.execute(
        "DELETE FROM entity_article_map WHERE article_id = %s",
        (article_id,),
    )

    # 2. Delete the article row (CASCADE handles other FKs)
    cur.execute("DELETE FROM articles WHERE id = %s", (article_id,))

    # 3. Delete Qdrant vector
    try:
        qdrant.delete(
            collection_name="articles",
            points_selector=[article_id],
        )
    except Exception as e:
        print(f"    Warning: Qdrant delete failed for {article_id}: {e}")

    # 4. Delete MinIO object
    if raw_storage_key:
        try:
            minio_client.remove_object(MINIO_BUCKET, raw_storage_key)
        except Exception as e:
            print(f"    Warning: MinIO delete failed for {raw_storage_key}: {e}")

    # 5. Remove URL from Redis dedup set so it can be re-ingested if needed
    if url:
        try:
            dedup_key = f"ttwatch:dedup:urls:{user_id}"
            _dedup_redis.srem(dedup_key, url)
        except Exception:
            pass


if __name__ == "__main__":
    main()
