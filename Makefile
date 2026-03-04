# Makefile — common TTwatch operations

.PHONY: dev prod gpu lan cloud stop logs backup restore migrate cleanup-data cleanup-data-dry verify-vllm

# === Development ===
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

dev-gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.dev.yml up --build

# === Production ===
prod:
	docker compose -f docker-compose.yml up -d

gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

lan:
	docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d

cloud:
	docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d

# === GPU Node (run on remote GPU machine) ===
gpu-node:
	docker compose -f docker-compose.gpu-node.yml up -d

search-node:
	docker compose -f docker-compose.search-node.yml up -d

# === Operations ===
stop:
	docker compose down

logs:
	docker compose logs -f --tail=100

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker-io worker-cpu

# === Database ===
migrate:
	docker compose exec api alembic upgrade head

migrate-new:
	docker compose exec api alembic revision --autogenerate -m "$(msg)"

# === Backup & Restore ===
backup:
	bash scripts/backup.sh

restore:
	bash scripts/restore.sh $(file)

# === Utilities ===
shell-api:
	docker compose exec api bash

shell-db:
	docker compose exec postgres psql -U postgres -d ttwatch

health:
	curl -s http://localhost:8080/health/services | python3 -m json.tool

create-admin:
	docker compose exec api python scripts/create-admin-user.py

seed-topics:
	docker compose exec api python scripts/seed-topics.py

cleanup-data:
	docker compose exec api python scripts/cleanup_bad_data.py

cleanup-data-dry:
	docker compose exec api python scripts/cleanup_bad_data.py --dry-run

# === GPU Verification ===
verify-vllm:
	bash scripts/verify-vllm-memory.sh
