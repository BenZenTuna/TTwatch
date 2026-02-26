#!/bin/bash
# scripts/restore.sh — Restore PostgreSQL from a backup file
# Usage: bash scripts/restore.sh backups/pg_20240101_120000.dump
set -e

BACKUP_FILE="${1:?Usage: $0 <backup-file>}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "=== TTwatch Restore ==="
echo "Backup file: ${BACKUP_FILE}"

case "$BACKUP_FILE" in
    *.dump)
        echo "Restoring PostgreSQL..."
        docker compose exec -T postgres pg_restore \
            -U "${POSTGRES_USER:-postgres}" \
            -d "${POSTGRES_DB:-ttwatch}" \
            --clean \
            --if-exists \
            --no-owner \
            < "$BACKUP_FILE"
        echo "PostgreSQL restore complete."
        ;;
    *.snapshot)
        COLLECTION=$(echo "$BACKUP_FILE" | sed -E 's/.*qdrant_([^_]+)_.*/\1/')
        echo "Restoring Qdrant collection: ${COLLECTION}"
        curl -X POST "http://localhost:6333/collections/${COLLECTION}/snapshots/upload" \
            -H "Content-Type: multipart/form-data" \
            -F "snapshot=@${BACKUP_FILE}"
        echo ""
        echo "Qdrant restore complete."
        ;;
    *)
        echo "Error: Unrecognized backup format. Expected .dump or .snapshot"
        exit 1
        ;;
esac

echo "=== Restore complete ==="
