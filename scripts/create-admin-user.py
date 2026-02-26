#!/usr/bin/env python3
"""Interactive script to create the first admin user.

Usage:
    # From project root (uses Docker-managed PostgreSQL):
    docker compose exec api python /app/../scripts/create-admin-user.py

    # Or with DATABASE_URL set:
    python scripts/create-admin-user.py
"""
import getpass
import re
import sys
import uuid
from datetime import datetime, timezone

import psycopg2
from argon2 import PasswordHasher


def get_database_url() -> str:
    """Resolve DATABASE_URL from environment or prompt."""
    import os
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    url = input("DATABASE_URL [postgresql://ttwatch_app:changeme@localhost:5432/ttwatch]: ").strip()
    return url or "postgresql://ttwatch_app:changeme@localhost:5432/ttwatch"


def validate_email(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def validate_password(password: str) -> list[str]:
    errors = []
    if len(password) < 10:
        errors.append("Must be at least 10 characters")
    if not any(c.isupper() for c in password):
        errors.append("Must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("Must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("Must contain at least one digit")
    return errors


def main():
    print("=" * 50)
    print("  TTwatch — Create Admin User")
    print("=" * 50)
    print()

    db_url = get_database_url()

    # Collect user info
    while True:
        email = input("Email: ").strip()
        if validate_email(email):
            break
        print("  Invalid email format. Try again.")

    display_name = input("Display name: ").strip()
    if not display_name:
        display_name = email.split("@")[0]

    while True:
        password = getpass.getpass("Password: ")
        errors = validate_password(password)
        if errors:
            print("  Password requirements not met:")
            for e in errors:
                print(f"    - {e}")
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("  Passwords do not match. Try again.")
            continue
        break

    # Hash password
    ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
    password_hash = ph.hash(password)
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Insert into database
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # Check if email already exists
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            print(f"\n  Error: Email '{email}' is already registered.")
            conn.close()
            sys.exit(1)

        cur.execute(
            """
            INSERT INTO users (id, email, display_name, password_hash, is_active, is_admin, max_topics, max_articles_per_topic, max_api_keys, created_at)
            VALUES (%s, %s, %s, %s, TRUE, TRUE, 50, 10000, 10, %s)
            """,
            (str(user_id), email, display_name, password_hash, now),
        )
        conn.commit()
        cur.close()
        conn.close()

        print()
        print("  Admin user created successfully!")
        print(f"  ID:    {user_id}")
        print(f"  Email: {email}")
        print(f"  Name:  {display_name}")
        print(f"  Admin: Yes")
        print()
        print("  You can now log in at the web UI or via POST /auth/login.")

    except psycopg2.Error as e:
        print(f"\n  Database error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
