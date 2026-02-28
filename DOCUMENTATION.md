# TTwatch Platform Documentation

**Version**: 1.1
**Last Updated**: 2026-02-27
**Scope**: Complete platform reference covering architecture, deployment, APIs, data flows, and implementation details.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Technology Stack](#3-technology-stack)
4. [Infrastructure and Deployment](#4-infrastructure-and-deployment)
5. [Multi-Tenancy Model](#5-multi-tenancy-model)
6. [Database Schema](#6-database-schema)
7. [Authentication and Authorization](#7-authentication-and-authorization)
8. [API Reference](#8-api-reference)
9. [Background Processing](#9-background-processing)
10. [Core Processing Pipeline](#10-core-processing-pipeline)
11. [Frontend](#11-frontend)
12. [Investment Module](#12-investment-module)
13. [Data Flow Diagrams](#13-data-flow-diagrams)
14. [Configuration Reference](#14-configuration-reference)
15. [Implementation Status](#15-implementation-status)
16. [File and Directory Reference](#16-file-and-directory-reference)
17. [Development Guide](#17-development-guide)
18. [Appendix: Key Design Decisions](#18-appendix-key-design-decisions)
19. [Changelog](#19-changelog-v10--v11)

---

## 1. Executive Summary

TTwatch is a self-hosted, multi-tenant intelligence monitoring platform that continuously ingests news from the open web, clusters related articles, extracts entities, tracks sentiment, generates analyst-style briefings, and correlates news events with financial market movements. It operates entirely offline (no mandatory cloud dependencies) using dual local LLMs (vLLM serving Qwen3-32B-AWQ as the primary reasoning model and Qwen3-8B-AWQ as a fast classification model) and a local embedding model (Qwen3-Embedding-0.6B), with optional cloud LLM fallback for GPU-less deployments.

### Core Capabilities

- **Automated News Ingestion**: SearXNG meta-search with LLM-generated query decomposition, trafilatura content extraction, and 3-layer deduplication (URL, content hash, semantic).
- **Intelligent Clustering**: HDBSCAN clustering on UMAP-reduced embeddings groups related articles into thematic clusters with LLM-generated labels.
- **Entity Extraction and Resolution**: LLM-based named entity recognition (NER) with automatic resolution of organizations, products, and technologies to ticker symbols.
- **Sentiment Analysis**: Per-article sentiment classification on a -1.0 to 1.0 scale with daily historical aggregation per cluster.
- **Briefing Generation**: Hierarchical summarization pipeline (articles to cluster summaries to topic briefings) producing executive-style intelligence reports.
- **Investment Intelligence**: Watchlists, price alerts (real-time via WebSocket), investment analyses per asset, and correlation signal detection between news sentiment and price movements.
- **Real-Time Updates**: WebSocket connections deliver price alert notifications, search completion events, and search progress updates to the frontend dashboard.
- **Dual-Model LLM Routing**: Per-user configurable task routing between primary (heavy reasoning) and fast (lightweight classification) LLM models across 10 task categories.
- **Pipeline Stall Detection**: Automatic detection and recovery of stuck processing pipelines every 2 minutes.

### Design Philosophy

- **Privacy-first**: All processing happens locally by default. No data leaves the network unless the user explicitly configures cloud LLM providers.
- **Multi-tenant from day one**: PostgreSQL Row-Level Security (RLS) enforces data isolation at the database level. Every user-scoped query is automatically filtered.
- **Resilient to reclustering**: Sentiment history records preserve `cluster_keyword` text so that historical timelines survive the cluster deletion/recreation cycle that runs every 2 hours.
- **LAN-distributable**: GPU inference, search, and storage services can each run on separate machines with zero code changes, only Docker Compose overlay configuration.

---

## 2. Architecture Overview

### High-Level Architecture

TTwatch follows a service-oriented architecture with 12 Docker containers communicating over a shared Docker network:

```
                                    +------------------+
                                    |    Frontend      |
                                    |  (Next.js 14.2)  |
                                    +--------+---------+
                                             |
                                             | HTTP / WebSocket
                                             v
+------------------+           +--------------------------+          +------------------+
|   SearXNG        |<----------|        API Server        |--------->|    PostgreSQL    |
|  (Meta-search)   |           |       (FastAPI)          |          |  (16 + RLS)      |
+------------------+           +----+--------+-------+----+          +------------------+
                                    |        |       |
                            +-------+   +----+  +----+------+
                            |           |       |           |
                            v           v       v           v
                     +----------+ +----------+ +----------+ +----------+
                     |  Redis   | |  Qdrant  | |  MinIO   | |  vLLM    |
                     |  (7)     | | (v1.12)  | | (S3)     | | (Primary)|
                     +----------+ +----------+ +----------+ +----------+
                            ^                                    ^
                            |                              +-----+------+
                     +------+------+                       | vLLM-Fast  |
                     | Worker-IO   |                       | (Classify) |
                     | (gevent x32)|                       +-----+------+
                     +------+------+                             ^
                            |                              +-----+------+
                     +------+------+                       |  Embedder  |
                     | Worker-CPU  |                       |  (GPU/CPU) |
                     | (prefork x2)|                       +------------+
                     +-------------+
                            ^
                     +------+------+
                     |  Scheduler  |
                     | (Celery Beat)|
                     +-------------+
```

### Service Roles

| Service | Container Name | Role |
|---------|---------------|------|
| **PostgreSQL 16** | `postgres` | Primary data store with RLS policies |
| **Qdrant v1.12.1** | `qdrant` | Vector database for article embeddings and semantic search |
| **Redis 7** | `redis` | Celery broker (db0), result backend (db1), URL dedup set (db2), cache/rate-limit/pub-sub (db3) |
| **MinIO** | `minio` | S3-compatible object store for raw article text |
| **SearXNG** | `searxng` | Privacy-respecting meta-search engine (Google, Bing, DuckDuckGo, Google News, Bing News) |
| **API** | `api` | FastAPI application server (HTTP + WebSocket) on port 8080 |
| **Worker-IO** | `worker-io` | Celery worker pool (gevent, concurrency=32) for I/O-bound tasks |
| **Worker-CPU** | `worker-cpu` | Celery worker pool (prefork, concurrency=2) for CPU/LLM-bound tasks |
| **Scheduler** | `scheduler` | Celery Beat scheduler for periodic task dispatch |
| **Frontend** | `frontend` | Next.js 14.2 single-page application on port 3000 |
| **vLLM** | `vllm` | GPU-accelerated primary LLM inference (Qwen3-32B-AWQ) for complex reasoning tasks |
| **vLLM-Fast** | `vllm-fast` | GPU-accelerated fast LLM inference (Qwen3-8B-AWQ) for classification tasks |
| **Embedder** | `embedder` | Embedding server (Qwen3-Embedding-0.6B, 1024 dimensions, GPU or CPU) |

### Communication Patterns

1. **Synchronous HTTP**: Frontend to API, API to Qdrant/MinIO, Worker to SearXNG/vLLM/vLLM-Fast/Embedder/Qdrant/MinIO.
2. **Async message queue**: API dispatches Celery tasks via Redis broker. Workers consume from `ttwatch:default` (IO tasks) and `ttwatch:compute` (CPU tasks) queues.
3. **WebSocket**: Frontend maintains a persistent WebSocket connection to API at `/ws` for real-time price alerts, search completion, and search progress notifications.
4. **Redis pub/sub**: Workers publish to `ttwatch:alerts:triggered`, `ttwatch:search:completed`, and `ttwatch:search:progress` channels. API background coroutines subscribe and bridge messages to the WebSocket ConnectionManager.
5. **Shared database**: Both API (async via asyncpg) and Workers (sync via psycopg2 with psycogreen gevent patching) connect to PostgreSQL with different roles and connection pools.

---

## 3. Technology Stack

### Backend

| Component | Technology | Version/Details |
|-----------|-----------|-----------------|
| API Framework | FastAPI | Async with Uvicorn ASGI server on port 8080 |
| ORM (API) | SQLAlchemy | Async engine via asyncpg, pool_size=20, max_overflow=10 |
| ORM (Worker) | SQLAlchemy | Sync engine via psycopg2 + psycogreen gevent patching, pool_size=5 |
| Migrations | Alembic | 7 migration versions (001-007) |
| Task Queue | Celery | JSON serialization, dual worker pools |
| Password Hashing | Argon2id | time_cost=3, memory_cost=65536 (64 MB), parallelism=4 |
| JWT | PyJWT | HS256 algorithm |
| Content Extraction | trafilatura | `favor_precision=True`, `include_tables=True`, download timeout 10s, max redirects 2 |
| HTTP Client (API) | httpx | Async client for vLLM/embedder communication |
| HTTP Client (Worker) | httpx | Sync client with 300s timeout, LAN startup retry (30 attempts, exponential backoff 5-60s) |
| Retry Logic | tenacity | Exponential backoff for LAN service connectivity |

### AI/ML

| Component | Technology | Details |
|-----------|-----------|---------|
| LLM Inference (Primary) | vLLM v0.16.0 | `--quantization awq_marlin --gpu-memory-utilization 0.65 --max-model-len 8192 --max-num-seqs 8 --enable-prefix-caching --reasoning-parser deepseek_r1` |
| LLM Inference (Fast) | vLLM v0.16.0 | `--quantization awq_marlin --gpu-memory-utilization 0.85 --max-model-len 8192 --max-num-seqs 16 --enable-prefix-caching --disable-log-requests` |
| Primary LLM Model | Qwen3-32B-AWQ | Qwen3 reasoning model, AWQ 4-bit quantization |
| Fast LLM Model | Qwen3-8B-AWQ | Qwen3 classification model, AWQ 4-bit, thinking disabled via `chat_template_kwargs.enable_thinking=False` |
| Embedding Model | Qwen3-Embedding-0.6B | 1024-dimensional embeddings, COSINE distance |
| Embedding Server | sentence-transformers | FastAPI wrapper, `batch_size=64`, `normalize_embeddings=True`, configurable device (GPU/CPU) |
| Dimensionality Reduction | UMAP | 1024 to 20 dimensions, cosine metric, `random_state=42` |
| Clustering | HDBSCAN | `min_cluster_size=5`, `min_samples=3` |
| Cloud LLM (optional) | OpenAI / Anthropic / OpenRouter | Configurable fallback; cloud embedding uses `text-embedding-3-large` (3072 dims) |
| LLM Task Router | Per-user config | 10 task categories routable to primary/fast/auto per user via `llm_task_config` table |

### Frontend

| Component | Technology | Version/Details |
|-----------|-----------|-----------------|
| Framework | Next.js | ^14.2.0 |
| UI Library | React | 18.3.0 |
| State Management | Zustand | ^4.5.0 |
| HTTP Client | Axios | ^1.7.0 with interceptors for JWT auto-refresh on 401 |
| Styling | Tailwind CSS | ^3.4.0, dark theme with custom design tokens (surface base `#0f1117`) |
| Charts | Recharts | ^2.12.0, sentiment timelines, trend charts |
| Visualizations | D3.js | ^7.9.0, force-directed bubble clusters and entity network graphs |
| Icons | Lucide React | ^0.400.0 |
| Date Formatting | date-fns | ^3.6.0 |
| TypeScript | | ^5.5.0 |

### Infrastructure

| Component | Technology | Version/Details |
|-----------|-----------|-----------------|
| Database | PostgreSQL | 16 with `pg_trgm` extension, RLS |
| Vector DB | Qdrant | v1.12.1, COSINE distance, REST API |
| Cache/Queue | Redis | 7-alpine, 4 databases (broker, results, dedup, cache), maxmemory 512MB volatile-LRU |
| Object Storage | MinIO | S3-compatible, single bucket `ttwatch-content` |
| Meta-Search | SearXNG | Google, Bing, DuckDuckGo, Google News, Bing News engines |
| Containerization | Docker Compose | 7 compose files for 4 deployment modes |
| Market Data | yfinance + CoinGecko | Equities via yfinance, crypto via CoinGecko API |

---

## 4. Infrastructure and Deployment

### Deployment Modes

TTwatch supports four deployment modes via Docker Compose overlay files:

#### 1. Development (`make dev`)
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```
- Hot-reload for API (`uvicorn --reload`) and workers (`watchmedo auto-restart`)
- Source code mounted as volumes for live editing
- No GPU services (uses cloud or no LLM)
- Worker-IO runs in solo mode with concurrency=1 for debugging

#### 2. GPU-Colocated (`make gpu`)
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```
- All services on a single machine with NVIDIA GPU
- vLLM (primary) uses 65% GPU memory, vLLM-Fast uses 85% (sequential startup)
- Embedder runs on CPU by default (`EMBEDDER_DEVICE=cpu`)
- API and workers depend on vLLM, vLLM-Fast, and embedder health checks

#### 3. LAN-Distributed (`make lan`)
```bash
# Main node:
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d
# GPU node:
docker compose -f docker-compose.gpu-node.yml up -d
# Search node (optional):
docker compose -f docker-compose.search-node.yml up -d
```
- Splits GPU inference, search, and main application across machines
- Configure via environment variables: `VLLM_URL`, `VLLM_FAST_URL`, `EMBEDDER_URL`, `SEARXNG_URL`
- Workers retry LAN connections with exponential backoff (30 attempts, 5-60s delay)
- Local SearXNG disabled via Docker profile

#### 4. Cloud-Only (`make cloud`)
```bash
docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d
```
- No local GPU required
- Sets `LLM_PROVIDER=cloud`
- Uses OpenAI/Anthropic/OpenRouter for LLM inference (default: `gpt-4o-mini`)
- Uses `text-embedding-3-large` (3072 dimensions) for embeddings
- Disables local vLLM, vLLM-Fast, and embedder services

### Docker Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Base configuration: postgres, qdrant, redis, minio, searxng, api, worker-io, worker-cpu, scheduler, frontend |
| `docker-compose.gpu.yml` | Adds vllm + vllm-fast + embedder services, overrides worker/api dependencies |
| `docker-compose.dev.yml` | Development overrides: hot-reload, source volume mounts, debug logging |
| `docker-compose.cloud.yml` | Cloud LLM mode: sets provider env vars, disables GPU services |
| `docker-compose.lan.yml` | LAN mode: disables local searxng, removes GPU dependencies |
| `docker-compose.gpu-node.yml` | Standalone GPU node: vllm + embedder only, exposes ports 8000/8001 |
| `docker-compose.search-node.yml` | Standalone SearXNG node, exposes port 8080 |

### Shared Environment Anchor

The base `docker-compose.yml` defines an `x-common-env` YAML anchor reused by API, worker-io, worker-cpu, and scheduler services:

```yaml
x-common-env: &common-env
  REDIS_URL: redis://redis:6379/0
  CELERY_RESULT_BACKEND: redis://redis:6379/1
  REDIS_DEDUP_URL: redis://redis:6379/2
  REDIS_CACHE_URL: redis://redis:6379/3
  QDRANT_URL: http://qdrant:6333
  VLLM_URL: ${VLLM_URL:-http://vllm:8000/v1}
  VLLM_FAST_URL: ${VLLM_FAST_URL:-http://vllm-fast:8000/v1}
  EMBEDDER_URL: ${EMBEDDER_URL:-http://embedder:8001}
  SEARXNG_URL: ${SEARXNG_URL:-http://searxng:8080}
  MINIO_URL: ${MINIO_URL:-http://minio:9000}
  MINIO_ACCESS_KEY: ${MINIO_ROOT_USER:-minioadmin}
  MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD:-minioadmin}
  MINIO_BUCKET: ${MINIO_BUCKET:-ttwatch-content}
  LLM_PROVIDER: ${LLM_PROVIDER:-local}
  LOCAL_MODEL_NAME: ${LOCAL_MODEL_NAME:-Qwen3-32B-AWQ}
  FAST_MODEL_NAME: ${FAST_MODEL_NAME:-Qwen3-8B-AWQ}
  EMBEDDING_MODEL_NAME: ${EMBEDDING_MODEL_NAME:-Qwen/Qwen3-Embedding-0.6B}
  EMBEDDING_DIMENSION: ${EMBEDDING_DIMENSION:-1024}
  JWT_SECRET: ${JWT_SECRET}
```

The API service adds `DATABASE_URL: postgresql://ttwatch_app:${APP_DB_PASSWORD}@postgres:5432/${POSTGRES_DB:-ttwatch}` and `CORS_ORIGINS`.

The worker services add `DATABASE_URL: postgresql://ttwatch_worker:${WORKER_DB_PASSWORD}@postgres:5432/${POSTGRES_DB:-ttwatch}`.

### Health Checks

Every infrastructure service has a Docker health check:

| Service | Health Check | Interval | Retries | Start Period |
|---------|-------------|----------|---------|-------------|
| PostgreSQL | `pg_isready -U postgres` | 5s | 5 | — |
| Qdrant | TCP check `:6333` | 5s | 5 | — |
| Redis | `redis-cli ping` | 5s | 5 | — |
| MinIO | HTTP GET `:9000/minio/health/live` | 10s | 5 | — |
| SearXNG | `wget --spider :8080/healthz` | 10s | 3 | — |
| API | HTTP GET `:8080/health` | 10s | 5 | — |
| Worker-IO | `celery inspect ping --timeout 10` | 30s | 3 | 30s |
| Worker-CPU | `celery inspect ping --timeout 10` | 30s | 3 | 30s |
| Scheduler | `pgrep -f celery` | 30s | 3 | 15s |
| Frontend | `wget --spider :3000` | 10s | 5 | 15s |
| vLLM | HTTP GET `:8000/health` | 10s | 10 | 120s |
| vLLM-Fast | HTTP GET `:8000/health` | 15s | 10 | 120s |
| Embedder | HTTP GET `:8001/health` | 10s | 5 | 60s |

### Makefile Targets

| Target | Command |
|--------|---------|
| `dev` | Dev mode (no GPU) |
| `dev-gpu` | Dev mode with GPU services |
| `prod` | Production (no GPU, detached) |
| `gpu` | Production GPU-colocated |
| `lan` | LAN-distributed main node |
| `cloud` | Cloud LLM mode |
| `gpu-node` | Standalone GPU node |
| `search-node` | Standalone SearXNG node |
| `stop` | Stop all containers |
| `logs` / `logs-api` / `logs-worker` | View logs |
| `migrate` | Run Alembic migrations |
| `migrate-new` | Create new migration revision |
| `backup` / `restore` | PostgreSQL backup/restore |
| `shell-api` / `shell-db` | Interactive shells |
| `health` | Check all service health via `/health/services` |
| `create-admin` | Create admin user |
| `seed-topics` | Seed sample topics |
| `cleanup-data` / `cleanup-data-dry` | Run data cleanup script |

### Scripts

| Script | Location | Purpose |
|--------|----------|---------|
| `init-db.sh` | `scripts/init-db.sh` | Creates `pg_trgm` extension, `ttwatch_app` and `ttwatch_worker` roles, grants CONNECT/USAGE/ALTER DEFAULT PRIVILEGES |
| `create-admin-user.py` | `scripts/create-admin-user.py` | Interactive admin user creation with Argon2id hashing |
| `seed-topics.py` | `scripts/seed-topics.py` | Seeds sample topics (AI and Semiconductors, Geopolitical Risk, Cryptocurrency Markets) |
| `backup.sh` | `scripts/backup.sh` | `pg_dump` to timestamped SQL file in `backups/` |
| `restore.sh` | `scripts/restore.sh` | Restore from backup file |
| `download-models.sh` | `scripts/download-models.sh` | Downloads LLM and embedding models from HuggingFace |
| `update.sh` | `scripts/update.sh` | `git pull`, rebuild, migrate, restart |
| `benchmark-gpu.py` | `scripts/benchmark-gpu.py` | Benchmarks vLLM inference throughput |
| `cleanup_bad_data.py` | `scripts/cleanup_bad_data.py` | Cleans articles with `<think>` tags, CoT remnants, or placeholder summaries; supports `--dry-run` |
| `ttwatch-diagnose.sh` | `scripts/ttwatch-diagnose.sh` | Comprehensive system diagnostic (12 sections: containers, connectivity, DB state, queues, logs, Qdrant, MinIO, env, E2E test) |

---

## 5. Multi-Tenancy Model

### PostgreSQL Row-Level Security (RLS)

TTwatch enforces tenant isolation at the database level using PostgreSQL RLS. Every request sets a session-level variable (`ttwatch.current_user_id`) that RLS policies use to filter rows automatically.

### Database Roles

| Role | Purpose | Capabilities |
|------|---------|-------------|
| `postgres` | Superuser, Alembic migrations | Full access, bypasses RLS |
| `ttwatch_app` | API server connections | CRUD on user-scoped tables (filtered by RLS), SELECT on shared tables |
| `ttwatch_worker` | Celery worker connections | Full access to all tables, bypasses RLS via explicit policy |

### RLS Policies

16 user-scoped tables have RLS enabled with two policies each:

**Policy: `user_isolation` (FOR ALL TO `ttwatch_app`)**
```sql
USING (user_id = current_setting('ttwatch.current_user_id')::uuid)
WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::uuid)
```

**Policy: `worker_bypass` (FOR ALL TO `ttwatch_worker`)**
```sql
USING (true) WITH CHECK (true)
```

Tables with RLS policies:
- `topics`, `sources`, `clusters`, `articles`
- `entities`, `entity_article_map`, `entity_cluster_map`
- `sentiment_history`, `saved_queries`, `briefings`
- `asset_mappings`, `investment_analyses`, `watchlist_items`, `price_alerts`, `correlation_signals`
- `llm_task_config`

### Tables WITHOUT RLS (Shared Data)

These tables contain shared reference data that all users can read:
- `ticker_reference` - Stock/ETF/crypto reference data
- `theme_etf_map` - Theme-to-ETF mappings
- `market_data_cache` - Current market prices (write-restricted to `ttwatch_worker`)
- `price_history` - Historical OHLCV data (write-restricted to `ttwatch_worker`)

### RLS Context Setting

**API side** (`services/api/app/deps.py`):
```python
validated_id = str(uuid.UUID(str(user.id)))
await db.execute(text(
    f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
))
```

**Worker side** (`services/worker/worker/rls.py`):
```python
validated_id = str(uuid.UUID(user_id))
with db_session() as session:
    session.execute(text(
        f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
    ))
```

Both paths validate the UUID via round-trip (`str(uuid.UUID(...))`) to guarantee only `[0-9a-f-]` characters reach the SQL string, mitigating injection risk. PostgreSQL `SET` does not accept bind parameters.

---

## 6. Database Schema

### Entity-Relationship Overview

```
users (1) ----< topics (1) ----< articles >---- clusters
  |                |                 |               |
  |                |                 |               |
  +----< api_keys  +----< sources    +----< entity_article_map
  |                |                 |
  +----< refresh_  +----< briefings  +---> entity_cluster_map
  |    tokens      |
  |                +----< saved_queries
  |                |
  |                +----< clusters ----< sentiment_history
  |                |
  |                +----< entities ----< asset_mappings ----> ticker_reference
  |
  +----< watchlist_items
  |
  +----< price_alerts
  |
  +----< investment_analyses ----> market_data_cache
  |
  +----< correlation_signals
  |
  +----< llm_task_config
```

### Table Definitions

#### `users`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK, default `uuid4` | |
| `email` | TEXT | UNIQUE, NOT NULL | |
| `display_name` | TEXT | NOT NULL | |
| `password_hash` | TEXT | NOT NULL | Argon2id hash |
| `is_active` | BOOLEAN | default `true` | |
| `is_admin` | BOOLEAN | default `false` | |
| `max_topics` | INTEGER | default `10` | Per-user topic limit |
| `max_articles_per_topic` | INTEGER | default `5000` | Per-topic article limit |
| `max_api_keys` | INTEGER | default `5` | Per-user API key limit |
| `created_at` | TIMESTAMPTZ | default `now()` | |
| `last_login_at` | TIMESTAMPTZ | | Updated on each login |

#### `api_keys`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `key_prefix` | TEXT | NOT NULL | First 14 chars (e.g., `tw_live_abc123`) |
| `key_hash` | TEXT | NOT NULL | SHA-256 of full key |
| `label` | TEXT | default `"default"` | User-assigned label |
| `scopes` | JSONB | default `["read","write","search"]` | Permission scopes |
| `rate_limit_per_minute` | INTEGER | default `60` | Per-key rate limit |
| `is_active` | BOOLEAN | default `true` | |
| `last_used_at` | TIMESTAMPTZ | | |
| `created_at` | TIMESTAMPTZ | | |
| `expires_at` | TIMESTAMPTZ | | Optional expiration |

#### `refresh_tokens`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `token_hash` | TEXT | NOT NULL | SHA-256 of raw token |
| `device_info` | TEXT | | Optional device identifier |
| `expires_at` | TIMESTAMPTZ | NOT NULL | 30 days from creation |
| `created_at` | TIMESTAMPTZ | | |

#### `topics`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `name` | TEXT | NOT NULL, UNIQUE(user_id, name) | |
| `icon` | TEXT | | Optional emoji/icon |
| `config` | JSONB | default `{}` | Stores `search_queries`, `search_terms` |
| `refresh_interval_minutes` | INTEGER | default `120` | |
| `last_refreshed_at` | TIMESTAMPTZ | | |
| `next_refresh_at` | TIMESTAMPTZ | | |
| `created_at` | TIMESTAMPTZ | | |
| `updated_at` | TIMESTAMPTZ | | Auto-updated |

**`config` JSONB structure:**
```json
{
  "search_queries": ["query1", "query2", ...],
  "search_terms": ["additional term 1", ...]
}
```

#### `sources`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `name` | TEXT | NOT NULL | |
| `url` | TEXT | NOT NULL, UNIQUE(user_id, topic_id, url) | |
| `source_type` | TEXT | default `"rss"` | |
| `enabled` | BOOLEAN | default `true` | |
| `is_builtin` | BOOLEAN | default `false` | |
| `config` | JSONB | default `{}` | |

#### `clusters`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `keyword` | TEXT | NOT NULL | LLM-generated 2-4 word label |
| `color` | TEXT | | Hex color from 15-color palette |
| `article_count` | INTEGER | default `0` | |
| `trend_score` | FLOAT | default `0` | Weighted recent activity |
| `velocity` | TEXT | | `surging`/`rising`/`steady`/`declining` |
| `created_at` | TIMESTAMPTZ | | |
| `updated_at` | TIMESTAMPTZ | | |

#### `articles`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | Also used as Qdrant point ID |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `url` | TEXT | NOT NULL, UNIQUE(user_id, topic_id, url) | |
| `title` | TEXT | NOT NULL | |
| `source_name` | TEXT | | Search engine name |
| `source_url` | TEXT | | |
| `published_at` | TIMESTAMPTZ | | From trafilatura metadata |
| `ingested_at` | TIMESTAMPTZ | default `now()` | |
| `content_hash` | TEXT | | SHA-256 of extracted text |
| `raw_storage_key` | TEXT | | MinIO path: `{user_id}/{topic_id}/{hash}.txt` |
| `summary` | TEXT | | LLM-generated 2-sentence summary |
| `sentiment_score` | FLOAT | | -1.0 (bearish) to 1.0 (bullish) |
| `relevance_score` | FLOAT | | 0.0 to 1.0 (threshold: 0.3) |
| `key_quotes` | JSONB | default `[]` | |
| `cluster_id` | UUID | FK `clusters.id` ON DELETE SET NULL | |
| `embedding_id` | TEXT | | Qdrant point ID (= article UUID) |
| `is_duplicate` | BOOLEAN | default `false` | Set by semantic dedup |
| `duplicate_of` | UUID | FK `articles.id` | Reference to original |

#### `entities`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `name` | TEXT | NOT NULL, UNIQUE(user_id, name, type, topic_id) | |
| `type` | TEXT | NOT NULL | `person`/`org`/`product`/`location`/`event`/`technology` |
| `first_seen` | TIMESTAMPTZ | default `now()` | Used for "new entity" detection in briefings |

#### `entity_article_map`

| Column | Type | Constraints |
|--------|------|------------|
| `entity_id` | UUID | PK, FK `entities.id` ON DELETE CASCADE |
| `article_id` | UUID | PK, FK `articles.id` ON DELETE CASCADE |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE |

#### `entity_cluster_map`

| Column | Type | Constraints |
|--------|------|------------|
| `entity_id` | UUID | PK, FK `entities.id` ON DELETE CASCADE |
| `cluster_id` | UUID | PK, FK `clusters.id` ON DELETE CASCADE |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE |

#### `sentiment_history`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | BIGINT | PK, autoincrement | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `cluster_id` | UUID | FK `clusters.id` ON DELETE SET NULL | Nullified before cluster deletion |
| `cluster_keyword` | TEXT | | Preserved text label (recluster-proof) |
| `period_start` | DATE | NOT NULL, UNIQUE(user_id, cluster_id, period_start) | |
| `avg_sentiment` | FLOAT | | Average of article sentiment_scores |
| `article_count` | INTEGER | | |

#### `saved_queries`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `query_text` | TEXT | NOT NULL | |
| `schedule` | TEXT | default `"on_refresh"` | |
| `last_run` | TIMESTAMPTZ | | |
| `last_result_count` | INTEGER | default `0` | |
| `created_at` | TIMESTAMPTZ | | |

#### `briefings`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `generated_at` | TIMESTAMPTZ | default `now()` | |
| `summary` | TEXT | | 2-3 paragraph executive summary |
| `highlights` | JSONB | default `[]` | Key developments list |
| `new_entities` | JSONB | default `[]` | Entities first seen in last 24h |
| `watch_items` | JSONB | default `[]` | Things to monitor |
| `coverage_gaps` | JSONB | default `[]` | Identified uncovered areas |
| `input_tokens` | INTEGER | | LLM usage tracking |
| `output_tokens` | INTEGER | | |
| `model_used` | TEXT | | |

#### `ticker_reference` (shared, no RLS)

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `symbol` | TEXT | NOT NULL, UNIQUE(symbol, exchange) | e.g., `AAPL` |
| `name` | TEXT | NOT NULL | e.g., `Apple Inc.` |
| `exchange` | TEXT | | e.g., `NASDAQ` |
| `asset_type` | TEXT | NOT NULL | `equity`/`etf`/`crypto` |
| `sector` | TEXT | | |
| `industry` | TEXT | | |
| `market_cap_tier` | TEXT | | |
| `is_active` | BOOLEAN | default `true` | |
| `metadata` | JSONB | default `{}` | |
| `updated_at` | TIMESTAMPTZ | | |

#### `theme_etf_map` (shared, no RLS)

| Column | Type | Constraints |
|--------|------|------------|
| `id` | UUID | PK |
| `theme` | TEXT | NOT NULL, UNIQUE(theme, etf_symbol) |
| `etf_symbol` | TEXT | NOT NULL |
| `relevance_score` | FLOAT | default `1.0` |

#### `market_data_cache` (shared, no RLS, worker-write-only)

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `symbol` | TEXT | NOT NULL | |
| `asset_type` | TEXT | NOT NULL | |
| `price` | NUMERIC | | |
| `price_change_pct` | NUMERIC | | |
| `volume` | BIGINT | | |
| `market_cap` | NUMERIC | | |
| `pe_ratio` | NUMERIC | | |
| `eps` | NUMERIC | | |
| `dividend_yield` | NUMERIC | | |
| `beta` | NUMERIC | | |
| `fifty_two_week_high` | NUMERIC | | |
| `fifty_two_week_low` | NUMERIC | | |
| `data_source` | TEXT | | `yfinance` or `coingecko` |
| `is_stale` | BOOLEAN | default `false` | |
| `fetched_at` | TIMESTAMPTZ | | |

#### `price_history` (shared, no RLS, worker-write-only)

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `symbol` | TEXT | PK (composite) | |
| `trade_date` | DATE | PK (composite) | |
| `open` | NUMERIC | | |
| `high` | NUMERIC | | |
| `low` | NUMERIC | | |
| `close` | NUMERIC | | |
| `adj_close` | NUMERIC | | |
| `volume` | BIGINT | | |
| `source` | TEXT | default `"yfinance"` | |

#### `asset_mappings`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `entity_id` | UUID | FK `entities.id` ON DELETE CASCADE | |
| `ticker_ref_id` | UUID | FK `ticker_reference.id` | |
| `entity_name` | TEXT | NOT NULL | |
| `resolved_symbol` | TEXT | | e.g., `TSLA` |
| `resolution_method` | TEXT | | `reference_lookup` or `llm_inference` |
| `confidence` | FLOAT | default `0` | 0.0-1.0 |
| `is_verified` | BOOLEAN | default `false` | |
| `created_at` | TIMESTAMPTZ | | |
| `updated_at` | TIMESTAMPTZ | | |

Unique constraint: `(user_id, entity_id, resolved_symbol)`

#### `investment_analyses`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `analysis_scope` | TEXT | NOT NULL, CHECK `IN ('asset','cluster','topic')` | |
| `scope_ref_id` | UUID | | Reference to asset/cluster/topic |
| `symbol` | TEXT | | |
| `analysis_text` | TEXT | NOT NULL | LLM-generated analysis |
| `recommendation` | TEXT | | |
| `confidence` | FLOAT | | |
| `key_signals` | JSONB | default `[]` | |
| `risk_factors` | JSONB | default `[]` | |
| `articles_considered` | INTEGER | default `0` | |
| `market_data_cache_id` | UUID | FK `market_data_cache.id` ON DELETE SET NULL | |
| `sentiment_score` | FLOAT | | |
| `technical_signals` | JSONB | default `{}` | |
| `input_tokens` | INTEGER | | |
| `output_tokens` | INTEGER | | |
| `model_used` | TEXT | | |
| `generated_at` | TIMESTAMPTZ | | |
| `analysis_frequency` | TEXT | default `"daily"` | |
| `next_analysis_at` | TIMESTAMPTZ | | |

#### `watchlist_items`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE, UNIQUE(user_id, symbol) | |
| `symbol` | TEXT | NOT NULL | |
| `asset_type` | TEXT | NOT NULL | |
| `added_reason` | TEXT | | |
| `topic_id` | UUID | FK `topics.id` ON DELETE SET NULL | |
| `notes` | TEXT | | |
| `target_price` | NUMERIC | | |
| `stop_loss` | NUMERIC | | |
| `created_at` | TIMESTAMPTZ | | |

#### `price_alerts`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `symbol` | TEXT | NOT NULL | |
| `condition` | TEXT | NOT NULL, CHECK `IN ('above','below','crosses_above','crosses_below')` | |
| `threshold` | NUMERIC | NOT NULL | |
| `last_known_price` | NUMERIC | | |
| `is_active` | BOOLEAN | default `true` | |
| `triggered_at` | TIMESTAMPTZ | | |
| `created_at` | TIMESTAMPTZ | | |

#### `correlation_signals`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `topic_id` | UUID | FK `topics.id` ON DELETE CASCADE | |
| `cluster_id` | UUID | FK `clusters.id` ON DELETE SET NULL | |
| `symbol` | TEXT | NOT NULL | |
| `signal_type` | TEXT | NOT NULL | See signal types below |
| `signal_strength` | FLOAT | | 0.0-1.0 |
| `description` | TEXT | | |
| `detected_at` | TIMESTAMPTZ | | |

**Signal types:**
- `sentiment_price_divergence_bullish` - Positive sentiment + falling price
- `sentiment_price_divergence_bearish` - Negative sentiment + rising price
- `momentum_confirmation_bullish` - Positive sentiment + rising price
- `momentum_confirmation_bearish` - Negative sentiment + falling price

#### `llm_task_config`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE | |
| `task_category` | TEXT | NOT NULL, UNIQUE(user_id, task_category) | One of 10 task categories |
| `model_target` | TEXT | default `"auto"`, NOT NULL | `primary`, `fast`, or `auto` |
| `created_at` | TIMESTAMPTZ | | |
| `updated_at` | TIMESTAMPTZ | | |

**Task categories:** `summarization`, `sentiment`, `relevance`, `entity_extraction`, `ticker_resolution`, `briefing`, `investment_analysis`, `coverage_gaps`, `search_planning`, `correlation`

### Migrations

| Migration | Description |
|-----------|-------------|
| `001_create_users_and_auth.py` | `users`, `api_keys`, `refresh_tokens` with indexes |
| `002_create_intelligence_tables.py` | `topics`, `sources`, `clusters`, `articles`, `entities`, `entity_article_map`, `entity_cluster_map`, `sentiment_history`, `saved_queries`, `briefings` |
| `003_create_investment_tables.py` | `ticker_reference`, `theme_etf_map`, `market_data_cache`, `price_history`, `asset_mappings`, `investment_analyses`, `watchlist_items`, `price_alerts`, `correlation_signals` |
| `004_add_rls_policies.py` | Enables RLS on 15 user-scoped tables, creates `user_isolation` + `worker_bypass` policies, `FORCE ROW LEVEL SECURITY` |
| `005_grants_app_role.py` | Grants for `ttwatch_app`: CRUD on user tables, SELECT on shared tables |
| `006_grants_worker_role.py` | Grants for `ttwatch_worker`: ALL on all tables, USAGE on sequences, ALTER DEFAULT PRIVILEGES |
| `007_create_llm_task_config.py` | `llm_task_config` table with RLS policies and grants for both app and worker roles |

---

## 7. Authentication and Authorization

### Authentication Mechanisms

TTwatch supports two authentication methods:

#### 1. JWT Bearer Token

- **Algorithm**: HS256
- **Access Token**: 15-minute expiry, contains `sub` (user UUID), `exp`, `iat`
- **Refresh Token**: 30-day expiry, stored as SHA-256 hash in `refresh_tokens` table
- **Rotation**: On refresh, the old token is deleted and a new one issued (prevents reuse)
- **Cap**: Maximum 10 active (unexpired) refresh tokens per user; oldest are pruned on login

#### 2. API Key (Header: `X-API-Key`)

- **Format**: `tw_live_` prefix + short ID + random suffix (e.g., `tw_live_abc123def456...`)
- **Storage**: Only the prefix (first 14 chars) and SHA-256 hash of the full key are stored
- **Scopes**: `["read", "write", "search"]` (configurable per key)
- **Rate Limit**: 60 requests/minute per key (configurable per key)
- **Lookup**: Match by prefix, then verify full key hash

### Auth Flow

**Registration** (`POST /auth/register`):
1. Validate email format and password strength (10+ chars, uppercase, lowercase, digit)
2. Hash password with Argon2id (time_cost=3, memory_cost=64MB, parallelism=4)
3. Create user record (handles concurrent duplicate email gracefully via UniqueViolationError)
4. Generate access + refresh token pair

**Login** (`POST /auth/login`):
1. Look up user by email
2. Verify password with Argon2id (auto-rehash if parameters changed)
3. Update `last_login_at`
4. Generate token pair
5. Cap active refresh tokens at 10 (delete oldest excess, only counting non-expired tokens)

**Token Refresh** (`POST /auth/refresh`):
1. Hash provided refresh token with SHA-256
2. Look up matching unexpired token
3. Delete old token (rotation)
4. Issue new access + refresh token pair

**Logout** (`POST /auth/logout`):
1. Hash provided refresh token
2. Delete from database
3. Always return 200 (does not reveal token existence)
4. Access token expires naturally (15 min)

### Rate Limiting

Implemented via a Lua script executed atomically in Redis (`services/api/app/middleware/rate_limit.py`):

```lua
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end
if current > limit then return 0 end
return 1
```

- **Key format**: `ttwatch:rate:{user_id}:{endpoint}`
- **Default**: 60 requests per 60-second window per user per endpoint
- **Applied to**: All authenticated API routers via `rate_limit_dependency`
- **Implementation**: FastAPI caches dependencies per request, so `get_current_user` runs once even when both `rate_limit_dependency` and the router depend on it

### RLS Integration

After authentication, `get_current_user` sets the PostgreSQL RLS context variable:

```python
validated_id = str(uuid.UUID(str(user.id)))
await db.execute(text(
    f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
))
```

`SET LOCAL` scopes the variable to the current transaction, ensuring automatic cleanup without explicit unset.

---

## 8. API Reference

Base URL: `http://localhost:8080`

All authenticated endpoints require either:
- `Authorization: Bearer <jwt_access_token>` header, OR
- `X-API-Key: <api_key>` header

### Health

#### `GET /health`
Basic health check. No authentication required.
**Response**: `{"status": "ok"}`

#### `GET /health/services`
Extended health check with service status (PostgreSQL, Redis, Qdrant, MinIO, vLLM, vLLM-Fast, Embedder, SearXNG).

### Auth

#### `POST /auth/register`
**Body**: `{"email": "...", "display_name": "...", "password": "..."}`
**Response**: `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}`
**Errors**: 409 (email exists), 422 (validation: password <10 chars, missing uppercase/lowercase/digit)

#### `POST /auth/login`
**Body**: `{"email": "...", "password": "..."}`
**Response**: Same as register
**Errors**: 401 (invalid credentials)

#### `POST /auth/refresh`
**Body**: `{"refresh_token": "..."}`
**Response**: New token pair (old refresh token is invalidated)
**Errors**: 401 (invalid/expired token)

#### `POST /auth/logout`
**Body**: `{"refresh_token": "..."}`
**Response**: `{"status": "logged_out"}`

### Topics

#### `GET /api/topics`
List all topics for the authenticated user.
**Response**: `TopicResponse[]` (id, name, icon, config, refresh_interval_minutes, timestamps)

#### `POST /api/topics`
Create a new topic. Enforces per-user topic limit (`max_topics`, default 10). Automatically dispatches `generate_search_queries` and sets search status to "searching".
**Body**: `{"name": "...", "icon": "...", "config": {...}, "refresh_interval_minutes": 120}`
**Response**: 201 `TopicResponse`
**Errors**: 403 (topic limit reached)

#### `GET /api/topics/{topic_id}`
Get a single topic by ID.
**Errors**: 404

#### `PUT /api/topics/{topic_id}`
Update a topic. Supports partial updates via `model_fields_set`. Config is merged (not replaced).
**Body**: Any subset of `{"name", "icon", "config", "refresh_interval_minutes"}`
**Errors**: 404

#### `DELETE /api/topics/{topic_id}`
Delete a topic and all associated data (cascades). Qdrant vectors cleaned up by daily `cleanup_orphaned_qdrant_points` task.
**Response**: 204
**Errors**: 404

#### `POST /api/topics/{topic_id}/search`
Manually trigger a new search. Rate-limited to once per 5 minutes (Redis lock key `ttwatch:search_lock:{topic_id}`, TTL 300s).
**Response**: 202 `{"status": "search_dispatched", "topic_id": "..."}`
**Errors**: 404, 429 (cooldown)

#### `POST /api/topics/{topic_id}/search/cancel`
Cancel an in-progress search.
**Response**: `{"status": "cancelled"}`

#### `GET /api/topics/{topic_id}/search-status`
Get current search status from Redis cache.
**Response**: `{"status": "generating_queries"|"searching"|"processing"|"completed"|"error"|"idle", ...}`

#### `GET /api/topics/{topic_id}/processing-status`
Get detailed processing progress for a topic.
**Response**: `{"phase": "...", "total_articles": N, "embedded": N, "summarized": N, ...}`

#### `GET /api/topics/{topic_id}/clusters`
List clusters for a topic, ordered by `trend_score` descending.
**Response**: `ClusterResponse[]` (id, keyword, color, article_count, trend_score, velocity)

### Clusters

#### `GET /api/clusters/{cluster_id}`
Get a single cluster.

#### `GET /api/clusters/{cluster_id}/articles`
List articles belonging to a cluster.
**Query params**: `limit` (default 50, max 200), `offset` (default 0)

### Articles

#### `GET /api/topics/{topic_id}/articles`
List articles with filtering.
**Query params**: `cluster_id`, `is_duplicate` (bool), `published_after` (ISO datetime), `published_before` (ISO datetime), `min_relevance` (float, default 0.3), `limit` (default 50, max 200), `offset` (default 0)

#### `GET /api/articles/{article_id}`
Get a single article with full details.

#### `GET /api/articles/{article_id}/entities`
List entities extracted from an article.

### Search

#### `POST /api/search`
Semantic search via Qdrant vector similarity.
**Body**: `{"query": "...", "topic_id": "...", "limit": 20}`
**Response**: Array of articles with similarity scores.

### Briefings

#### `GET /api/topics/{topic_id}/briefings`
List briefings for a topic.

#### `GET /api/briefings/{briefing_id}`
Get a single briefing with full content.

#### `POST /api/topics/{topic_id}/briefings/generate`
Manually trigger briefing generation for a topic.
**Response**: 202 `{"task_id": "...", "status": "..."}`

### Entities

#### `GET /api/topics/{topic_id}/entities`
List entities for a topic.
**Query params**: `type` (filter by entity type), `limit`, `offset`

#### `GET /api/topics/{topic_id}/entity-graph`
Get entity co-occurrence graph for visualization.
**Query params**: `min_articles` (default 1), `min_cooccurrence` (default 2)
**Response**: `{"entities": [...], "edges": [...]}`
Edges are computed from entities sharing articles (co-occurrence via `entity_article_map`).

#### `GET /api/entities/{entity_id}`
Get a single entity.

#### `GET /api/entities/{entity_id}/articles`
List articles mentioning an entity.

### Sentiment

#### `GET /api/topics/{topic_id}/sentiment`
Latest sentiment scores per cluster for a topic.

#### `GET /api/topics/{topic_id}/sentiment/history`
Sentiment history timeline for a cluster.
**Query params**: `cluster_keyword` (optional), `limit` (default 90, max 365)
Uses `cluster_keyword` for lookup, making it resilient to cluster ID changes from reclustering.

### Sources

#### `GET /api/topics/{topic_id}/sources`
List sources for a topic.

#### `POST /api/topics/{topic_id}/sources`
Add a custom source.
**Body**: `{"name": "...", "url": "...", "source_type": "rss", "enabled": true, "config": {}}`
**Errors**: 409 (duplicate URL per user+topic)

#### `PUT /api/sources/{source_id}`
Update a source.

#### `DELETE /api/sources/{source_id}`
Delete a source.

### Saved Queries

#### `GET /api/topics/{topic_id}/queries`
List saved queries for a topic.

#### `POST /api/topics/{topic_id}/queries`
Create a saved query.
**Body**: `{"query_text": "...", "schedule": "on_refresh"}`

#### `DELETE /api/queries/{query_id}`
Delete a saved query.

### Investment

#### `GET /api/topics/{topic_id}/watchlist`
List user's watchlist items for a topic.

#### `POST /api/topics/{topic_id}/watchlist`
Add a symbol to watchlist.
**Body**: `{"symbol": "...", "asset_type": "equity|etf|crypto", "notes": "...", "target_price": 150.00, "stop_loss": 120.00}`
**Errors**: 409 (symbol already in watchlist)

#### `DELETE /api/watchlist/{item_id}`
Remove from watchlist.

#### `GET /api/topics/{topic_id}/analyses`
List investment analyses for a topic.

#### `GET /api/topics/{topic_id}/correlation-signals`
List correlation signals for a topic (limit 50).

#### `POST /api/price-alerts`
Create a price alert.
**Body**: `{"symbol": "...", "condition": "above|below|crosses_above|crosses_below", "threshold": 150.00}`

#### `GET /api/price-alerts`
List user's price alerts.

#### `DELETE /api/price-alerts/{alert_id}`
Delete a price alert.

### Market Data

#### `GET /api/market-data/{symbol}`
Get current market data for a symbol from cache.

#### `GET /api/market-data/{symbol}/history`
Get price history for a symbol.
**Query params**: `limit` (default 90, max 365)

### Users

#### `GET /api/me`
Get current user profile.

#### `PUT /api/me`
Update profile (display_name).

#### `GET /api/me/api-keys`
List user's API keys (shows prefix and label, not the full key).

#### `POST /api/me/api-keys`
Create a new API key. Returns the full key exactly once. Enforces `max_api_keys` (default 5).
**Body**: `{"label": "...", "scopes": ["read","write","search"]}`
**Response**: `{"key": "tw_live_...", "prefix": "tw_live_...", "label": "..."}`

#### `DELETE /api/me/api-keys/{key_id}`
Revoke an API key.

### Models

#### `GET /api/models/status`
Get LLM model health status for primary and fast models.
**Response**: `{"models": [...], "gpu_mode": "local"|"cloud", "provider": "..."}`

#### `GET /api/models/task-routing`
Get current task routing configuration (10 task categories with model assignments).
**Response**: `{"entries": [{"task_category": "...", "model_target": "fast"|"primary"|"auto", ...}]}`

#### `PUT /api/models/task-routing`
Update task routing for one or more categories.
**Body**: `{"changes": [{"task_category": "briefing", "model_target": "primary"}]}`

### Admin

#### `GET /api/admin/versions`
Get service version status (current vs. latest available). Admin only (403 for non-admins).
Cached for 24 hours in Redis.

#### `POST /api/admin/versions/check`
Trigger a version check against upstream registries (GitHub, DockerHub, HuggingFace). Admin only.

### WebSocket

#### `WS /ws`
Real-time updates connection.

**Protocol**:
1. Client connects to `ws://host:8080/ws`
2. Server accepts and waits for auth message (10s timeout)
3. Client sends: `{"type": "auth", "token": "<jwt_access_token>"}`
4. Server verifies JWT and responds: `{"type": "connected", "user_id": "..."}`
5. Server sends `{"type": "ping"}` every 30s
6. Client should respond with `{"type": "pong"}` (connections without pong for >90s are terminated)

**Server-pushed events**:
- `{"type": "price_alert", "symbol": "...", "condition": "...", ...}` - Triggered price alert
- `{"type": "search_completed", "topic_id": "...", "articles_found": N}` - Search completion
- `{"type": "search_progress", "topic_id": "...", "status": "...", ...}` - Search progress update

**Close codes**:
- `4001` - Auth timeout or invalid credentials
- `4002` - Heartbeat timeout (no pong for 90s)

---

## 9. Background Processing

### Worker Architecture

TTwatch uses a dual-pool Celery worker architecture:

#### Worker-IO (gevent pool)
- **Container**: `worker-io`
- **Pool**: gevent with concurrency=32
- **Queue**: `ttwatch:default`
- **Tasks**: I/O-bound operations (HTTP requests, database queries, LLM API calls)
- **Concurrency model**: Cooperative multitasking via greenlets; psycogreen patches psycopg2 for gevent compatibility

#### Worker-CPU (prefork pool)
- **Container**: `worker-cpu`
- **Pool**: prefork with concurrency=2
- **Queue**: `ttwatch:compute`
- **Tasks**: CPU-bound operations (UMAP, HDBSCAN, LLM-heavy generation)
- **Memory model**: Process-based isolation, no GIL contention

#### Scheduler (Celery Beat)
- **Container**: `scheduler`
- **Role**: Dispatches periodic tasks on cron-like schedules

### Queue Routing

Tasks are routed to queues by name in `services/worker/worker/celeryconfig.py`:

```python
app.conf.task_routes = {
    "recluster_topic":                {"queue": "ttwatch:compute"},
    "update_trends":                  {"queue": "ttwatch:compute"},
    "compute_sentiment_history":      {"queue": "ttwatch:compute"},
    "detect_coverage_gaps":           {"queue": "ttwatch:compute"},
    "generate_briefing":              {"queue": "ttwatch:compute"},
    "generate_investment_analyses":   {"queue": "ttwatch:compute"},
    "detect_correlation_signals":     {"queue": "ttwatch:compute"},
}
```

All other tasks default to `ttwatch:default` (IO pool).

### LLM Task Routing

Workers use the `llm_router` module (`services/worker/worker/llm_router.py`) to route LLM calls to the appropriate model:

```python
def get_llm_for_task(session, user_id: str, task_category: str) -> SyncLLMClient:
```

1. Queries `llm_task_config` table for user-specific routing
2. Falls back to hardcoded defaults (all tasks default to `fast` model)
3. Returns `_fast` (Qwen3-8B-AWQ with thinking disabled) or `_primary` (Qwen3-32B-AWQ)
4. For `auto` target: returns fast client (which itself falls back to primary if unavailable)

### Task Registry

20 task modules, 24+ named tasks:

| Task Name | Module | Queue | Retries | Description |
|-----------|--------|-------|---------|-------------|
| `run_topic_search` | `search` | default | 0 | Query SearXNG, dispatch ingestion |
| `detect_stalled_pipelines` | `search` | default | 0 | Force-complete stuck processing pipelines |
| `generate_search_queries` | `search_plan` | default | 2 | LLM decomposes topic into 3-6 queries |
| `ingest_article` | `ingest` | default | 2 | Fetch, extract, dedup, store, fan-out |
| `embed_article` | `embed` | default | 3 | Generate embedding, upsert Qdrant, semantic dedup |
| `summarize_article` | `summarize` | default | 3 | LLM 2-sentence summary |
| `extract_entities` | `entities` | default | 3 | LLM NER, create entities + mappings |
| `classify_sentiment` | `sentiment` | default | 3 | LLM sentiment score (-1.0 to 1.0) |
| `score_relevance` | `relevance` | default | 3 | LLM relevance score (0.0 to 1.0) |
| `resolve_entity_ticker` | `resolve_ticker` | default | 2 | Reference lookup + LLM ticker resolution |
| `recluster_topic` | `cluster` | compute | 0 | UMAP + HDBSCAN clustering |
| `update_trends` | `trends` | compute | 0 | Trend scores + velocity labels |
| `generate_briefing` | `briefing` | compute | 2 | Hierarchical briefing generation |
| `compute_sentiment_history` | `sentiment_agg` | compute | 0 | Daily sentiment aggregation |
| `detect_coverage_gaps` | `coverage_gaps` | compute | 0 | LLM gap detection |
| `generate_investment_analyses` | `investment_analysis` | compute | 2 | Per-asset LLM analysis |
| `detect_correlation_signals` | `correlation_signals` | compute | 2 | Sentiment-price correlation |
| `check_price_alerts` | `price_alerts` | default | 0 | Check thresholds, publish via pub/sub |
| `fetch_market_data` | `maintenance` | default | 2 | yfinance/CoinGecko data fetch |
| `cleanup_stale_market_data` | `maintenance` | default | 0 | 30-day retention cleanup |
| `cleanup_stale_snapshots` | `maintenance` | default | 0 | 10 briefings + 90d analyses retention |
| `cleanup_expired_refresh_tokens` | `maintenance` | default | 0 | Prune expired tokens |
| `cleanup_orphaned_qdrant_points` | `maintenance` | default | 0 | Remove vectors without PG articles |
| `check_service_versions` | `version_check` | default | 0 | Check upstream registries |

### Beat Schedule (Periodic Tasks)

| Schedule | Task | Frequency |
|----------|------|-----------|
| `schedule-searches` | `schedule_searches` | Every 2 hours (`:00`) |
| `schedule-reclustering` | `schedule_reclustering` | Every 2 hours (`:00`) |
| `schedule-trend-updates` | `schedule_trend_updates` | Every hour (`:00`) |
| `schedule-briefings` | `schedule_briefings` | Every 6 hours (`:00`) |
| `schedule-coverage-gaps` | `schedule_coverage_gaps` | Every 12 hours (`:00`) |
| `schedule-sentiment-history` | `schedule_sentiment_history` | Every 2 hours (`:00`) |
| `refresh-market-data` | `refresh_market_data` | Every 30 minutes |
| `schedule-investment-analyses` | `schedule_investment_analyses` | Daily at 06:00 |
| `schedule-correlation-signals` | `schedule_correlation_signals` | Every 4 hours (`:00`) |
| `check-price-alerts` | `check_price_alerts` | Every 15 minutes |
| `cleanup-stale-market-data` | `cleanup_stale_market_data` | Daily at 03:00 |
| `cleanup-stale-snapshots` | `cleanup_stale_snapshots` | Daily at 03:30 |
| `cleanup-expired-refresh-tokens` | `cleanup_expired_refresh_tokens` | Daily at 02:30 |
| `cleanup-orphaned-qdrant` | `cleanup_orphaned_qdrant_points` | Daily at 04:00 |
| `check-service-versions` | `check_service_versions` | Daily at 06:30 |
| `detect-stalled-pipelines` | `detect_stalled_pipelines` | Every 2 minutes |

### Periodic Task Dispatch Pattern

The `schedule_*` tasks in `services/worker/worker/tasks/periodic.py` query all active users and topics, then dispatch individual tasks per user/topic pair. For example, `schedule_searches` finds all active user/topic pairs and dispatches `run_topic_search` for each. The `refresh_market_data` task discovers symbols from both `watchlist_items` and `asset_mappings` (auto-resolved from entities) to ensure market data is available for investment analyses.

### Worker Database Access

Workers use synchronous SQLAlchemy (`services/worker/worker/db.py`):

```python
engine = create_engine(
    DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://"),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
```

Gevent compatibility is achieved via psycogreen patching:
```python
from psycogreen.gevent import patch_psycopg
patch_psycopg()
```

The `db_session()` context manager yields a scoped session with automatic commit/rollback.

### Worker RLS Context

The `@with_rls_context` decorator (`services/worker/worker/rls.py`) handles both bound (`bind=True`) and unbound tasks:

1. Detects if the first argument is a `Task` instance (bound task)
2. Extracts `user_id` from the appropriate position
3. Opens a database session via `db_session()`
4. Sets `SET LOCAL ttwatch.current_user_id = '{validated_id}'`
5. Passes the session as `session=` keyword argument to the wrapped function

### Pipeline Stall Detection

The `detect_stalled_pipelines` task (`services/worker/worker/tasks/search.py`) runs every 2 minutes as a safety net:

1. Scans all `ttwatch:search_status:*` Redis keys
2. Identifies pipelines stuck in `processing` status for over 5 minutes (`_STALL_TIMEOUT`)
3. If clustering hasn't been dispatched yet, dispatches `recluster_topic`
4. If clustering was already dispatched but didn't complete, forces the pipeline to `completed` status
5. Publishes `search_completed` event via Redis pub/sub

---

## 10. Core Processing Pipeline

### Pipeline Overview

```
Topic Creation
      |
      v
[generate_search_queries] --- LLM decomposes topic name into 3-6 search queries
      |
      v
[run_topic_search] --- Query SearXNG with each query, deduplicate URLs
      |
      v (for each unique URL)
[ingest_article] --- Fetch, extract, store, 3-layer dedup
      |
      +---> [embed_article]         --- Vectorize + Qdrant upsert + semantic dedup (countdown=1)
      +---> [summarize_article]     --- LLM 2-sentence summary (countdown=1)
      +---> [classify_sentiment]    --- LLM sentiment classification (countdown=3)
      +---> [score_relevance]       --- LLM topic relevance scoring (countdown=6)
      +---> [extract_entities]      --- LLM NER + entity-article mapping (countdown=10)
                +---> [resolve_entity_ticker]  --- Ticker symbol resolution


Periodic (every 2h):
[recluster_topic] --- UMAP + HDBSCAN re-clustering
[update_trends]   --- Trend scores + velocity labels
[compute_sentiment_history] --- Daily sentiment snapshots

Periodic (every 6h):
[generate_briefing] --- Hierarchical summarization

Periodic (every 12h):
[detect_coverage_gaps] --- LLM gap analysis

Safety (every 2min):
[detect_stalled_pipelines] --- Force-complete stuck pipelines
```

### Step 1: Search Query Generation

**File**: `services/worker/worker/tasks/search_plan.py`
**Task**: `generate_search_queries`

When a topic is created, the system uses the LLM to decompose the topic name into 3-6 targeted search queries:

```python
result = _llm.generate_json([
    {"role": "system", "content": (
        "Given a research topic, generate 3-6 specific search queries "
        "that would find the most relevant and recent news articles. "
        "Return JSON: {\"queries\": [\"query1\", \"query2\", ...]}"
    )},
    {"role": "user", "content": f"Topic: {topic.name}"},
])
```

The generated queries are stored in `topic.config["search_queries"]` and then `run_topic_search` is dispatched.

### Step 2: SearXNG Search

**File**: `services/worker/worker/tasks/search.py`
**Task**: `run_topic_search`

For each search query (LLM-generated + user-configured `search_terms`):
1. Query SearXNG at `/search?q={query}&format=json`
2. Collect results, deduplicating by URL within the batch
3. Track per-query progress in Redis (`queries_total`, `queries_completed`)
4. Dispatch `ingest_article` for each unique URL
5. Set processing counters in Redis for progress tracking
6. Transition search status through phases: `searching` -> `processing` -> `completed`
7. Publish progress events to `ttwatch:search:progress` pub/sub channel

### Step 3: Article Ingestion

**File**: `services/worker/worker/tasks/ingest.py`
**Task**: `ingest_article` (bind=True, max_retries=2)

Three-layer deduplication:

**Layer 1 -- URL Dedup (Redis SET)**:
```python
dedup_key = f"ttwatch:dedup:urls:{user_id}"
if _dedup_redis.sismember(dedup_key, url):
    return {"status": "duplicate", "layer": "url"}
```
O(1) lookup in Redis SET per user. Prevents refetching known URLs.

**Layer 2 -- Content Hash Dedup (PostgreSQL)**:
```python
content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
existing = session.execute(
    select(Article.id).where(
        Article.user_id == user_id,
        Article.topic_id == topic_id,
        Article.content_hash == content_hash,
    )
).scalar_one_or_none()
```
Catches mirror sites or syndicated content with identical text but different URLs.

**Layer 3 -- Semantic Dedup (Qdrant, in embed_article)**:
After embedding, searches for vectors with cosine similarity > 0.92 within the same user+topic scope. If found, marks the article as `is_duplicate=True` with `duplicate_of` pointing to the original.

**Content extraction** uses trafilatura with custom config:
```python
_traf_config = configparser.ConfigParser()
_traf_config.read_dict({"DEFAULT": {
    "DOWNLOAD_TIMEOUT": "10",
    "MAX_REDIRECTS": "2",
}})

extracted = trafilatura.extract(
    downloaded,
    include_comments=False,
    include_tables=True,
    favor_precision=True,
    output_format="txt",
)
```

Articles with fewer than 100 characters of extracted text are rejected. Title and `published_at` are extracted from document metadata via `trafilatura.extract_metadata()`.

**Raw storage**: Full extracted text is stored in MinIO at path `{user_id}/{topic_id}/{content_hash}.txt`.

**Skipped article tracking**: When an article is deduplicated or fails to fetch, `_track_skipped_article()` decrements the expected count and checks if the processing pipeline is effectively complete. This prevents pipeline stalls when most articles are duplicates.

**Fan-out**: After successful ingestion, 5 tasks are dispatched with staggered countdowns to ensure the ingest transaction commits first:
1. `embed_article` (countdown=1s)
2. `summarize_article` (countdown=1s)
3. `classify_sentiment` (countdown=3s)
4. `score_relevance` (countdown=6s)
5. `extract_entities` (countdown=10s)

### Step 4: Summarization

**File**: `services/worker/worker/tasks/summarize.py`
**Task**: `summarize_article`

Generates a concise 2-sentence summary using the LLM. Includes chain-of-thought cleanup: a regex strips any `<think>...</think>` blocks or reasoning preamble from the output:

```python
raw = _llm.generate([...])
# Strip CoT reasoning artifacts
raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
```

### Step 5: Embedding

**File**: `services/worker/worker/tasks/embed.py`
**Task**: `embed_article`

1. Fetches raw article text from MinIO
2. Creates embedding text: `title + "\n\n" + raw_text[:1500]` (1500 char limit balances quality with throughput)
3. Calls the embedding service (local Qwen3-Embedding-0.6B or cloud text-embedding-3-large)
4. Upserts to Qdrant with point ID = article UUID (critical contract: `recluster_topic` depends on this identity)
5. Payload includes `user_id`, `topic_id`, `title`, `source`, `ingested_at` for filtering
6. Performs Layer 3 semantic dedup (cosine > 0.92 threshold)
7. Increments Redis processing counter and auto-dispatches `recluster_topic` when embedded count reaches 80% of expected

### Step 6: Entity Extraction

**File**: `services/worker/worker/tasks/entities.py`
**Task**: `extract_entities`

LLM extracts up to 15 named entities per article:

```python
result = _llm.generate_json([
    {"role": "system", "content": (
        "Extract named entities from the article. Return JSON: "
        '{"entities": [{"name": "...", "type": "person|org|product|location|event|technology"}]}'
    )},
    {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
])
```

For each entity:
- Upsert to `entities` table (unique per user + name + type + topic)
- Create `entity_article_map` link
- For newly created `org`/`product`/`technology` entities, dispatch `resolve_entity_ticker`

### Step 7: Sentiment Classification

**File**: `services/worker/worker/tasks/sentiment.py`
**Task**: `classify_sentiment`

LLM classifies article sentiment on a -1.0 to 1.0 scale:
- -1.0: Very negative/bearish
- 0.0: Neutral
- 1.0: Very positive/bullish

Stored in `article.sentiment_score`.

### Step 8: Relevance Scoring

**File**: `services/worker/worker/tasks/relevance.py`
**Task**: `score_relevance`

LLM scores how relevant an article is to its parent topic on a 0.0-1.0 scale.

**Threshold**: `RELEVANCE_THRESHOLD = 0.3`

Articles below this threshold are excluded from clustering to prevent noise clusters.

### Step 9: Clustering

**File**: `services/worker/worker/tasks/cluster.py`
**Task**: `recluster_topic` (runs every 2 hours and auto-triggered after embedding)

**Two-phase Qdrant scroll**:
1. **Phase 1**: Scroll ALL points (no vectors) for sorting by `ingested_at`, cap at `MAX_CLUSTER_ARTICLES = 2000`
2. Filter out duplicates and low-relevance articles (below `RELEVANCE_THRESHOLD`)
3. **Phase 2**: Retrieve selected points WITH vectors

**Dimensionality reduction + clustering**:
```python
vectors = np.array([p.vector for p in points_with_vectors])
reduced = UMAP(n_components=20, metric="cosine", random_state=42).fit_transform(vectors)
labels = HDBSCAN(min_cluster_size=5, min_samples=3).fit_predict(reduced)
```

**Pre-deletion data preservation** (critical for data integrity):
Before deleting old clusters, the task:
1. Nullifies `sentiment_history.cluster_id` for old clusters (prevents CASCADE destruction)
2. Deletes `entity_cluster_map` entries for old clusters
3. Then deletes old clusters

**Cluster creation**:
For each HDBSCAN label (excluding noise label -1):
1. LLM generates a 2-4 word keyword from the top 10 article titles
2. Creates `Cluster` record with a color from a 15-color palette
3. Updates articles with `cluster_id`
4. Corrects `article_count` based on actual DB rows (not Qdrant points, which may include orphans)

Noise articles (label -1) have their `cluster_id` set to NULL.

### Step 10: Trend Analysis

**File**: `services/worker/worker/tasks/trends.py`
**Task**: `update_trends` (runs every hour)

For each cluster:
- Count non-duplicate articles in last 24h and previous 24h windows
- Compute `trend_score = (recent_24h * 3) + (previous_24h * 1)`
- Compute velocity label:
  - `surging`: ratio >= 2.0 (or >3 articles with no previous)
  - `rising`: ratio >= 1.2 (or >0 articles with no previous)
  - `steady`: ratio >= 0.8
  - `declining`: ratio < 0.8

### Step 11: Briefing Generation

**File**: `services/worker/worker/tasks/briefing.py`
**Task**: `generate_briefing` (runs every 6 hours)

Hierarchical summarization pipeline:
1. Load top 12 clusters by article count
2. For each cluster, load up to 20 non-duplicate article summaries
3. Build cluster sections: `### {keyword} ({count} articles)\n{summaries}`
4. Detect new entities (first seen in last 24 hours)
5. LLM generates JSON briefing:

```json
{
  "summary": "2-3 paragraph executive summary",
  "highlights": ["key development 1", ...],
  "watch_items": ["thing to monitor", ...]
}
```

### Step 12: Coverage Gap Detection

**File**: `services/worker/worker/tasks/coverage_gaps.py`
**Task**: `detect_coverage_gaps` (runs every 12 hours)

LLM analyzes existing cluster keywords and identifies areas that should be covered but are missing. Results are stored in the latest briefing's `coverage_gaps` field.

### Step 13: Sentiment History Aggregation

**File**: `services/worker/worker/tasks/sentiment_agg.py`
**Task**: `compute_sentiment_history` (runs every 2 hours)

For each cluster, computes daily average sentiment and article count. Uses `ON CONFLICT` (upsert) to backfill or update existing records. Critically stores `cluster_keyword` text alongside the `cluster_id` foreign key, making historical data queryable even after reclustering changes cluster IDs.

---

## 11. Frontend

### Technology

- **Framework**: Next.js ^14.2.0 with App Router
- **UI**: React 18.3.0, Tailwind CSS ^3.4.0 with dark theme
- **State**: Zustand ^4.5.0
- **HTTP**: Axios ^1.7.0 with JWT interceptors
- **Visualizations**: D3.js ^7.9.0 (force simulations), Recharts ^2.12.0 (charts)
- **Icons**: Lucide React ^0.400.0
- **TypeScript**: ^5.5.0 with full type coverage

### File Structure

```
services/frontend/
  src/
    app/
      layout.tsx              # Root layout (global styles)
      page.tsx                # Root page
      login/page.tsx          # Login page
      register/page.tsx       # Registration page
      dashboard/
        layout.tsx            # Dashboard layout (AuthGuard, Sidebar, WebSocket)
        page.tsx              # Main dashboard (stats, trending, briefing)
        articles/page.tsx     # Article list with filters
        investment/page.tsx   # Investment dashboard
        search/page.tsx       # Semantic search
        settings/page.tsx     # User settings, API keys
        models/page.tsx       # AI model status and task routing
        topics/
          new/page.tsx        # Create topic
          [id]/page.tsx       # Topic detail (clusters, articles)
    components/
      AnalysisCard.tsx        # Investment analysis card
      AssetMappings.tsx       # Entity-to-ticker mapping display
      AuthGuard.tsx           # Redirect to /login if unauthenticated
      BriefingView.tsx        # Briefing display with highlights
      BubbleCluster.tsx       # D3 force-directed bubble visualization
      ClusterDetail.tsx       # Cluster articles and stats
      CorrelationSignals.tsx  # Signal cards
      EntityNetwork.tsx       # D3 entity co-occurrence graph
      PriceAlerts.tsx         # Alert management UI
      SentimentTimeline.tsx   # Recharts sentiment chart
      Sidebar.tsx             # Navigation sidebar with WS status
      SymbolDetail.tsx        # Market data display
      TrendChart.tsx          # Recharts trend visualization
    hooks/
      useWebSocket.ts         # WebSocket with exponential backoff reconnect
    lib/
      api-client.ts           # Axios instance with JWT refresh interceptor
      auth-storage.ts         # localStorage token management
      design-tokens.ts        # Color/spacing/typography tokens
      force-simulation.ts     # D3 force simulation utilities
      store.ts                # Zustand state store
      types.ts                # TypeScript type definitions
```

### State Management (Zustand)

**File**: `services/frontend/src/lib/store.ts`

```typescript
interface AppState {
  user: User | null;
  topics: Topic[];
  clusters: Cluster[];
  latestBriefing: Briefing | null;
  pendingUpdates: number;
  selectedTopicId: string | null;
  setUser: (user: User | null) => void;
  setTopics: (topics: Topic[]) => void;
  selectTopic: (id: string | null) => void;
  setClusters: (clusters: Cluster[]) => void;
  setLatestBriefing: (briefing: Briefing | null) => void;
  incrementUpdates: () => void;
  clearUpdates: () => void;
}
```

### API Client

**File**: `services/frontend/src/lib/api-client.ts`

Axios instance configured with:
- Base URL: SSR-safe (Docker internal `http://api:8080` for server-side, `NEXT_PUBLIC_API_URL` for client-side)
- Request interceptor: attaches `Authorization: Bearer <token>` from localStorage
- Response interceptor: on 401 error, attempts token refresh via `/auth/refresh` with deduplication of concurrent refresh calls, then retries the original request. If refresh fails, clears tokens and redirects to `/login`.

### WebSocket Hook

**File**: `services/frontend/src/hooks/useWebSocket.ts`

Custom React hook providing:
- Automatic connection with JWT auth message
- Exponential backoff reconnection (1s, 2s, 4s, 8s... up to 30s)
- Pong response to server ping messages
- Prevents reconnect on code 4001 (auth fail) or 1000 (intentional close)
- Returns `{ connected, lastMessage, send }`

### Dashboard Layout

**File**: `services/frontend/src/app/dashboard/layout.tsx`

Wraps all dashboard pages with:
1. `AuthGuard` -- redirects unauthenticated users to `/login`
2. `Sidebar` -- navigation with WebSocket connection status indicator
3. WebSocket connection -- established once, handles `onMessage` to increment `pendingUpdates` counter (for any message type except `connected` and `ping`)
4. User profile fetch on mount via `getMe()`

### Models Page

**File**: `services/frontend/src/app/dashboard/models/page.tsx`

Displays:
- Model status cards for primary and fast models (health polling every 30 seconds)
- Task routing configuration table with 10 task categories
- Ability to switch each task between `fast`, `primary`, and `auto` model targets

### Design System

**File**: `services/frontend/src/lib/design-tokens.ts`

Dark theme with:
- Surface base: `#0f1117`
- Surface raised: `#161923`
- Surface overlay: `#1e2130`
- Border: `#2a2d3e`
- Accent primary: `#3B82F6` (blue)
- Accent success: `#10B981` (green)
- Accent warning: `#F59E0B` (amber)
- Accent danger: `#EF4444` (red)
- Text primary: `#F1F5F9`
- Text secondary: `#94A3B8`

**Cluster color palette** (15 colors):
`#3B82F6`, `#10B981`, `#F59E0B`, `#EF4444`, `#8B5CF6`, `#EC4899`, `#06B6D4`, `#84CC16`, `#F97316`, `#6366F1`, `#14B8A6`, `#E11D48`, `#A855F7`, `#0EA5E9`, `#D946EF`

**Sentiment color scale**: Red (-0.3+) -> Orange -> Gray (-0.1 to 0.1) -> Green -> Full green (0.3+)

**Velocity colors**: surging (red), rising (amber), stable (gray), declining (blue)

### Key Visualizations

**BubbleCluster** (`services/frontend/src/components/BubbleCluster.tsx`):
D3 force-directed bubble chart where each bubble represents a cluster. Size is proportional to `article_count`, color matches the cluster's assigned color, and position is determined by force simulation (center gravity + collision avoidance). Radius scale: 20-80px.

**EntityNetwork** (`services/frontend/src/components/EntityNetwork.tsx`):
D3 force graph showing entities as nodes (colored by type) and edges representing co-occurrence in articles. Uses the `/api/topics/{topic_id}/entity-graph` endpoint data. Link distance: 100, charge strength: -150.

**SentimentTimeline** (`services/frontend/src/components/SentimentTimeline.tsx`):
Recharts line chart showing daily sentiment trends per cluster keyword over configurable time ranges.

---

## 12. Investment Module

### Overview

The investment module bridges intelligence gathering with financial market data, enabling users to track how news events correlate with price movements.

### Entity-to-Ticker Resolution

**File**: `services/worker/worker/tasks/resolve_ticker.py`

Two-step resolution process:

**Step 1 -- Reference Lookup** (fast, no LLM):
```python
ref = session.execute(
    select(TickerReference).where(
        TickerReference.name.ilike(f"%{entity.name}%"),
        TickerReference.is_active == True,
    ).limit(1)
).scalar_one_or_none()
```
Confidence: 0.9, method: `reference_lookup`

**Step 2 -- LLM Inference** (if no reference match):
```python
result = _llm.generate_json([
    {"role": "system", "content": "Given the entity name, determine if it corresponds to a publicly traded stock, ETF, or cryptocurrency..."},
    {"role": "user", "content": f"Entity: {entity.name} (type: {entity.type})"},
])
```
Only accepts resolutions with `confidence >= 0.6`. Method: `llm_inference`.

Triggered only for entities of type `org`, `product`, or `technology`.

### Market Data

**File**: `services/worker/worker/tasks/maintenance.py`

**`fetch_market_data`** runs for each resolved symbol from both watchlists and asset mappings:
- **Equities/ETFs**: Uses `yfinance` library. Fetches current price, volume, market cap, P/E ratio, EPS, dividend yield, beta, 52-week high/low, and 6 months of daily OHLCV history.
- **Crypto**: Uses CoinGecko API (`/api/v3/coins/{id}/market_chart`). Converts coin symbols to CoinGecko IDs.
- Uses `ON CONFLICT` (upsert) for both `market_data_cache` and `price_history`.

### Price Alerts

**File**: `services/worker/worker/tasks/price_alerts.py`

Runs every 15 minutes. For each active alert:

1. Fetch latest `market_data_cache` entry for the symbol
2. Evaluate condition against threshold:
   - `above`: current price > threshold
   - `below`: current price < threshold
   - `crosses_above`: previous price <= threshold AND current price > threshold
   - `crosses_below`: previous price >= threshold AND current price < threshold
3. On trigger:
   - Deactivate the alert (`is_active = False`)
   - Set `triggered_at` timestamp
   - Publish to Redis pub/sub channel `ttwatch:alerts:triggered`:
     ```json
     {"type": "price_alert", "user_id": "...", "symbol": "...", "condition": "...", "threshold": 150.0, "current_price": 155.0}
     ```
4. Update `last_known_price` for crossing condition tracking

### Investment Analyses

**File**: `services/worker/worker/tasks/investment_analysis.py`

Per-asset LLM analysis combining:
- Recent article summaries mentioning the entity
- Current market data from `market_data_cache`
- Existing correlation signals

Generates structured analysis with:
- `recommendation` text
- `key_signals` (JSONB array)
- `risk_factors` (JSONB array)
- `confidence` score
- `sentiment_score` aggregate

Runs daily at 06:00 for all topics with resolved asset mappings.

### Correlation Signal Detection

**File**: `services/worker/worker/tasks/correlation_signals.py`

Compares 48-hour average sentiment with price change percentage:

| Condition | Signal Type |
|-----------|-------------|
| avg_sentiment > 0.3 AND price_change < -2% | `sentiment_price_divergence_bullish` |
| avg_sentiment < -0.3 AND price_change > 2% | `sentiment_price_divergence_bearish` |
| avg_sentiment > 0.5 AND price_change > 3% | `momentum_confirmation_bullish` |
| avg_sentiment < -0.5 AND price_change < -3% | `momentum_confirmation_bearish` |

Signals with strength >= 0.3 are persisted to the `correlation_signals` table.

### Real-Time Price Alert Delivery

```
Worker (price_alerts.py)
    |
    | redis.publish("ttwatch:alerts:triggered", JSON)
    v
API (main.py: ws_alert_listener coroutine)
    |
    | Subscribes async, bridges to ConnectionManager
    v
ConnectionManager.notify_user(user_id, event)
    |
    | ws.send_json(event)
    v
Frontend WebSocket -> Dashboard notification
```

---

## 13. Data Flow Diagrams

### Complete Article Lifecycle

```
[User creates topic]
        |
        v
[generate_search_queries] -- LLM --> topic.config["search_queries"]
        |
        v
[run_topic_search]
        |
        +-- SearXNG query 1 --> results
        +-- SearXNG query 2 --> results
        +-- SearXNG query N --> results
        |
        v
  [URL dedup within batch]
        |
        v (per unique URL)
[ingest_article]
        |
        +-- Layer 1: Redis URL SET check
        |       (fail: return "duplicate", track skipped)
        |
        +-- trafilatura.fetch_url() with 10s timeout
        +-- trafilatura.extract(favor_precision=True)
        |       (fail if < 100 chars, track skipped)
        |
        +-- Layer 2: SHA-256 content hash check vs PostgreSQL
        |       (fail: return "duplicate", track skipped)
        |
        +-- MinIO: store raw text at {user}/{topic}/{hash}.txt
        +-- PostgreSQL: INSERT article record
        +-- Redis: SADD url to dedup set
        |
        +---> [embed_article]         -- Embedder --> Qdrant upsert
        |         |                              + Layer 3: cosine > 0.92 semantic dedup
        |         +-- Auto-dispatch recluster when embedded >= 80% expected
        +---> [summarize_article]     -- LLM --> article.summary
        +---> [classify_sentiment]    -- LLM --> article.sentiment_score
        +---> [score_relevance]       -- LLM --> article.relevance_score
        +---> [extract_entities]      -- LLM --> entities + entity_article_map
                  +---> [resolve_entity_ticker] -- LLM/ref --> asset_mappings

[Periodic: every 2h]
[recluster_topic]
        |
        +-- Qdrant: scroll all points (no vectors, for sorting)
        +-- PostgreSQL: filter out duplicates + low relevance
        +-- Qdrant: retrieve selected points WITH vectors
        +-- UMAP: 1024d --> 20d
        +-- HDBSCAN: cluster assignment
        +-- Preserve sentiment_history + entity_cluster_map
        +-- Delete old clusters
        +-- LLM: generate 2-4 word keyword per cluster
        +-- PostgreSQL: create new clusters + update article cluster_ids

[Periodic: every 1h]
[update_trends]
        |
        +-- Count articles in 24h and 48h windows per cluster
        +-- Compute weighted trend_score and velocity label

[Periodic: every 6h]
[generate_briefing]
        |
        +-- Load top 12 clusters with article summaries
        +-- Detect new entities (first_seen in last 24h)
        +-- LLM: generate executive briefing JSON

[Periodic: every 12h]
[detect_coverage_gaps]
        |
        +-- LLM: analyze cluster keywords, identify missing coverage
        +-- Store in latest briefing.coverage_gaps
```

### Investment Data Flow

```
[extract_entities] -- new org/product/technology entity
        |
        v
[resolve_entity_ticker]
        |
        +-- Step 1: ticker_reference ILIKE lookup
        |       (match: confidence=0.9, method=reference_lookup)
        |
        +-- Step 2: LLM inference
        |       (match if confidence >= 0.6, method=llm_inference)
        |
        v
  asset_mappings record created
        |
        v
[refresh_market_data] (every 30min)
        |
        +-- Discovers symbols from watchlist_items AND asset_mappings
        +-- yfinance (equities/ETFs): price, volume, fundamentals, OHLCV
        +-- CoinGecko (crypto): price, market cap, volume
        |
        v
  market_data_cache + price_history upserted

[check_price_alerts] (every 15min)
        |
        +-- Compare market_data_cache.price vs alert.threshold
        +-- Evaluate condition (above/below/crosses_above/crosses_below)
        |
        v (on trigger)
  Redis pub/sub --> API ws_alert_listener --> WebSocket --> Frontend

[detect_correlation_signals] (every 4h)
        |
        +-- 48h avg sentiment per entity vs price_change_pct
        +-- Detect divergence/confirmation patterns
        |
        v
  correlation_signals record created

[generate_investment_analyses] (daily 06:00)
        |
        +-- Per-asset: article summaries + market data + signals
        +-- LLM: structured analysis with recommendation
        |
        v
  investment_analyses record created
```

### WebSocket Event Flow

```
[Frontend]                          [API]                           [Worker]
    |                                 |                                |
    |-- WS connect ------------------>|                                |
    |                                 |-- accept()                     |
    |<-- {"type":"connected"} --------|                                |
    |                                 |                                |
    |                                 |<-- Redis pub/sub subscribe ----|
    |                                 |    (alerts, search, progress)  |
    |                                 |                                |
    |                                 |        [price_alerts task runs]|
    |                                 |                                |
    |                                 |<-- publish("ttwatch:alerts:    |
    |                                 |     triggered", {...})         |
    |                                 |                                |
    |<-- {"type":"price_alert",...}----|                                |
    |                                 |                                |
    |                                 |      [search progress update]  |
    |                                 |                                |
    |                                 |<-- publish("ttwatch:search:    |
    |                                 |     progress", {...})          |
    |                                 |                                |
    |<-- {"type":"search_progress"}----|                                |
    |                                 |                                |
    |<-- {"type":"ping"} -------------|                                |
    |-- {"type":"pong"} ------------->|                                |
    |                                 |                                |
```

---

## 14. Configuration Reference

### Environment Variables

All settings are managed via environment variables, loaded by Pydantic Settings in `services/api/app/config.py`.

#### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://ttwatch_app:changeme@postgres:5432/ttwatch` | PostgreSQL connection string |
| `POSTGRES_PASSWORD` | (required in `.env`) | PostgreSQL superuser password |
| `APP_DB_PASSWORD` | (required in `.env`) | Password for `ttwatch_app` role |
| `WORKER_DB_PASSWORD` | (required in `.env`) | Password for `ttwatch_worker` role |

#### Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` | Celery results |
| `REDIS_DEDUP_URL` | `redis://redis:6379/2` | URL dedup SET storage |
| `REDIS_CACHE_URL` | `redis://redis:6379/3` | Cache, rate limiting, pub/sub |

#### Vector Database

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant REST endpoint |

#### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `local` | `local` or `cloud` |
| `VLLM_URL` | `http://vllm:8000/v1` | Primary vLLM OpenAI-compatible endpoint |
| `VLLM_FAST_URL` | `http://vllm-fast:8000/v1` | Fast vLLM endpoint for classification tasks |
| `LOCAL_MODEL_NAME` | `Qwen3-32B-AWQ` | Primary model name (used in vLLM path) |
| `FAST_MODEL_NAME` | `Qwen3-8B-AWQ` | Fast model name |
| `CLOUD_LLM_PROVIDER` | `openai` | `openai`, `anthropic`, or `openrouter` |
| `CLOUD_LLM_API_KEY` | (empty) | API key for cloud provider |
| `CLOUD_LLM_MODEL` | `gpt-4o-mini` | Cloud model name |

#### Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDER_URL` | `http://embedder:8001` | Local embedding server |
| `EMBEDDING_MODEL_NAME` | `Qwen/Qwen3-Embedding-0.6B` | Embedding model to load |
| `EMBEDDING_DIMENSION` | `1024` | Vector dimension (1024 for Qwen3, 3072 for OpenAI large) |
| `EMBEDDER_DEVICE` | `cuda` | Embedding device (`cuda` or `cpu`) |
| `CLOUD_EMBEDDING_PROVIDER` | `openai` | Cloud embedding provider |
| `CLOUD_EMBEDDING_MODEL` | `text-embedding-3-large` | Cloud embedding model |

#### Search

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG meta-search endpoint |

#### Object Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_URL` | `http://minio:9000` | MinIO S3-compatible endpoint |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | `ttwatch-content` | Bucket name for article content |

#### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | `change-me` | HS256 signing key |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |

#### Frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8080` | API base URL for client-side requests |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8080/ws` | WebSocket URL |
| `INTERNAL_API_URL` | `http://api:8080` | API URL for server-side rendering |

### SearXNG Configuration

**File**: `config/searxng/settings.yml`

```yaml
search:
  formats:
    - json
engines:
  - name: google
    engine: google
    shortcut: g
  - name: bing
    engine: bing
    shortcut: b
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
  - name: google_news
    engine: google_news
    shortcut: gn
  - name: bing_news
    engine: bing_news
    shortcut: bn
server:
  limiter: false
  image_proxy: false
```

JSON format is enabled (required for programmatic access). Rate limiter is disabled since access is internal only.

### vLLM Configuration (GPU mode)

From `docker-compose.gpu.yml`:

**Primary model (vllm)**:
```
--model /models/Qwen3-32B-AWQ
--quantization awq_marlin
--gpu-memory-utilization 0.65
--max-model-len 8192
--max-num-seqs 8
--enable-prefix-caching
--reasoning-parser deepseek_r1
```

**Fast model (vllm-fast)**:
```
--model /models/Qwen3-8B-AWQ
--quantization awq_marlin
--gpu-memory-utilization 0.85
--max-model-len 8192
--max-num-seqs 16
--enable-prefix-caching
--disable-log-requests
```

| Flag | Primary | Fast | Purpose |
|------|---------|------|---------|
| `--quantization` | awq_marlin | awq_marlin | Optimized AWQ quantization kernel |
| `--gpu-memory-utilization` | 0.65 | 0.85 | GPU memory allocation (shared GPU) |
| `--max-model-len` | 8192 | 8192 | Maximum context length |
| `--max-num-seqs` | 8 | 16 | Maximum concurrent sequences |
| `--reasoning-parser` | deepseek_r1 | — | Parse reasoning output format |
| `--disable-log-requests` | — | yes | Reduce logging for high-throughput |

**GPU-node standalone** (`docker-compose.gpu-node.yml`) uses different settings: `--quantization awq --gpu-memory-utilization 0.85 --max-model-len 32768` (single model, full GPU).

### Embedder Configuration

**File**: `services/embedder/server.py`

- Model: `Qwen/Qwen3-Embedding-0.6B` (from `MODEL_NAME` env var)
- Device: Configurable via `EMBEDDER_DEVICE` (default `cuda`, set to `cpu` in gpu.yml)
- Batch size: 64
- Normalize: True
- Max texts per request: 256
- Output dimension: Dynamically reported by model (1024 for Qwen3-Embedding-0.6B)

### Redis Database Allocation

| Database | Purpose |
|----------|---------|
| db0 | Celery message broker |
| db1 | Celery result backend (expires in 3600s) |
| db2 | URL dedup SETs (key: `ttwatch:dedup:urls:{user_id}`) |
| db3 | Cache (search status, rate limits), pub/sub (alerts, search completed, search progress) |

### Redis Key Patterns

| Pattern | Database | Purpose |
|---------|----------|---------|
| `ttwatch:dedup:urls:{user_id}` | db2 | SET of ingested URLs per user |
| `ttwatch:search_status:{topic_id}` | db3 | JSON search status (TTL 3600s) |
| `ttwatch:search_lock:{topic_id}` | db3 | Search cooldown lock (TTL 300s) |
| `ttwatch:search_progress:{topic_id}:*` | db3 | Search progress counters (queries_total, queries_completed, ingested, tasks_completed, started_at) (TTL 7200s) |
| `ttwatch:processing:{topic_id}:*` | db3 | Processing phase counters (expected, phase, embedded, summarized, sentiment, relevance, entities, cluster_dispatched) (TTL 7200s) |
| `ttwatch:rate:{user_id}:{endpoint}` | db3 | Rate limit counter (TTL 60s) |
| `ttwatch:alerts:triggered` | db3 | Pub/sub channel for price alerts |
| `ttwatch:search:completed` | db3 | Pub/sub channel for search completions |
| `ttwatch:search:progress` | db3 | Pub/sub channel for search progress updates |

---

## 15. Implementation Status

### Fully Implemented

- User registration, login, JWT auth, API key auth
- Topic CRUD with LLM query decomposition
- SearXNG meta-search with multi-query support
- Article ingestion with 3-layer deduplication
- LLM summarization with CoT cleanup
- Embedding generation and Qdrant vector storage
- Semantic dedup (cosine > 0.92)
- HDBSCAN clustering with UMAP reduction
- LLM-generated cluster keywords
- Trend scoring and velocity labels
- Entity extraction and entity-article mapping
- Entity-to-ticker resolution (reference lookup + LLM)
- Sentiment classification (-1.0 to 1.0)
- Relevance scoring with filtering threshold
- Briefing generation via hierarchical summarization
- Coverage gap detection
- Sentiment history aggregation (recluster-proof via cluster_keyword)
- Market data fetching (yfinance + CoinGecko)
- Price alerts with WebSocket delivery
- Investment analysis generation
- Correlation signal detection
- Watchlist management
- Saved query CRUD
- Source CRUD
- Semantic search via Qdrant
- Entity co-occurrence graph API
- Rate limiting (Lua-based atomic Redis operations)
- PostgreSQL RLS with 3 database roles
- WebSocket with heartbeat, auth, and reconnect
- Service version checking against upstream registries
- Data cleanup tasks (market data, snapshots, tokens, orphaned vectors)
- Admin version status endpoint
- Full dark-theme frontend with D3 visualizations
- 4 deployment modes with Docker Compose overlays
- Dual-model LLM routing (primary Qwen3-32B-AWQ + fast Qwen3-8B-AWQ)
- Per-user LLM task routing configuration (10 categories)
- Models dashboard page with health monitoring and routing controls
- Search progress tracking with multi-phase status (generating_queries -> searching -> processing -> completed)
- Search cancellation
- Pipeline stall detection and auto-recovery
- Skipped article tracking to prevent pipeline stalls from dedup-heavy batches
- Comprehensive system diagnostic script (ttwatch-diagnose.sh)

### Not Yet Implemented / Placeholder

- **MCP Server**: `services/api/app/mcp/__init__.py` exists but is empty. No MCP functionality is implemented.
- **RSS Feed Sources**: The `sources` table and CRUD exist, but no RSS feed polling task is implemented. Sources are managed but not consumed.
- **Saved Query Execution**: Saved queries can be created and listed, but no task executes them on schedule.
- **Entity Cluster Map Population**: `entity_cluster_map` table exists and is preserved during reclustering, but no task populates it after initial cluster creation.
- **Article Key Quotes**: The `key_quotes` JSONB column exists on articles, but no task extracts key quotes.
- **Cloud Embedding Dimension Mismatch Handling**: When switching between local (1024d) and cloud (3072d) embeddings, existing Qdrant collection dimensions may conflict. No automatic migration exists.

---

## 16. File and Directory Reference

### Root Directory

```
TTwatch/
  .env                          # Environment variables (secrets, service versions)
  .env.example                  # Template with placeholder values
  Makefile                      # Deployment and management targets
  docker-compose.yml            # Base: 10 services (no GPU)
  docker-compose.gpu.yml        # GPU overlay: adds vllm + vllm-fast + embedder
  docker-compose.dev.yml        # Dev overlay: hot-reload, volume mounts
  docker-compose.cloud.yml      # Cloud overlay: cloud LLM settings
  docker-compose.lan.yml        # LAN overlay: remote services
  docker-compose.gpu-node.yml   # Standalone GPU node
  docker-compose.search-node.yml # Standalone SearXNG node
```

### API Service

```
services/api/
  Dockerfile                    # Python 3.11-slim, pip install, uvicorn
  requirements.txt              # FastAPI, SQLAlchemy[asyncio], asyncpg, pyjwt, argon2-cffi, etc.
  app/
    __init__.py
    main.py                     # FastAPI app, WebSocket, ConnectionManager, lifespan, pub/sub listeners
    config.py                   # Pydantic Settings (all env vars)
    deps.py                     # DB engine, Redis, auth deps, rate limiting
    celery_client.py            # Celery app instance for task dispatch
    auth/
      __init__.py
      router.py                 # Register, login, refresh, logout endpoints
    models/
      __init__.py               # Imports all models
      base.py                   # SQLAlchemy declarative base
      user.py                   # User, ApiKey, RefreshToken
      intelligence.py           # Topic, Source, Cluster, Article, Entity, etc.
      investment.py             # TickerReference, MarketDataCache, PriceAlert, etc.
      llm_config.py             # LlmTaskConfig (per-user model routing)
    routers/
      health.py                 # /health, /health/services
      topics.py                 # CRUD + search trigger + cancel + status + processing + clusters
      clusters.py               # Get cluster + list articles
      articles.py               # List (filtered) + get + entities
      search.py                 # Semantic search via Qdrant
      briefings.py              # List + get + trigger generation
      entities.py               # List + graph + get + articles
      sentiment.py              # Overview + history timeline
      sources.py                # CRUD
      queries.py                # CRUD saved queries
      investment.py             # Watchlist + analyses + signals + alerts
      market_data.py            # Symbol data + price history
      users.py                  # Profile + API key management
      admin.py                  # Version status + check trigger
      models.py                 # Model status + task routing config
    schemas/
      topics.py                 # TopicCreate, TopicUpdate, TopicResponse, ClusterResponse
      articles.py               # ArticleResponse, ArticleDetail
      entities.py               # EntityResponse, EntityGraphResponse
      investment.py             # WatchlistCreate, PriceAlertCreate, MarketDataResponse, etc.
      queries.py                # SavedQueryCreate, SavedQueryResponse
      sentiment.py              # SentimentPointResponse
      sources.py                # SourceCreate, SourceResponse
    services/
      llm.py                    # Abstract LLMProvider interface
      llm_factory.py            # Factory: local vs cloud provider
      llm_local.py              # LocalVLLMProvider (httpx async)
      llm_cloud.py              # CloudLLMProvider (OpenAI/Anthropic/OpenRouter)
      llm_utils.py              # JSON parsing from LLM output
      embedder.py               # LocalEmbeddingProvider + CloudEmbeddingProvider
      init_services.py          # Qdrant collection + MinIO bucket init (with dimension validation)
      http_utils.py             # Shared HTTP retry configuration
      version_checker.py        # Upstream version checking (GitHub, DockerHub, HuggingFace)
    middleware/
      rate_limit.py             # Lua-based Redis rate limiter
    mcp/
      __init__.py               # Empty placeholder
```

### Worker Service

```
services/worker/
  Dockerfile                    # Python 3.11-slim, apt-get build-essential (for HDBSCAN)
  requirements.txt              # celery[gevent], sqlalchemy, psycopg2-binary, hdbscan, umap-learn, etc.
  worker/
    __init__.py
    celeryconfig.py             # Celery config: routing, beat schedule, task discovery
    db.py                       # Sync SQLAlchemy engine, psycogreen patching, db_session()
    rls.py                      # with_rls_context decorator (bound + unbound task support)
    llm_sync.py                 # SyncLLMClient + SyncEmbeddingClient (httpx sync, tenacity retry)
    llm_router.py               # Per-user LLM task routing (primary/fast/auto)
    tasks/
      briefing.py               # generate_briefing: hierarchical summarization
      cluster.py                # recluster_topic: UMAP + HDBSCAN
      correlation_signals.py    # detect_correlation_signals: sentiment vs. price
      coverage_gaps.py          # detect_coverage_gaps: LLM gap analysis
      embed.py                  # embed_article: vectorize + Qdrant + semantic dedup + auto-cluster trigger
      entities.py               # extract_entities: LLM NER + entity-article mapping
      ingest.py                 # ingest_article: fetch + extract + 3-layer dedup + fan-out + skip tracking
      investment_analysis.py    # generate_investment_analyses: per-asset LLM analysis
      maintenance.py            # cleanup tasks + fetch_market_data
      periodic.py               # schedule_* dispatch tasks (9 dispatch tasks + refresh_market_data)
      price_alerts.py           # check_price_alerts: threshold evaluation + pub/sub
      relevance.py              # score_relevance: LLM relevance scoring
      resolve_ticker.py         # resolve_entity_ticker: reference + LLM resolution
      search.py                 # run_topic_search: SearXNG queries + dispatch + detect_stalled_pipelines
      search_plan.py            # generate_search_queries: LLM query decomposition
      sentiment.py              # classify_sentiment: LLM sentiment score
      sentiment_agg.py          # compute_sentiment_history: daily aggregation
      summarize.py              # summarize_article: LLM summary + CoT cleanup
      trends.py                 # update_trends: trend scores + velocity
      utils.py                  # fetch_article_text: MinIO raw text retrieval
      version_check.py          # check_service_versions: upstream registry check
```

### Embedder Service

```
services/embedder/
  Dockerfile                    # Python 3.11-slim, sentence-transformers, torch
  requirements.txt              # fastapi, uvicorn, sentence-transformers, torch
  server.py                     # FastAPI: /embed (POST), /health (GET)
```

### Frontend Service

```
services/frontend/
  Dockerfile                    # Node 20-alpine, npm install, next build, next start
  package.json                  # next ^14.2.0, react 18.3.0, zustand, axios, d3, recharts
  tailwind.config.ts            # Dark theme configuration
  tsconfig.json
  src/                          # (see Section 11 for full file tree)
```

### Configuration

```
config/
  searxng/
    settings.yml                # SearXNG engine configuration
```

### Migrations

```
migrations/
  env.py                        # Alembic environment (imports all models)
  versions/
    001_create_users_and_auth.py    # users, api_keys, refresh_tokens
    002_create_intelligence_tables.py  # topics, sources, clusters, articles, entities, etc.
    003_create_investment_tables.py  # ticker_reference, market_data, watchlist, alerts, etc.
    004_add_rls_policies.py         # RLS policies on 15 tables
    005_grants_app_role.py          # ttwatch_app role grants
    006_grants_worker_role.py       # ttwatch_worker role grants
    007_create_llm_task_config.py   # llm_task_config table with RLS
```

### Scripts

```
scripts/
  init-db.sh                    # Database initialization (roles, extensions)
  create-admin-user.py          # Admin user creation
  seed-topics.py                # Sample topic seeding
  backup.sh                     # PostgreSQL backup
  restore.sh                    # PostgreSQL restore
  download-models.sh            # HuggingFace model download
  update.sh                     # Application update
  benchmark-gpu.py              # vLLM benchmark
  cleanup_bad_data.py           # Data cleanup (CoT artifacts, bad summaries)
  ttwatch-diagnose.sh           # Comprehensive system diagnostic
```

### Tests

```
tests/
  conftest.py                   # SQLite test DB, mocked services (LLM, embedder, Qdrant, MinIO, Redis, Celery)
  test_auth.py                  # Auth flow tests (register, login, refresh, logout, protected endpoints)
  test_topics.py                # Topic CRUD tests (create, read, update, delete, limits, isolation)
  test_search.py                # Semantic search tests (auth, results, scoring, filtering)
  test_ingestion.py             # Ingestion pipeline tests
  test_investment.py            # Investment module tests (watchlist, alerts, analyses, signals)
```

---

## 17. Development Guide

### Prerequisites

- Docker and Docker Compose v2
- NVIDIA GPU with CUDA drivers (for GPU modes)
- 24+ GB VRAM recommended for dual-model GPU mode (Qwen3-32B-AWQ + Qwen3-8B-AWQ)
- Node.js 20+ (for frontend development outside Docker)

### First-Time Setup

```bash
# 1. Clone the repository
git clone <repo-url> && cd TTwatch

# 2. Copy and configure environment
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, APP_DB_PASSWORD, WORKER_DB_PASSWORD,
#            JWT_SECRET, MINIO_ROOT_PASSWORD

# 3. Download models (GPU mode only)
make download-models  # or: bash scripts/download-models.sh

# 4. Start in development mode
make dev              # No GPU
make dev-gpu          # With GPU

# 5. Initialize database (first run only)
# init-db.sh runs automatically via postgres entrypoint
make migrate          # Run Alembic migrations

# 6. Create admin user
make create-admin

# 7. Optionally seed sample topics
make seed-topics
```

### Development Workflow

**API development** (with hot-reload):
```bash
make dev  # or dev-gpu
# API auto-reloads on file changes (uvicorn --reload)
# Workers auto-restart on file changes (watchmedo)
```

**Frontend development** (standalone):
```bash
cd services/frontend
npm install
npm run dev  # Next.js dev server on :3000
```

**Running tests**:
```bash
# From API container
make shell-api
pytest tests/

# Or from host with virtual environment
cd services/api
pip install -r requirements.txt
pytest ../../tests/
```

**Database operations**:
```bash
make migrate          # Apply pending migrations
make migrate-new      # Create new migration (interactive)
make shell-db         # psql shell
make backup           # Backup to backups/ directory
make restore          # Restore from backup file
```

**Log inspection**:
```bash
make logs             # All services
make logs-api         # API only
make logs-worker      # Worker only
```

**System diagnostics**:
```bash
bash scripts/ttwatch-diagnose.sh  # Full system diagnostic
```

### Adding a New API Router

1. Create router file in `services/api/app/routers/`:
```python
from fastapi import APIRouter, Depends
from app.deps import get_current_user, get_db

router = APIRouter()

@router.get("/my-endpoint")
async def my_endpoint(user=Depends(get_current_user), db=Depends(get_db)):
    ...
```

2. Register in `services/api/app/main.py`:
```python
from app.routers import my_router
app.include_router(my_router.router, prefix="/api", tags=["my_tag"], dependencies=_rate_limited)
```

### Adding a New Celery Task

1. Create task file in `services/worker/worker/tasks/`:
```python
from worker.celeryconfig import app
from worker.rls import with_rls_context

@app.task(name="my_task")
@with_rls_context
def my_task(user_id: str, ..., session=None):
    ...
```

2. Add module to `app.conf.include` in `services/worker/worker/celeryconfig.py`:
```python
app.conf.include = [
    ...,
    "worker.tasks.my_module",
]
```

3. If CPU-bound, add routing in `app.conf.task_routes`:
```python
app.conf.task_routes = {
    ...,
    "my_task": {"queue": "ttwatch:compute"},
}
```

### Adding a New Database Table

1. Define model in `services/api/app/models/`:
```python
class MyModel(Base):
    __tablename__ = "my_table"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    ...
```

2. Import in `services/api/app/models/__init__.py`

3. Create migration:
```bash
make migrate-new  # Enter migration message
```

4. If user-scoped, add RLS policy in a new migration:
```sql
ALTER TABLE my_table ENABLE ROW LEVEL SECURITY;
ALTER TABLE my_table FORCE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON my_table FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::uuid)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::uuid);
CREATE POLICY worker_bypass ON my_table FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
```

### Test Configuration

**File**: `tests/conftest.py`

Tests use an in-memory SQLite database with mocked external services:
- **LLM**: Returns canned JSON responses
- **Embedder**: Returns vectors of correct dimension (`[0.1] * 1024`)
- **Qdrant**: No-op operations
- **MinIO**: In-memory dict storage
- **Redis**: No-op
- **Celery**: Tasks mocked (not executed)
- **Password hasher**: Argon2 with reduced parameters for speed

---

## 18. Appendix: Key Design Decisions

### 1. Dual Worker Pool Architecture

**Decision**: Separate gevent (IO) and prefork (CPU) worker pools rather than a single mixed pool.

**Rationale**: Gevent's cooperative multitasking allows 32 concurrent I/O-bound tasks (HTTP fetches, LLM API calls, database queries) with minimal memory overhead. However, CPU-intensive tasks like UMAP+HDBSCAN clustering and hierarchical LLM briefing generation block the event loop and starve other greenlets. Prefork workers use process-based isolation, eliminating GIL contention for numpy/scipy operations.

**Trade-off**: Two worker containers consume more memory than one, but the throughput improvement is significant: 32 concurrent article ingestions can proceed while a 2-minute clustering job runs in a separate process.

### 2. Three-Layer Deduplication

**Decision**: URL check (Redis SET) -> content hash (PostgreSQL SHA-256) -> semantic similarity (Qdrant cosine > 0.92).

**Rationale**: Each layer catches different duplicate types:
- Layer 1 (URL): Prevents re-fetching known URLs. O(1) Redis lookup, zero HTTP overhead.
- Layer 2 (content hash): Catches mirror sites and syndication (same content, different URLs). Requires fetch but no LLM/embedding cost.
- Layer 3 (semantic): Catches paraphrased/rewritten articles that share the same information. Requires embedding but catches the most subtle duplicates.

**Trade-off**: Layer 3 adds latency (embedding generation + vector search), but only runs for articles that pass layers 1 and 2.

### 3. PostgreSQL RLS for Multi-Tenancy

**Decision**: Row-Level Security at the database level rather than application-level filtering.

**Rationale**: RLS guarantees isolation even if an application-level filter is accidentally omitted. Every query against user-scoped tables is automatically filtered by `current_setting('ttwatch.current_user_id')`. This eliminates an entire class of data leakage bugs.

**Trade-off**: Requires careful role management (3 database roles), `SET LOCAL` in every request path, and awareness of which tables have RLS vs. which are shared.

### 4. Cluster Keyword Preservation in Sentiment History

**Decision**: Store `cluster_keyword` text alongside `cluster_id` in `sentiment_history`.

**Rationale**: Clusters are deleted and recreated every 2 hours during reclustering. Without `cluster_keyword`, historical sentiment data would lose its label when the foreign key becomes NULL. By storing the text label, sentiment timelines remain meaningful and queryable even after many reclustering cycles.

**Trade-off**: Slight data denormalization. The keyword is duplicated between `clusters.keyword` and `sentiment_history.cluster_keyword`. But this is essential for data preservation.

### 5. Pre-Deletion Data Preservation During Reclustering

**Decision**: Before deleting old clusters, explicitly nullify `sentiment_history.cluster_id` and delete `entity_cluster_map` entries.

**Rationale**: Without this step, `ON DELETE CASCADE` on `entity_cluster_map` and `ON DELETE SET NULL` on `sentiment_history` would execute automatically. While SET NULL is technically the same outcome, the explicit handling ensures the operation is visible in the code and can be extended (e.g., to migrate entity-cluster relationships to new clusters).

### 6. Article UUID as Qdrant Point ID

**Decision**: Use `str(article.id)` as the Qdrant point ID, creating a 1:1 mapping between PostgreSQL articles and Qdrant vectors.

**Rationale**: During reclustering, the system retrieves vectors from Qdrant and needs to update the corresponding PostgreSQL articles with cluster assignments. Using the article UUID as the point ID eliminates the need for a separate mapping table or lookup step.

**Trade-off**: If Qdrant and PostgreSQL get out of sync (e.g., article deleted from PG but vector remains in Qdrant), orphaned points accumulate. The daily `cleanup_orphaned_qdrant_points` task handles this.

### 7. Hierarchical Summarization for Briefings

**Decision**: Articles -> article summaries -> cluster summaries -> topic briefing, rather than sending all article text directly.

**Rationale**: A topic may have 2000 articles with megabytes of combined text, far exceeding any LLM context window. By pre-summarizing at each level, the briefing generation task works with ~16K tokens of cluster summaries rather than raw article text.

**Trade-off**: Information loss at each summarization level. The briefing may miss nuances present in individual articles.

### 8. LLM Query Decomposition for Search

**Decision**: Use the LLM to generate 3-6 targeted search queries from a topic name, rather than using the topic name directly as a search query.

**Rationale**: A topic like "AI and Semiconductors" benefits from decomposed queries like "NVIDIA AI chip demand 2025", "TSMC semiconductor manufacturing", "AI inference hardware market" etc. This captures more relevant results than a single broad query.

**Trade-off**: One LLM call per topic creation. The cost is negligible compared to the quality improvement in search results.

### 9. Sync Workers with psycogreen Patching

**Decision**: Use synchronous SQLAlchemy with psycogreen gevent patching in workers, rather than async SQLAlchemy.

**Rationale**: Celery tasks are synchronous functions (`def`, not `async def`). Using async SQLAlchemy would require running an event loop inside each task, adding complexity. psycogreen makes psycopg2 gevent-compatible by monkey-patching its I/O operations to yield to the gevent scheduler.

**Trade-off**: The worker codebase is entirely synchronous, which means it cannot use async libraries. LLM and embedding clients use `httpx.Client` (sync) rather than `httpx.AsyncClient`.

### 10. Redis Pub/Sub Bridge for WebSocket

**Decision**: Workers publish to Redis pub/sub channels; API background coroutines subscribe and bridge to the WebSocket ConnectionManager.

**Rationale**: Workers and the API are separate processes (often separate containers). Redis pub/sub provides a decoupled, reliable channel between them. The API coroutines (`ws_alert_listener`, `ws_search_listener`, `ws_search_progress_listener`) run as background tasks in the FastAPI lifespan, consuming minimal resources.

**Trade-off**: If the API process restarts, in-flight pub/sub messages are lost. This is acceptable because price alerts are persisted in the database and will be visible on next page load.

### 11. Relevance Threshold Filtering

**Decision**: Exclude articles with `relevance_score < 0.3` from clustering.

**Rationale**: SearXNG returns some off-topic results. Without filtering, these create noise clusters that dilute the signal. The 0.3 threshold removes clearly irrelevant articles while keeping marginally relevant ones that might add context.

**Trade-off**: Some relevant articles with unusual framing may be incorrectly scored below 0.3 and excluded. The threshold is a tunable constant (`RELEVANCE_THRESHOLD` in `services/worker/worker/tasks/relevance.py`).

### 12. LAN Startup Retry with Exponential Backoff

**Decision**: Worker LLM/embedding clients retry connections up to 30 times with exponential backoff (5-60s).

**Rationale**: In LAN-distributed mode, the GPU node may start after the main node. Without retries, workers would fail permanently on first task execution. The retry pattern (implemented via `tenacity` in `services/worker/worker/llm_sync.py`) allows workers to self-heal once the GPU node becomes available.

**Trade-off**: First tasks may be delayed by up to ~15 minutes in worst case (sum of backoff delays). This is acceptable for LAN deployments where startup order is not guaranteed.

### 13. Dual-Model LLM Architecture

**Decision**: Run two vLLM instances -- a primary 32B reasoning model and a fast 8B classification model -- with per-user task routing.

**Rationale**: Most pipeline tasks (summarization, sentiment, relevance, entity extraction) are simple classification/extraction that don't benefit from a large reasoning model. Using the smaller 8B model for these tasks provides 2-4x throughput improvement with negligible quality loss. Complex tasks (briefings, investment analyses, coverage gaps) can optionally use the full 32B model for higher quality output.

**Trade-off**: Double GPU memory footprint (primary at 65% + fast at 85% utilization with sequential startup). Requires sufficient VRAM (24+ GB recommended). The fast model disables thinking (`enable_thinking=False`) to avoid unnecessary chain-of-thought overhead.

### 14. Pipeline Stall Detection

**Decision**: Run a `detect_stalled_pipelines` task every 2 minutes to force-complete stuck pipelines.

**Rationale**: In a distributed pipeline with many async fan-out tasks, race conditions can cause the completion check to never fire (e.g., all tasks complete before the expected count is fully set, or skipped articles aren't tracked). The stall detector provides a safety net that ensures the frontend always sees pipeline completion within 5 minutes of the actual completion.

**Trade-off**: Pipelines may show as "completed" slightly before all sub-tasks truly finish. This is acceptable because the 80% threshold for auto-clustering means most work is already done when the stall is detected.

---

## 19. Changelog (v1.0 -> v1.1)

### New Features

1. **Dual-Model LLM System**: Added `vllm-fast` service running Qwen3-8B-AWQ alongside the primary Qwen3-32B-AWQ model. All 10 task categories default to the fast model for improved throughput.

2. **Per-User LLM Task Routing**: New `llm_task_config` table (migration 007) and `llm_router.py` module allow users to configure which model (primary/fast/auto) handles each task category via the API.

3. **Models Dashboard Page**: New `/dashboard/models` frontend page showing model health status and task routing configuration with live controls.

4. **Models API Router**: New endpoints `GET /api/models/status`, `GET /api/models/task-routing`, `PUT /api/models/task-routing` for model management.

5. **Pipeline Stall Detection**: New `detect_stalled_pipelines` task runs every 2 minutes to detect and force-complete processing pipelines stuck for >5 minutes.

6. **Search Progress Tracking**: Multi-phase progress tracking (generating_queries -> searching -> processing -> completed) with Redis counters for queries, ingestion, and processing subtasks.

7. **Search Cancellation**: New `POST /api/topics/{topic_id}/search/cancel` endpoint.

8. **Processing Status Endpoint**: New `GET /api/topics/{topic_id}/processing-status` for detailed per-phase progress.

9. **Skipped Article Tracking**: `_track_skipped_article()` in ingest.py decrements expected counts when articles are deduplicated, preventing pipeline stalls from dedup-heavy batches.

10. **Auto-Cluster Trigger**: Embedding task auto-dispatches `recluster_topic` when 80% of expected articles are embedded, rather than waiting for the periodic 2-hour schedule.

11. **System Diagnostic Script**: New `scripts/ttwatch-diagnose.sh` with 12 diagnostic sections.

12. **Search Progress WebSocket**: New `ttwatch:search:progress` pub/sub channel and `ws_search_progress_listener` for real-time search progress updates.

### Model Changes

- **Primary LLM**: QwQ-32B-AWQ -> Qwen3-32B-AWQ
- **Fast LLM (new)**: Qwen3-8B-AWQ with thinking disabled
- **vLLM quantization**: `awq` -> `awq_marlin` (optimized kernel) in GPU-colocated mode
- **GPU memory split**: Primary model uses 0.65 (was 0.85), fast model uses 0.85
- **Max model length**: 32768 -> 8192 in GPU-colocated mode (8192 sufficient for all tasks)
- **Fast model concurrency**: 16 max sequences (vs 8 for primary)

### Infrastructure Changes

- **API port**: Standardized to 8080 (was 8000 in some configs)
- **SearXNG host port**: 8888:8080 (was 8080:8080)
- **Database passwords**: Split into `APP_DB_PASSWORD` and `WORKER_DB_PASSWORD` (was single `POSTGRES_PASSWORD`)
- **Health checks**: Updated intervals (Postgres/Qdrant/Redis 5s, was 10s), new checks for workers (celery inspect) and scheduler (pgrep)
- **Qdrant health check**: Changed from HTTP GET `/readyz` to TCP check on port 6333
- **MinIO health check**: Changed from `mc ready local` to `curl /minio/health/live`
- **Redis**: Added `--maxmemory 512mb --maxmemory-policy volatile-lru` configuration
- **Embedder device**: Now configurable via `EMBEDDER_DEVICE` env var (default: `cpu` in gpu.yml, `cuda` in gpu-node.yml)

### API Changes

- **Health endpoint**: `/health/extended` renamed to `/health/services`, now also checks vLLM-Fast
- **Topic articles**: Moved from `GET /api/articles?topic_id=...` to `GET /api/topics/{topic_id}/articles`
- **Briefings**: Moved from `GET /api/briefings?topic_id=...` to `GET /api/topics/{topic_id}/briefings`
- **Briefing generation**: Moved from `POST /api/briefings/generate` to `POST /api/topics/{topic_id}/briefings/generate`
- **Entities**: Moved from `GET /api/entities?topic_id=...` to `GET /api/topics/{topic_id}/entities`
- **Entity graph**: Moved from `GET /api/entities/graph` to `GET /api/topics/{topic_id}/entity-graph`
- **Sentiment**: Moved from `GET /api/sentiment/overview` to `GET /api/topics/{topic_id}/sentiment`
- **Sentiment history**: Moved from `GET /api/sentiment/history` to `GET /api/topics/{topic_id}/sentiment/history`
- **Sources**: Moved to topic-scoped paths (`/api/topics/{topic_id}/sources`)
- **Saved queries**: Moved to topic-scoped paths (`/api/topics/{topic_id}/queries`)
- **Watchlist**: Moved to topic-scoped paths (`/api/topics/{topic_id}/watchlist`)
- **Analyses**: Moved to `GET /api/topics/{topic_id}/analyses`
- **Correlation signals**: Moved to `GET /api/topics/{topic_id}/correlation-signals`
- **Users**: Moved from `/api/users/me` to `/api/me`
- **New endpoints**: `/api/topics/{topic_id}/search/cancel`, `/api/topics/{topic_id}/processing-status`, `/api/models/*`

### Environment Variable Changes

- **New**: `VLLM_FAST_URL`, `FAST_MODEL_NAME`, `EMBEDDING_MODEL_NAME`, `EMBEDDING_DIMENSION`, `EMBEDDER_DEVICE`, `MINIO_URL`, `APP_DB_PASSWORD`, `WORKER_DB_PASSWORD`, `NEXT_PUBLIC_WS_URL`, `INTERNAL_API_URL`
- **Changed default**: `LOCAL_MODEL_NAME`: `QwQ-32B-AWQ` -> `Qwen3-32B-AWQ`
- **Changed default**: `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`: Now sourced from `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`

### Database Changes

- **New table**: `llm_task_config` (migration 007) with RLS policies and grants
- **RLS count**: 15 -> 16 tables (added `llm_task_config`)

### Worker Changes

- **New module**: `llm_router.py` for dual-model task routing
- **New task**: `detect_stalled_pipelines` (every 2 minutes)
- **New function**: `create_fast_client()` in `llm_sync.py`
- **Beat schedule**: 15 -> 16 entries (added `detect-stalled-pipelines`)
- **Ingest task**: Added `_track_skipped_article()` for pipeline progress tracking
- **Embed task**: Added auto-cluster dispatch at 80% embedded threshold
- **Fan-out**: Added staggered countdowns (1s, 1s, 3s, 6s, 10s) to prevent transaction race conditions
- **Trafilatura config**: Custom config with 10s download timeout, 2 max redirects

### Frontend Changes

- **New page**: `/dashboard/models` for model status and task routing
- **New types**: `ModelInfo`, `ModelStatusResponse`, `TaskRoutingEntry`, `TaskRoutingResponse`, `TaskRoutingChange`, `ProcessingStatusResponse`, `SearchStatusResponse`
- **API client**: Added model status and task routing API functions, search cancel, processing status
- **WebSocket**: Added handling for `search_progress` message type
