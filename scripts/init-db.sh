#!/bin/bash
# scripts/init-db.sh
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EOSQL

PGPASSWORD="$POSTGRES_PASSWORD" createuser -U "$POSTGRES_USER" \
    --login --no-superuser --no-createdb --no-createrole ttwatch_app 2>/dev/null || true
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "ALTER ROLE ttwatch_app PASSWORD '$(printf '%s' "$APP_DB_PASSWORD" | sed "s/'/''/g")';"

PGPASSWORD="$POSTGRES_PASSWORD" createuser -U "$POSTGRES_USER" \
    --login --no-superuser --no-createdb --no-createrole ttwatch_worker 2>/dev/null || true
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "ALTER ROLE ttwatch_worker PASSWORD '$(printf '%s' "$WORKER_DB_PASSWORD" | sed "s/'/''/g")';"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    GRANT CONNECT ON DATABASE ttwatch TO ttwatch_app;
    GRANT CONNECT ON DATABASE ttwatch TO ttwatch_worker;
    GRANT USAGE ON SCHEMA public TO ttwatch_app;
    GRANT USAGE ON SCHEMA public TO ttwatch_worker;

    -- Grant default privileges so future tables created by Alembic migrations
    -- are automatically accessible by both roles. This is critical because
    -- Alembic runs as the postgres superuser, and without DEFAULT PRIVILEGES,
    -- the app and worker roles would get "permission denied" on every table.
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ttwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO ttwatch_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ttwatch_worker;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO ttwatch_worker;
EOSQL
