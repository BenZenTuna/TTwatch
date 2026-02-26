#!/bin/bash
# scripts/backup.sh — Backup PostgreSQL and Qdrant data
set -e

BACKUP_DIR="$(cd "$(dirname "$0")/.." && pwd)/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

echo "=== TTwatch Backup — ${TIMESTAMP} ==="

# PostgreSQL backup
echo "Backing up PostgreSQL..."
docker compose exec -T postgres pg_dump \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-ttwatch}" \
    --format=custom \
    --compress=9 \
    > "${BACKUP_DIR}/pg_${TIMESTAMP}.dump"
echo "PostgreSQL backup: ${BACKUP_DIR}/pg_${TIMESTAMP}.dump"

# Qdrant snapshot
echo "Backing up Qdrant collections..."
COLLECTIONS=$(curl -s http://localhost:6333/collections | python3 -c "
import sys, json
data = json.load(sys.stdin)
for c in data.get('result', {}).get('collections', []):
    print(c['name'])
" 2>/dev/null || true)

if [ -n "$COLLECTIONS" ]; then
    for collection in $COLLECTIONS; do
        echo "  Snapshotting collection: ${collection}"
        SNAPSHOT=$(curl -s -X POST "http://localhost:6333/collections/${collection}/snapshots" | \
            python3 -c "import sys, json; print(json.load(sys.stdin)['result']['name'])" 2>/dev/null || true)
        if [ -n "$SNAPSHOT" ]; then
            curl -s -o "${BACKUP_DIR}/qdrant_${collection}_${TIMESTAMP}.snapshot" \
                "http://localhost:6333/collections/${collection}/snapshots/${SNAPSHOT}"
            echo "  Saved: qdrant_${collection}_${TIMESTAMP}.snapshot"
        fi
    done
else
    echo "  No Qdrant collections found."
fi

echo "=== Backup complete ==="
ls -lh "${BACKUP_DIR}"/*"${TIMESTAMP}"* 2>/dev/null || true
