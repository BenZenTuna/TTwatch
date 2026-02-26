#!/usr/bin/env python3
"""Seed example topics with search configurations for a user.

Usage:
    # Provide user email or ID via env var or argument:
    USER_EMAIL=admin@example.com python scripts/seed-topics.py

    # Or interactively:
    python scripts/seed-topics.py
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import psycopg2


EXAMPLE_TOPICS = [
    {
        "name": "AI Safety",
        "icon": "shield",
        "config": {
            "search_terms": [
                "AI safety research",
                "AI alignment",
                "artificial intelligence regulation",
                "AI governance policy",
                "large language model safety",
            ],
            "search_engines": ["google", "bing", "duckduckgo"],
            "max_results_per_query": 10,
            "language": "en",
        },
        "refresh_interval_minutes": 120,
    },
    {
        "name": "Biotechnology",
        "icon": "dna",
        "config": {
            "search_terms": [
                "biotech breakthrough",
                "gene therapy clinical trial",
                "CRISPR gene editing",
                "mRNA technology",
                "synthetic biology startups",
            ],
            "search_engines": ["google", "bing", "duckduckgo"],
            "max_results_per_query": 10,
            "language": "en",
        },
        "refresh_interval_minutes": 180,
    },
    {
        "name": "Semiconductor Industry",
        "icon": "cpu",
        "config": {
            "search_terms": [
                "semiconductor chip manufacturing",
                "TSMC Intel Samsung foundry",
                "AI chip GPU market",
                "semiconductor supply chain",
                "chip export controls",
            ],
            "search_engines": ["google", "bing", "duckduckgo"],
            "max_results_per_query": 10,
            "language": "en",
        },
        "refresh_interval_minutes": 120,
    },
    {
        "name": "Renewable Energy",
        "icon": "zap",
        "config": {
            "search_terms": [
                "solar energy breakthrough",
                "wind power capacity",
                "battery storage technology",
                "green hydrogen production",
                "renewable energy investment",
            ],
            "search_engines": ["google", "bing", "duckduckgo"],
            "max_results_per_query": 10,
            "language": "en",
        },
        "refresh_interval_minutes": 240,
    },
    {
        "name": "Cybersecurity Threats",
        "icon": "lock",
        "config": {
            "search_terms": [
                "cybersecurity threat intelligence",
                "ransomware attack",
                "zero-day vulnerability",
                "nation-state cyber attack",
                "critical infrastructure cyber defense",
            ],
            "search_engines": ["google", "bing", "duckduckgo"],
            "max_results_per_query": 10,
            "language": "en",
        },
        "refresh_interval_minutes": 60,
    },
]


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    url = input("DATABASE_URL [postgresql://ttwatch_app:changeme@localhost:5432/ttwatch]: ").strip()
    return url or "postgresql://ttwatch_app:changeme@localhost:5432/ttwatch"


def main():
    print("=" * 50)
    print("  TTwatch — Seed Example Topics")
    print("=" * 50)
    print()

    db_url = get_database_url()

    # Resolve user
    user_email = os.environ.get("USER_EMAIL")
    if not user_email:
        user_email = input("User email to seed topics for: ").strip()

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT id, display_name, max_topics FROM users WHERE email = %s", (user_email,))
        row = cur.fetchone()
        if not row:
            print(f"  Error: User '{user_email}' not found.")
            conn.close()
            sys.exit(1)

        user_id, display_name, max_topics = row
        print(f"  User: {display_name} ({user_email})")
        print(f"  Max topics: {max_topics}")
        print()

        # Check existing topics
        cur.execute("SELECT COUNT(*) FROM topics WHERE user_id = %s", (str(user_id),))
        existing_count = cur.fetchone()[0]
        available_slots = max_topics - existing_count

        if available_slots <= 0:
            print(f"  User already has {existing_count} topics (limit: {max_topics}). Cannot seed more.")
            conn.close()
            sys.exit(1)

        topics_to_seed = EXAMPLE_TOPICS[:available_slots]
        now = datetime.now(timezone.utc)
        seeded = 0

        for topic_data in topics_to_seed:
            topic_id = uuid.uuid4()
            # Skip if topic name already exists for this user
            cur.execute(
                "SELECT id FROM topics WHERE user_id = %s AND name = %s",
                (str(user_id), topic_data["name"]),
            )
            if cur.fetchone():
                print(f"  Skipped (exists): {topic_data['name']}")
                continue

            cur.execute(
                """
                INSERT INTO topics (id, user_id, name, icon, config, refresh_interval_minutes, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(topic_id),
                    str(user_id),
                    topic_data["name"],
                    topic_data["icon"],
                    json.dumps(topic_data["config"]),
                    topic_data["refresh_interval_minutes"],
                    now,
                    now,
                ),
            )
            print(f"  Created: {topic_data['name']} ({topic_id})")
            seeded += 1

        conn.commit()
        cur.close()
        conn.close()

        print()
        print(f"  Seeded {seeded} topics for {display_name}.")
        print("  Topics will begin searching on the next scheduler cycle.")

    except psycopg2.Error as e:
        print(f"\n  Database error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
