# TTwatch Deployment Guide

TTwatch supports four deployment modes. All modes share the same codebase — only environment variables and compose file combinations change.

## Prerequisites

- Docker Engine 24+ and Docker Compose V2
- Git
- 8 GB RAM minimum (16 GB recommended for GPU mode)
- NVIDIA GPU + nvidia-container-toolkit (GPU modes only)

---

## Quick Start

```bash
git clone <repo-url> && cd TTwatch
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD, APP_DB_PASSWORD, WORKER_DB_PASSWORD, JWT_SECRET
```

Generate a JWT secret:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Mode 1: Development (no GPU)

Uses cloud LLM (OpenAI) with hot-reload for API and worker.

```bash
# Set in .env:
# LLM_PROVIDER=cloud
# CLOUD_LLM_API_KEY=sk-...

make dev
# Or: docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Features:
- API auto-reloads on code changes (uvicorn `--reload`)
- Worker auto-restarts via watchmedo
- Frontend uses watchpack polling for WSL
- Solo worker pool (single-threaded, easier debugging)

---

## Mode 2: GPU Colocated (single machine)

All services including vLLM and BGE-M3 on one machine with GPU.

```bash
# Download model weights first:
bash scripts/download-models.sh

# Start everything:
make gpu
# Or: docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Verify GPU services:
```bash
python scripts/benchmark-gpu.py
```

VRAM requirements: ~25-31 GB (RTX 5090/A100 recommended).

---

## Mode 3: LAN Distributed

Split GPU services to a separate machine on the same LAN.

### GPU Machine (e.g. 192.168.1.200)

```bash
# On the GPU machine — clone the repo and run:
make gpu-node
# Or: docker compose -f docker-compose.gpu-node.yml up -d
```

Optionally run SearXNG on a third machine:
```bash
# On search machine (e.g. 192.168.1.201):
make search-node
# Or: docker compose -f docker-compose.search-node.yml up -d
```

### Main Server (e.g. 192.168.1.100)

```bash
# Set in .env:
# VLLM_URL=http://192.168.1.200:8000/v1
# EMBEDDER_URL=http://192.168.1.200:8001
# SEARXNG_URL=http://192.168.1.201:8080  (if using search node)
# CORS_ORIGINS=http://192.168.1.100:3000
# NEXT_PUBLIC_API_URL=http://192.168.1.100:8080
# NEXT_PUBLIC_WS_URL=ws://192.168.1.100:8080/ws

make lan
# Or: docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d
```

See [docs/lan-deployment.md](lan-deployment.md) for detailed LAN instructions.

---

## Mode 4: Cloud (no GPU)

Uses OpenAI (or compatible) for LLM and embeddings. No local GPU needed.

```bash
# Set in .env:
# LLM_PROVIDER=cloud
# CLOUD_LLM_API_KEY=sk-...
# CLOUD_LLM_MODEL=gpt-4o-mini
# CLOUD_EMBEDDING_MODEL=text-embedding-3-large
# EMBEDDING_DIMENSION=3072

make cloud
# Or: docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d
```

Note: `EMBEDDING_DIMENSION` changes from 1024 (BGE-M3) to 3072 (OpenAI). If switching modes, Qdrant collections must be recreated.

---

## Post-Deployment Setup

### 1. Run Database Migrations

```bash
make migrate
# Or: docker compose exec api alembic upgrade head
```

### 2. Create Admin User

```bash
make create-admin
# Or: docker compose exec api python scripts/create-admin-user.py
```

### 3. Seed Example Topics (optional)

```bash
make seed-topics
# Or: USER_EMAIL=admin@example.com docker compose exec api python scripts/seed-topics.py
```

### 4. Verify Health

```bash
make health
# Or: curl -s http://localhost:8080/health/services | python3 -m json.tool
```

---

## Operations

### Logs

```bash
make logs           # All services
make logs-api       # API only
make logs-worker    # Both workers
```

### Backup & Restore

```bash
make backup         # PostgreSQL + Qdrant snapshots -> backups/
make restore file=backups/ttwatch_20250101_120000.dump
```

### Safe Update

```bash
bash scripts/update.sh
# Steps: backup -> git pull -> rebuild -> migrate -> restart
```

### Database Shell

```bash
make shell-db       # psql into PostgreSQL
make shell-api      # bash into API container
```

---

## Services Overview

| Service | Port | Description |
|---|---|---|
| frontend | 3000 | Next.js React UI |
| api | 8080 | FastAPI backend |
| postgres | 5432 | PostgreSQL 16 with RLS |
| redis | 6379 | Cache, broker, pub/sub |
| qdrant | 6333 | Vector search |
| minio | 9000/9001 | Object storage |
| searxng | 8888 | Meta-search engine |
| worker-io | — | Celery gevent worker (I/O) |
| worker-cpu | — | Celery prefork worker (compute) |
| scheduler | — | Celery Beat |
| vllm | 8100 | Local LLM (GPU mode) |
| embedder | 8101 | BGE-M3 embeddings (GPU mode) |

---

## Environment Variables

See `.env.example` for the complete list. Key variables:

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL superuser password |
| `APP_DB_PASSWORD` | Yes | API service DB password |
| `WORKER_DB_PASSWORD` | Yes | Worker service DB password |
| `JWT_SECRET` | Yes | JWT signing secret (256-bit) |
| `LLM_PROVIDER` | No | `local` (default) or `cloud` |
| `CLOUD_LLM_API_KEY` | Cloud mode | OpenAI API key |
| `CORS_ORIGINS` | No | Comma-separated origins |
| `VLLM_URL` | LAN mode | Remote vLLM URL |
| `EMBEDDER_URL` | LAN mode | Remote embedder URL |
| `SEARXNG_URL` | LAN mode | Remote SearXNG URL |

---

## Troubleshooting

**Services not starting:** Check `docker compose logs <service>` for errors.

**Database connection errors:** Verify `APP_DB_PASSWORD` / `WORKER_DB_PASSWORD` match what was set when PostgreSQL was first initialized. If passwords were changed after first run, delete the `pgdata` volume and reinitialize.

**Qdrant dimension mismatch:** If you switch between local (1024-dim) and cloud (3072-dim) embeddings, delete the Qdrant volume: `docker volume rm ttwatch_qdrant_data`.

**GPU not detected:** Ensure `nvidia-container-toolkit` is installed and `nvidia-smi` works inside Docker: `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`.
