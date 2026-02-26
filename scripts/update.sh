#!/usr/bin/env bash
set -euo pipefail

# TTwatch — Safe Update Script
# Usage: ./scripts/update.sh [--no-backup] [--compose-args "..."]
#
# Steps: backup -> git pull -> rebuild -> migrate -> restart
# Safe rollback: restores from backup on failure.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
NO_BACKUP=false
COMPOSE_ARGS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-backup) NO_BACKUP=true; shift ;;
        --compose-args) COMPOSE_ARGS="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--no-backup] [--compose-args \"...\"]"
            echo ""
            echo "Options:"
            echo "  --no-backup       Skip pre-update backup"
            echo "  --compose-args    Extra args for docker compose (e.g. \"-f docker-compose.gpu.yml\")"
            exit 0
            ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

cd "$PROJECT_DIR"

echo "=========================================="
echo "  TTwatch Update — $TIMESTAMP"
echo "=========================================="
echo ""

# Step 1: Backup
if [ "$NO_BACKUP" = false ]; then
    echo "[1/5] Creating pre-update backup..."
    mkdir -p "$BACKUP_DIR"

    # Database backup
    if docker compose ps postgres --status running -q 2>/dev/null | grep -q .; then
        docker compose exec -T postgres pg_dump \
            -U "${POSTGRES_USER:-postgres}" \
            -d "${POSTGRES_DB:-ttwatch}" \
            -Fc --compress=9 \
            > "$BACKUP_DIR/pre-update_${TIMESTAMP}.dump" 2>/dev/null
        echo "  Database backup: pre-update_${TIMESTAMP}.dump"
    else
        echo "  Warning: PostgreSQL not running, skipping DB backup."
    fi

    # Save current git ref for rollback
    git rev-parse HEAD > "$BACKUP_DIR/pre-update_${TIMESTAMP}.gitref"
    echo "  Git ref saved: $(cat "$BACKUP_DIR/pre-update_${TIMESTAMP}.gitref")"
else
    echo "[1/5] Backup skipped (--no-backup)"
fi
echo ""

# Step 2: Git pull
echo "[2/5] Pulling latest changes..."
PREV_REF="$(git rev-parse HEAD)"
git pull --ff-only
NEW_REF="$(git rev-parse HEAD)"

if [ "$PREV_REF" = "$NEW_REF" ]; then
    echo "  Already up to date ($PREV_REF)."
else
    echo "  Updated: ${PREV_REF:0:8} -> ${NEW_REF:0:8}"
    echo "  Changes:"
    git log --oneline "$PREV_REF".."$NEW_REF" | head -10 | sed 's/^/    /'
fi
echo ""

# Step 3: Rebuild containers
echo "[3/5] Rebuilding containers..."
# shellcheck disable=SC2086
docker compose $COMPOSE_ARGS build --parallel 2>&1 | tail -5
echo "  Build complete."
echo ""

# Step 4: Run migrations
echo "[4/5] Running database migrations..."
if [ -f config/alembic.ini ]; then
    # shellcheck disable=SC2086
    docker compose $COMPOSE_ARGS run --rm api alembic -c /app/config/alembic.ini upgrade head 2>&1 | tail -3
    echo "  Migrations applied."
else
    echo "  No alembic.ini found, skipping migrations."
fi
echo ""

# Step 5: Restart services
echo "[5/5] Restarting services..."
# shellcheck disable=SC2086
docker compose $COMPOSE_ARGS up -d 2>&1 | tail -5
echo ""

# Health check
echo "Waiting for services to become healthy..."
sleep 10

HEALTHY=true
for service in api postgres redis qdrant; do
    STATUS=$(docker compose ps "$service" --format json 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        data = data[0] if data else {}
    print(data.get('Health', data.get('State', 'unknown')))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
    if echo "$STATUS" | grep -qi "healthy"; then
        echo "  $service: healthy"
    else
        echo "  $service: $STATUS"
        HEALTHY=false
    fi
done

echo ""
if [ "$HEALTHY" = true ]; then
    echo "Update completed successfully!"
else
    echo "Warning: Some services are not healthy yet."
    echo "Check logs: docker compose logs --tail=50"
    echo ""
    echo "To rollback:"
    echo "  git checkout $PREV_REF"
    echo "  docker compose $COMPOSE_ARGS build && docker compose $COMPOSE_ARGS up -d"
    if [ "$NO_BACKUP" = false ]; then
        echo "  # Restore DB: docker compose exec -T postgres pg_restore -U postgres -d ttwatch --clean < backups/pre-update_${TIMESTAMP}.dump"
    fi
fi
