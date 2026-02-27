# TTwatch Platform -- Comprehensive Technical Documentation

> Generated from full codebase analysis. Every statement is derived from actual source code inspection with file paths and line references.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Technology Stack](#3-technology-stack)
4. [Infrastructure & Deployment](#4-infrastructure--deployment)
5. [Multi-Tenancy Model](#5-multi-tenancy-model)
6. [Database Schema](#6-database-schema)
7. [Authentication & Authorization](#7-authentication--authorization)
8. [API Reference](#8-api-reference)
9. [Background Processing (Celery)](#9-background-processing-celery)
10. [Core Processing Pipeline](#10-core-processing-pipeline)
11. [Frontend](#11-frontend)
12. [Investment Module](#12-investment-module)
13. [Data Flow Diagrams](#13-data-flow-diagrams)
14. [Configuration Reference](#14-configuration-reference)
15. [Implementation Status](#15-implementation-status)
16. [File & Directory Reference](#16-file--directory-reference)
17. [Development Guide](#17-development-guide)
18. [Appendix: Key Design Decisions](#18-appendix-key-design-decisions)

---

## 1. Executive Summary

TTwatch is a **self-hosted, multi-tenant intelligence monitoring platform** that continuously ingests news articles from the web, clusters them into coherent topic groups, generates AI-powered summaries and briefings, performs named entity recognition, tracks sentiment over time, and correlates news signals with financial market data.

### Core Capabilities

- **Automated News Ingestion**: SearXNG-powered web search dispatches articles into a multi-stage processing pipeline with three-layer deduplication (URL, content hash, semantic embedding).
- **AI-Powered Analysis**: Local LLM inference via vLLM (Qwen3-32B-AWQ) generates article summaries, entity extraction, sentiment classification, cluster labels, intelligence briefings, investment analyses, and coverage gap detection.
- **HDBSCAN Clustering**: Articles are clustered using UMAP dimensionality reduction followed by HDBSCAN density-based clustering, enabling automatic theme discovery across a topic's article corpus.
- **Hierarchical Summarization**: Three-tier summarization chain: articles -> article summaries -> cluster summaries -> intelligence briefings.
- **Investment Intelligence**: Entity-to-ticker resolution, watchlist management, market data integration (yfinance/CoinGecko), price alerts with real-time WebSocket delivery, correlation signal detection (sentiment-price divergence).
- **Multi-Tenant Isolation**: PostgreSQL Row-Level Security (RLS) enforced at the database layer, with separate database roles for the API and worker services.
- **Flexible Deployment**: Four deployment modes -- development, GPU-colocated, LAN-distributed, and cloud-only -- all driven by Docker Compose overlay files.

### Key Numbers

| Metric | Value |
|--------|-------|
| Services in Docker Compose | 11 (postgres, qdrant, redis, minio, searxng, api, worker-io, worker-cpu, scheduler, frontend, vllm/embedder) |
| Database Tables | ~30 (15 user-scoped with RLS, ~10 shared reference, plus auth tables) |
| Celery Task Modules | 18 |
| Scheduled Jobs (Beat) | 15 periodic tasks |
| API Routers | 14 (health, auth, topics, articles, clusters, briefings, entities, sentiment, sources, queries, search, investment, market_data, admin, users) |
| Frontend Pages | 9 routes |
| Frontend Components | 12 reusable components |
| Test Files | 5 test modules + conftest |
| LLM Context Window | 32,768 tokens (vLLM --max-model-len) |

---

## 2. Architecture Overview

### High-Level System Architecture

```
                                    +-------------------+
                                    |    Frontend        |
                                    |    (Next.js 14)    |
                                    |    Port 3000       |
                                    +--------+----------+
                                             |
                                    HTTP / WebSocket
                                             |
                                    +--------v----------+
                                    |    API Server      |
                                    |    (FastAPI)       |
                                    |    Port 8080       |
                                    +---+---+---+---+---+
                                        |   |   |   |
               +------------------------+   |   |   +-------------------------+
               |                            |   |                             |
    +----------v--------+    +-------------v---v-----------+    +------------v-----------+
    |   PostgreSQL 16    |    |          Redis 7            |    |      Qdrant v1.12      |
    |   Port 5432        |    |       Port 6379             |    |      Port 6333         |
    |   - RLS Policies   |    |   DB0: Celery Broker        |    |   - articles collection|
    |   - ttwatch_app    |    |   DB1: Celery Results       |    |   - COSINE distance    |
    |   - ttwatch_worker |    |   DB2: URL Dedup SET        |    |   - 1024-dim vectors   |
    +--------------------+    |   DB3: API Cache            |    +------------------------+
                              |   Pub/Sub: price alerts     |
                              +----------+------------------+
                                         |
                          +--------------+--------------+
                          |              |              |
               +----------v---+  +------v------+  +---v----------+
               |  worker-io   |  | worker-cpu  |  |  scheduler   |
               |  gevent x32  |  | prefork x2  |  |  celery beat |
               |  IO-bound    |  | CPU-bound   |  |  15 periodic |
               +--------------+  +-------------+  +--------------+
                          |              |
               +----------v--------------v----------+
               |                                    |
    +----------v--------+           +---------------v------+
    |   MinIO            |           |   SearXNG             |
    |   Port 9000/9001   |           |   Port 8888           |
    |   Object Storage   |           |   Meta-Search Engine  |
    +--------------------+           +------------------------+

                     GPU Services (optional, separate or colocated)
               +------------------+    +---------------------+
               |   vLLM           |    |   Embedder          |
               |   Port 8000/8100 |    |   Port 8001/8101    |
               |   Qwen3-32B-AWQ |    |   Qwen3-Embed-0.6B  |
               +------------------+    +---------------------+
```

### Service Responsibilities

| Service | Role | Source |
|---------|------|--------|
| **api** | FastAPI REST API + WebSocket server | `services/api/` |
| **worker-io** | Celery gevent worker for I/O-bound tasks (search, ingest, embed, summarize, entity extraction, sentiment) | `services/worker/` |
| **worker-cpu** | Celery prefork worker for CPU-bound tasks (clustering, briefings, investment analysis, correlation) | `services/worker/` |
| **scheduler** | Celery Beat -- dispatches periodic tasks on cron schedules | `services/worker/` |
| **frontend** | Next.js 14 React dashboard with dark UI | `services/frontend/` |
| **postgres** | PostgreSQL 16 with RLS, pg_trgm extension, three database roles | Docker image |
| **redis** | Message broker (DB0), result backend (DB1), URL dedup (DB2), API cache (DB3), pub/sub (alerts) | Docker image |
| **qdrant** | Vector database for article embeddings and semantic search | Docker image |
| **minio** | S3-compatible object storage for raw article text | Docker image |
| **searxng** | Privacy-respecting meta-search engine (Google, Bing, DuckDuckGo) | Docker image |
| **vllm** | vLLM OpenAI-compatible inference server for Qwen3-32B-AWQ | Docker image |
| **embedder** | Custom FastAPI service wrapping Qwen3-Embedding-0.6B via sentence-transformers | `services/embedder/` |

---

## 3. Technology Stack

### Backend

| Component | Technology | Version | Source |
|-----------|-----------|---------|--------|
| API Framework | FastAPI | -- | `services/api/requirements.txt` |
| ORM | SQLAlchemy (async) | -- | `services/api/app/deps.py:17-22` |
| Settings | pydantic-settings | -- | `services/api/app/config.py:1` |
| Database | PostgreSQL | 16 | `docker-compose.yml:24` |
| Migrations | Alembic | -- | `config/alembic.ini` |
| Task Queue | Celery | -- | `services/worker/requirements.txt` |
| Task Broker | Redis | 7-alpine | `docker-compose.yml:54` |
| Vector DB | Qdrant | v1.12.1 | `docker-compose.yml:43` |
| Object Storage | MinIO | RELEASE.2024-11-07 | `docker-compose.yml:70` |
| Search Engine | SearXNG | latest | `docker-compose.yml:85` |
| LLM Inference | vLLM | v0.16.0 | `docker-compose.gpu.yml:20` |
| LLM Model | Qwen3-32B-AWQ | -- | `docker-compose.yml:17` |
| Embedding Model | Qwen3-Embedding-0.6B | -- | `docker-compose.yml:18` |
| Embedding Framework | sentence-transformers | -- | `services/embedder/server.py:3` |
| Password Hashing | argon2-cffi (Argon2id) | -- | `services/api/app/auth/router.py:12` |
| HTTP Client | httpx | -- | `services/api/app/services/version_checker.py:8` |
| Content Extraction | trafilatura | -- | `services/worker/worker/tasks/ingest.py` |
| Clustering | hdbscan + umap-learn | -- | `services/worker/worker/tasks/cluster.py` |
| Market Data | yfinance + requests (CoinGecko) | -- | `services/worker/worker/tasks/maintenance.py` |
| Gevent Patching | psycogreen | -- | `services/worker/worker/db.py:5` |
| Retry Logic | tenacity | -- | `services/api/app/services/http_utils.py` |

### Frontend

| Component | Technology | Version | Source |
|-----------|-----------|---------|--------|
| Framework | Next.js | 14.2.29 | `services/frontend/package.json` |
| UI Library | React | 18.3.1 | `services/frontend/package.json` |
| State Management | Zustand | 4.5.5 | `services/frontend/package.json` |
| HTTP Client | Axios | 1.7.9 | `services/frontend/package.json` |
| Charts | Recharts | 2.12.7 | `services/frontend/package.json` |
| D3 Visualizations | d3 | 7.9.0 | `services/frontend/package.json` |
| Icons | lucide-react | 0.468.0 | `services/frontend/package.json` |
| Date Utilities | date-fns | 4.1.0 | `services/frontend/package.json` |
| CSS Framework | Tailwind CSS | 3.4.17 | `services/frontend/package.json` |
| TypeScript | TypeScript | 5.5.4 | `services/frontend/package.json` |
| Build Output | standalone | -- | `services/frontend/next.config.js:2` |

### Infrastructure

| Component | Technology | Notes |
|-----------|-----------|-------|
| Containerization | Docker + Docker Compose V2 | No `version:` key in compose files |
| GPU Runtime | NVIDIA Container Toolkit | `nvidia/cuda:12.4.1-runtime-ubuntu22.04` base for embedder |
| Build Automation | Makefile | 16 targets |
| Database Init | Shell script + SQL | `scripts/init-db.sh` |

---

## 4. Infrastructure & Deployment

### Deployment Modes

TTwatch supports four deployment modes, all sharing the same codebase. Only environment variables and Docker Compose file combinations change.

| Mode | Compose Files | GPU Required | LLM Provider |
|------|--------------|--------------|---------------|
| **Development** | `docker-compose.yml` + `docker-compose.dev.yml` | No | Cloud (OpenAI) |
| **GPU Colocated** | `docker-compose.yml` + `docker-compose.gpu.yml` | Yes (local) | Local (vLLM) |
| **LAN Distributed** | `docker-compose.yml` + `docker-compose.lan.yml` | Yes (remote) | Local (remote vLLM) |
| **Cloud Only** | `docker-compose.yml` + `docker-compose.cloud.yml` | No | Cloud (OpenAI/Anthropic) |

**Source**: `Makefile` targets define each mode:

```
dev:        docker-compose.yml + docker-compose.dev.yml
dev-gpu:    docker-compose.yml + docker-compose.gpu.yml + docker-compose.dev.yml
prod:       docker-compose.yml
gpu:        docker-compose.yml + docker-compose.gpu.yml
lan:        docker-compose.yml + docker-compose.lan.yml
cloud:      docker-compose.yml + docker-compose.cloud.yml
gpu-node:   docker-compose.gpu-node.yml  (standalone)
search-node: docker-compose.search-node.yml  (standalone)
```

### Docker Compose File Architecture

**`docker-compose.yml`** (main, lines 1-216):
- Defines the `x-common-env` YAML anchor with all shared environment variables
- 9 core services: postgres, qdrant, redis, minio, searxng, api, worker-io, worker-cpu, scheduler, frontend
- Named volumes: pgdata, qdrant_data, redis_data, minio_data

**`docker-compose.gpu.yml`** (GPU overlay, lines 1-73):
- Adds `embedder` and `vllm` services with GPU device reservations
- Maps ports to 8101:8001 (embedder) and 8100:8000 (vllm) to avoid conflicts
- Overrides `api`, `worker-io`, `worker-cpu` to depend on vllm and embedder health

**`docker-compose.dev.yml`** (development overlay):
- API: adds `--reload` to uvicorn, mounts source code
- Workers: uses `watchmedo auto-restart` for auto-reload
- Frontend: enables `WATCHPACK_POLLING=true` for WSL compatibility
- Worker: single `solo` pool (no concurrency, easier debugging)

**`docker-compose.cloud.yml`** (cloud overlay):
- Sets `VLLM_URL` and `EMBEDDER_URL` to empty strings (disables local GPU services)
- Injects cloud LLM configuration: `CLOUD_LLM_API_KEY`, `CLOUD_LLM_MODEL`, `CLOUD_EMBEDDING_MODEL`

**`docker-compose.lan.yml`** (LAN overlay):
- Disables local searxng via Docker profiles (`profiles: ["disabled"]`)
- Removes searxng from api/worker dependency chains

**`docker-compose.gpu-node.yml`** (standalone GPU node):
- Only `embedder` (port 8001) and `vllm` (port 8000)
- Designed to run on a separate GPU machine on the LAN

**`docker-compose.search-node.yml`** (standalone search node):
- Only `searxng` (port 8080)

### Port Mapping

| Service | Internal Port | External Port (main) | External Port (gpu overlay) |
|---------|--------------|---------------------|---------------------------|
| postgres | 5432 | 5432 | -- |
| qdrant | 6333 | 6333 | -- |
| redis | 6379 | 6379 | -- |
| minio | 9000, 9001 | 9000, 9001 | -- |
| searxng | 8080 | 8888 | -- |
| api | 8080 | 8080 | -- |
| frontend | 3000 | 3000 | -- |
| vllm | 8000 | -- | 8100 |
| embedder | 8001 | -- | 8101 |

### Dockerfiles

**API** (`services/api/Dockerfile`):
- Multi-stage build, copies requirements first for layer caching
- Installs dependencies, copies application code
- Runs Alembic migrations on startup, then uvicorn

**Worker** (`services/worker/Dockerfile`):
- Shares the same dependency base as the API
- Entry point is overridden by docker-compose command (celery worker or celery beat)

**Frontend** (`services/frontend/Dockerfile`):
- Multi-stage: Node.js build stage -> production stage with standalone output
- Uses `next.config.js` `output: "standalone"` for minimal production image

**Embedder** (`services/embedder/Dockerfile`):
- Base: `nvidia/cuda:12.4.1-runtime-ubuntu22.04`
- Installs Python, torch, sentence-transformers
- Runs `server.py` via uvicorn on port 8001

### Operational Scripts

| Script | Purpose | Source |
|--------|---------|--------|
| `scripts/init-db.sh` | Creates pg_trgm extension, ttwatch_app and ttwatch_worker roles with passwords from env vars | `scripts/init-db.sh` |
| `scripts/create-admin-user.py` | Interactive admin user creation with password validation (10+ chars, upper/lower/digit) | `scripts/create-admin-user.py` |
| `scripts/seed-topics.py` | Seeds 5 example topics (AI Safety, Biotech, Semiconductors, Renewables, Cybersecurity) | `scripts/seed-topics.py` |
| `scripts/backup.sh` | PostgreSQL pg_dump + Qdrant collection snapshots to `backups/` directory | `scripts/backup.sh` |
| `scripts/restore.sh` | Restores `.dump` (pg_restore) or `.snapshot` (Qdrant upload) files | `scripts/restore.sh` |
| `scripts/update.sh` | Safe update: backup -> git pull --ff-only -> rebuild -> migrate -> restart with health checks | `scripts/update.sh` |
| `scripts/download-models.sh` | Downloads model weights for local inference | `scripts/download-models.sh` |
| `scripts/benchmark-gpu.py` | Tests vLLM inference and embedding latency | `scripts/benchmark-gpu.py` |

---

## 5. Multi-Tenancy Model

### Overview

TTwatch implements multi-tenancy using **PostgreSQL Row-Level Security (RLS)** enforced at the database layer. This ensures data isolation even if application-level bugs occur -- the database itself prevents cross-tenant data access.

### Database Roles

Three PostgreSQL roles are created by `scripts/init-db.sh`:

| Role | Purpose | Permissions |
|------|---------|------------|
| `postgres` | Superuser (migrations only) | Full access, RLS bypassed |
| `ttwatch_app` | API service role | CRUD on user-scoped + auth tables; READ-ONLY on shared reference tables |
| `ttwatch_worker` | Celery worker role | Full access to ALL tables (RLS bypassed via policy) |

**Source**: `scripts/init-db.sh`, `migrations/versions/005_grants_app_role.py`, `migrations/versions/006_grants_worker_role.py`

### RLS Implementation

**Migration 004** (`migrations/versions/004_add_rls_policies.py`) enables RLS on 15 user-scoped tables:

```
topics, sources, clusters, articles, entities, entity_article_map,
entity_cluster_map, sentiment_history, saved_queries, briefings,
asset_mappings, investment_analyses, watchlist_items, price_alerts,
correlation_signals
```

Two policies are created per table:

1. **`user_isolation`** (for `ttwatch_app` role):
   ```sql
   USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
   WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID)
   ```

2. **`worker_bypass`** (for `ttwatch_worker` role):
   ```sql
   USING (true) WITH CHECK (true)
   ```

### RLS Context Setting

**API Side** (`services/api/app/deps.py`, `get_current_user` dependency):
Every authenticated request sets the RLS context via:
```sql
SET LOCAL ttwatch.current_user_id = '<user-uuid>'
```
This is executed inside the database session that handles the request. `SET LOCAL` ensures it applies only to the current transaction.

**Worker Side** (`services/worker/worker/rls.py`, `with_rls_context` decorator):
Every Celery task that operates on user data uses the `@with_rls_context` decorator, which:
1. Validates the `user_id` parameter as a UUID
2. Opens a database session
3. Executes `SET LOCAL ttwatch.current_user_id` before yielding the session
4. Handles both bound tasks (`self, user_id`) and unbound tasks (`user_id`)

### Shared Reference Tables (No RLS)

These tables have no `user_id` column and are accessible to all users (read-only for `ttwatch_app`):

- `ticker_reference` -- Stock/ETF/crypto reference data
- `theme_etf_map` -- Theme-to-ETF mappings
- `market_data_cache` -- Cached market prices
- `price_history` -- Historical OHLCV data

**Source**: `migrations/versions/003_create_investment_tables.py`, `migrations/versions/005_grants_app_role.py`

---

## 6. Database Schema

### Migration History

| Migration | Description | Source |
|-----------|------------|--------|
| 001 | Users, API keys, refresh tokens | `migrations/versions/001_create_users_and_auth.py` |
| 002 | Intelligence tables (topics, sources, clusters, articles, entities, sentiment, briefings) | `migrations/versions/002_create_intelligence_tables.py` |
| 003 | Investment tables (ticker_reference, market_data_cache, price_history, asset_mappings, investment_analyses, watchlist_items, price_alerts, correlation_signals) | `migrations/versions/003_create_investment_tables.py` |
| 004 | RLS policies on 15 tables | `migrations/versions/004_add_rls_policies.py` |
| 005 | Grants for ttwatch_app role | `migrations/versions/005_grants_app_role.py` |
| 006 | Grants for ttwatch_worker role | `migrations/versions/006_grants_worker_role.py` |

### Entity-Relationship Diagram (Core Tables)

```
users
  |-- 1:N --> topics
  |             |-- 1:N --> sources
  |             |-- 1:N --> clusters
  |             |             |-- 1:N --> articles (via cluster_id FK)
  |             |-- 1:N --> articles
  |             |             |-- M:N --> entities (via entity_article_map)
  |             |             |-- N:1 --> articles (duplicate_of FK)
  |             |-- 1:N --> entities
  |             |             |-- M:N --> clusters (via entity_cluster_map)
  |             |-- 1:N --> sentiment_history
  |             |-- 1:N --> saved_queries
  |             |-- 1:N --> briefings
  |             |-- 1:N --> asset_mappings
  |             |-- 1:N --> investment_analyses
  |             |-- 1:N --> watchlist_items
  |             |-- 1:N --> correlation_signals
  |-- 1:N --> price_alerts
  |-- 1:N --> api_keys
  |-- 1:N --> refresh_tokens
```

### Table Details

#### Authentication Tables

**`users`** (migration 001):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK, server default uuid4 |
| email | String(255) | UNIQUE, NOT NULL |
| display_name | String(100) | NOT NULL |
| password_hash | String(255) | NOT NULL, Argon2id |
| is_active | Boolean | default True |
| is_admin | Boolean | default False |
| max_topics | Integer | default 10 |
| max_articles_per_topic | Integer | default 5000 |
| max_api_keys | Integer | default 5 |
| created_at | DateTime(tz) | server default now() |
| last_login_at | DateTime(tz) | nullable |

**`api_keys`** (migration 001):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK -> users.id |
| key_prefix | String(20) | Format: `tw_live_{short_id}_1` |
| key_hash | String(64) | SHA-256 of full key |
| label | String(100) | User-defined label |
| scopes | JSONB | Permission scopes |
| rate_limit_per_minute | Integer | default 60 |
| is_active | Boolean | default True |
| last_used_at | DateTime(tz) | nullable |
| created_at | DateTime(tz) | |
| expires_at | DateTime(tz) | nullable |

**`refresh_tokens`** (migration 001):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK -> users.id |
| token_hash | String(64) | SHA-256 |
| device_info | String(255) | nullable |
| expires_at | DateTime(tz) | 30-day expiry |
| created_at | DateTime(tz) | |

#### Intelligence Tables

**`topics`** (migration 002):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| name | String(200) | |
| icon | String(10) | nullable, emoji |
| config | JSONB | `{search_terms: [], search_engines: [], max_results_per_query: int, language: str}` |
| refresh_interval_minutes | Integer | default 120 |
| last_refreshed_at | DateTime(tz) | nullable |
| next_refresh_at | DateTime(tz) | nullable |

**`articles`** (migration 002):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK -> topics.id |
| url | String(2048) | UNIQUE(user_id, topic_id, url) |
| title | String(500) | |
| source_name | String(200) | nullable |
| source_url | String(2048) | nullable |
| published_at | DateTime(tz) | nullable |
| ingested_at | DateTime(tz) | server default now() |
| content_hash | String(64) | SHA-256 of content |
| raw_storage_key | String(500) | MinIO object key |
| summary | Text | LLM-generated |
| sentiment_score | Float | -1.0 to 1.0 |
| relevance_score | Float | nullable |
| key_quotes | JSONB | nullable |
| cluster_id | UUID | FK -> clusters.id, nullable |
| embedding_id | String(100) | Qdrant point UUID |
| is_duplicate | Boolean | default False |
| duplicate_of | UUID | FK -> articles.id, nullable |

**`clusters`** (migration 002):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK -> topics.id |
| keyword | String(200) | LLM-generated 2-4 word label |
| color | String(7) | Hex color from 15-color palette |
| article_count | Integer | default 0 |
| trend_score | Float | `recent*3 + previous*1` |
| velocity | String(20) | surging/rising/stable/declining |

**`entities`** (migration 002):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK -> topics.id |
| name | String(200) | |
| type | String(50) | person/org/product/location/event/technology |
| first_seen | DateTime(tz) | server default now() |

**`sentiment_history`** (migration 002):

| Column | Type | Notes |
|--------|------|-------|
| id | BigInteger | PK, auto-increment |
| user_id | UUID | RLS-scoped |
| topic_id | UUID | FK |
| cluster_id | UUID | nullable (recluster-proof) |
| cluster_keyword | String(200) | Preserved across reclusters |
| period_start | Date | |
| avg_sentiment | Float | |
| article_count | Integer | |
| UNIQUE | | (user_id, topic_id, cluster_keyword, period_start) |

**`briefings`** (migration 002):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK -> topics.id |
| generated_at | DateTime(tz) | server default now() |
| summary | Text | Executive summary |
| highlights | JSONB | Key bullet points |
| new_entities | JSONB | Entities from last 24h |
| watch_items | JSONB | Items requiring attention |
| coverage_gaps | JSONB | Uncovered areas |
| input_tokens | Integer | nullable |
| output_tokens | Integer | nullable |
| model_used | String(100) | nullable |

#### Investment Tables

**`ticker_reference`** (migration 003, shared -- no RLS):

| Column | Type | Notes |
|--------|------|-------|
| symbol | String(20) | PK |
| name | String(200) | |
| exchange | String(20) | nullable |
| asset_type | String(20) | stock/etf/crypto/commodity |
| sector | String(100) | nullable |
| industry | String(100) | nullable |
| market_cap_tier | String(20) | nullable |
| is_active | Boolean | default True |
| metadata | JSONB | nullable |

**`market_data_cache`** (migration 003, shared):

| Column | Type | Notes |
|--------|------|-------|
| symbol | String(20) | PK |
| asset_type | String(20) | |
| price | Numeric(20,6) | nullable |
| price_change_pct | Float | nullable |
| volume | BigInteger | nullable |
| market_cap | Numeric(20,2) | nullable |
| pe_ratio | Float | nullable |
| eps | Float | nullable |
| dividend_yield | Float | nullable |
| beta | Float | nullable |
| fifty_two_week_high | Numeric(20,6) | nullable |
| fifty_two_week_low | Numeric(20,6) | nullable |
| data_source | String(50) | yfinance/coingecko |
| is_stale | Boolean | default False |
| fetched_at | DateTime(tz) | server default now() |

**`price_history`** (migration 003, shared):

| Column | Type | Notes |
|--------|------|-------|
| symbol | String(20) | Composite PK |
| trade_date | Date | Composite PK |
| open | Numeric(20,6) | |
| high | Numeric(20,6) | |
| low | Numeric(20,6) | |
| close | Numeric(20,6) | |
| volume | BigInteger | |

**`asset_mappings`** (migration 003, user-scoped):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK |
| entity_id | UUID | FK -> entities.id |
| ticker_ref_id | String | FK -> ticker_reference.symbol, nullable |
| entity_name | String(200) | Denormalized |
| resolved_symbol | String(20) | nullable |
| resolution_method | String(50) | reference_lookup / llm_inference |
| confidence | Float | 0.0 to 1.0 |
| is_verified | Boolean | default False |

**`investment_analyses`** (migration 003, user-scoped):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK |
| analysis_scope | String(20) | CHECK IN (asset, cluster, topic) |
| scope_ref_id | UUID | nullable |
| symbol | String(20) | nullable |
| analysis_text | Text | LLM-generated |
| recommendation | String(50) | bullish/bearish/neutral |
| confidence | Float | |
| key_signals | JSONB | |
| risk_factors | JSONB | |
| articles_considered | Integer | |
| market_data_cache_id | String | FK, nullable |
| sentiment_score | Float | nullable |
| technical_signals | JSONB | nullable |
| model_used | String(100) | nullable |

**`price_alerts`** (migration 003, user-scoped):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| symbol | String(20) | |
| condition | String(20) | CHECK IN (above, below, crosses_above, crosses_below) |
| threshold | Numeric(20,6) | |
| last_known_price | Numeric(20,6) | nullable |
| is_active | Boolean | default True |
| triggered_at | DateTime(tz) | nullable |
| created_at | DateTime(tz) | server default now() |

**`correlation_signals`** (migration 003, user-scoped):

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK, RLS-scoped |
| topic_id | UUID | FK |
| cluster_id | UUID | FK -> clusters.id, nullable |
| symbol | String(20) | |
| signal_type | String(50) | sentiment_price_divergence, momentum_confirmation, etc. |
| signal_strength | Float | 0.0 to 1.0 |
| description | Text | nullable |
| detected_at | DateTime(tz) | server default now() |

---

## 7. Authentication & Authorization

### Authentication Methods

TTwatch supports two authentication methods:

1. **JWT Bearer Tokens** -- Primary method for the frontend
2. **API Keys** -- For programmatic access (agents, scripts)

**Source**: `services/api/app/deps.py`, `get_current_user` function (lines 55-110)

### JWT Token Lifecycle

| Token | Expiry | Storage |
|-------|--------|---------|
| Access Token | 15 minutes | Client localStorage |
| Refresh Token | 30 days | Client localStorage + DB (hashed) |

**Source**: `services/api/app/auth/router.py:20-21`

### Password Security

- **Algorithm**: Argon2id (via argon2-cffi)
- **Parameters**: `time_cost=3, memory_cost=65536 (64 MB), parallelism=4`
- **Validation**: Minimum 10 characters, at least 1 uppercase, 1 lowercase, 1 digit
- **Rehashing**: On login, existing hashes are checked against current parameters and rehashed if needed

**Source**: `services/api/app/auth/router.py:12-14`, `services/api/app/auth/schemas.py`

### Authentication Flow

```
Client                          API                          Database
  |                              |                              |
  |-- POST /auth/register ------>|                              |
  |                              |-- Validate password rules -->|
  |                              |-- Argon2id hash ----------->|
  |                              |-- Insert user + tokens ----->|
  |<-- {access, refresh} --------|                              |
  |                              |                              |
  |-- POST /auth/login --------->|                              |
  |                              |-- Verify Argon2id hash ----->|
  |                              |-- Check rehash needed ------>|
  |                              |-- Cap refresh tokens (10) -->|
  |<-- {access, refresh} --------|                              |
  |                              |                              |
  |-- Bearer <access> ---------> |                              |
  |                              |-- Decode JWT -------------->|
  |                              |-- SET LOCAL ttwatch.current_user_id
  |                              |-- Process request ---------->|
  |                              |                              |
  |-- POST /auth/refresh ------->|                              |
  |                              |-- Verify hash + expiry ----->|
  |                              |-- DELETE old token --------->|
  |                              |-- Issue new pair ----------->|
  |<-- {new_access, new_refresh}-|                              |
```

### API Key Format

Keys follow the format: `tw_live_{short_id}_{random32}`

- `tw_live_` -- Fixed prefix identifying TTwatch live keys
- `{short_id}` -- First 8 chars of UUID for quick visual identification
- `{random32}` -- 32-character cryptographically random string

Only the SHA-256 hash is stored. The full key is returned once on creation and cannot be retrieved again.

**Source**: `services/api/app/routers/users.py`

### Rate Limiting

- **Algorithm**: Sliding window via Lua script executed atomically in Redis
- **Default**: 60 requests per 60 seconds per user per endpoint
- **Redis Key Format**: `ttwatch:rate:{user_id}:{endpoint}`
- **API Key Override**: API keys have their own `rate_limit_per_minute` field (default 60)

**Source**: `services/api/app/middleware/rate_limit.py`

---

## 8. API Reference

### Base URL

`http://localhost:8080`

### Authentication

All authenticated endpoints accept either:
- `Authorization: Bearer <jwt>` header
- `X-API-Key: tw_live_...` header

### Endpoint Summary

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/register` | No | Register new user |
| POST | `/auth/login` | No | Login |
| POST | `/auth/refresh` | No | Rotate refresh token |
| POST | `/auth/logout` | No | Invalidate refresh token |
| GET | `/api/me` | Yes | Get current user profile |
| PUT | `/api/me` | Yes | Update display name |
| GET | `/api/me/api-keys` | Yes | List API keys |
| POST | `/api/me/api-keys` | Yes | Create API key |
| DELETE | `/api/me/api-keys/{id}` | Yes | Revoke API key |
| GET | `/api/topics` | Yes | List topics |
| POST | `/api/topics` | Yes | Create topic |
| GET | `/api/topics/{id}` | Yes | Get topic |
| PUT | `/api/topics/{id}` | Yes | Update topic |
| DELETE | `/api/topics/{id}` | Yes | Delete topic |
| GET | `/api/topics/{id}/clusters` | Yes | List clusters |
| GET | `/api/topics/{id}/articles` | Yes | List articles (filtered, paginated) |
| GET | `/api/articles/{id}` | Yes | Get article detail |
| GET | `/api/articles/{id}/entities` | Yes | Get article entities |
| GET | `/api/clusters/{id}` | Yes | Get cluster |
| GET | `/api/clusters/{id}/articles` | Yes | List cluster articles |
| POST | `/api/search` | Yes | Semantic search |
| GET | `/api/topics/{id}/briefings` | Yes | List briefings |
| GET | `/api/briefings/{id}` | Yes | Get briefing |
| POST | `/api/topics/{id}/briefings/generate` | Yes | Trigger briefing |
| GET | `/api/topics/{id}/entities` | Yes | List entities |
| GET | `/api/topics/{id}/sentiment/history` | Yes | Sentiment time series |
| GET | `/api/topics/{id}/sources` | Yes | List sources |
| POST | `/api/topics/{id}/sources` | Yes | Add source |
| GET | `/api/topics/{id}/watchlist` | Yes | List watchlist |
| POST | `/api/topics/{id}/watchlist` | Yes | Add to watchlist |
| DELETE | `/api/watchlist/{id}` | Yes | Remove from watchlist |
| GET | `/api/topics/{id}/analyses` | Yes | List investment analyses |
| GET | `/api/topics/{id}/correlation-signals` | Yes | List correlation signals |
| POST | `/api/price-alerts` | Yes | Create price alert |
| GET | `/api/price-alerts` | Yes | List price alerts |
| DELETE | `/api/price-alerts/{id}` | Yes | Delete price alert |
| GET | `/api/market-data/{symbol}` | Yes | Get market data |
| GET | `/api/market-data/{symbol}/history` | Yes | Get price history |
| GET | `/api/topics/{id}/asset-mappings` | Yes | List asset mappings |
| POST | `/api/asset-mappings/{id}/verify` | Yes | Verify mapping |
| DELETE | `/api/asset-mappings/{id}` | Yes | Reject mapping |
| GET | `/health` | No | Basic health check |
| GET | `/health/services` | No | Extended service health |
| GET | `/api/admin/versions` | Admin | Get version status |
| POST | `/api/admin/versions/check` | Admin | Trigger version check |
| WS | `/ws` | JWT | Real-time updates |

### WebSocket Protocol

**Connection**: `ws://host:8080/ws`

**Authentication Flow**:
1. Client connects
2. Client sends: `{"type": "auth", "token": "<jwt>"}`
3. Server responds: `{"type": "connected", "user_id": "..."}`

**Keepalive**: Server sends `{"type": "ping"}` every 30 seconds. Client must respond with `{"type": "pong"}`. Connection is dropped after 90 seconds of inactivity.

**Event Types**:
- `article_ingested` -- New article processed
- `cluster_updated` -- Clusters recalculated
- `briefing_ready` -- New briefing available
- `alert` with `alert_type: "price_alert"` -- Price threshold crossed

**Source**: `services/api/app/main.py:75-160`

### Error Format

All errors return:
```json
{ "detail": "Error description" }
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request |
| 401 | Authentication required |
| 403 | Forbidden (limit reached) |
| 404 | Not found |
| 409 | Conflict (duplicate) |
| 422 | Validation error |
| 429 | Rate limit exceeded |

---

## 9. Background Processing (Celery)

### Architecture

TTwatch uses a dual-worker architecture with task routing to optimize resource utilization:

| Worker | Pool | Concurrency | Queue | Task Types |
|--------|------|------------|-------|------------|
| worker-io | gevent | 32 | `ttwatch:default` | I/O-bound: search, ingest, embed, summarize, entity extraction, sentiment, market data fetch |
| worker-cpu | prefork | 2 | `ttwatch:compute` | CPU-bound: clustering, trends, sentiment aggregation, briefings, coverage gaps, investment analysis, correlation detection |

**Source**: `docker-compose.yml:116-170`, `services/worker/worker/celeryconfig.py:14-28`

### Task Routing

```python
# services/worker/worker/celeryconfig.py:14-28
task_routes = {
    'worker.tasks.cluster.recluster_topic':             {'queue': 'ttwatch:compute'},
    'worker.tasks.trends.update_trends':                {'queue': 'ttwatch:compute'},
    'worker.tasks.sentiment_agg.compute_sentiment_history': {'queue': 'ttwatch:compute'},
    'worker.tasks.coverage_gaps.detect_coverage_gaps':  {'queue': 'ttwatch:compute'},
    'worker.tasks.briefing.generate_briefing':          {'queue': 'ttwatch:compute'},
    'worker.tasks.investment_analysis.generate_investment_analyses': {'queue': 'ttwatch:compute'},
    'worker.tasks.correlation_signals.detect_correlation_signals':   {'queue': 'ttwatch:compute'},
}
# All other tasks default to 'ttwatch:default' (IO worker)
```

### Beat Schedule (15 Periodic Tasks)

| Task | Schedule | Queue | Description |
|------|----------|-------|-------------|
| `schedule_searches` | Every 2 hours | default | Fan-out topic searches to SearXNG |
| `schedule_reclustering` | Every 2 hours | default -> compute | Trigger HDBSCAN reclustering |
| `schedule_trend_updates` | Every 1 hour | default -> compute | Update trend scores and velocity |
| `schedule_briefings` | Every 6 hours | default -> compute | Generate intelligence briefings |
| `schedule_coverage_gaps` | Every 12 hours | default -> compute | Detect coverage gaps in briefings |
| `schedule_sentiment_history` | Every 2 hours | default -> compute | Aggregate daily sentiment |
| `refresh_market_data` | Every 30 min | default | Fetch prices from yfinance/CoinGecko |
| `schedule_investment_analyses` | Daily 6:00 AM | default -> compute | Generate investment reports |
| `schedule_correlation_signals` | Every 4 hours | default -> compute | Detect sentiment-price divergence |
| `check_price_alerts` | Every 15 min | default | Evaluate price alert conditions |
| `cleanup_stale_market_data` | Daily 3:00 AM | default | Prune old market data |
| `cleanup_stale_snapshots` | Daily 3:30 AM | default | Prune old briefings and analyses |
| `cleanup_expired_refresh_tokens` | Daily 2:30 AM | default | Remove expired auth tokens |
| `cleanup_orphaned_qdrant` | Daily 4:00 AM | default | Remove Qdrant points without PG articles |
| `check_service_versions` | Daily 6:30 AM | default | Check for service updates |

**Source**: `services/worker/worker/celeryconfig.py:36-122`

### Task Inventory (18 Modules)

| Module | Key Tasks | Source |
|--------|-----------|--------|
| `search.py` | `run_topic_search` -- Builds queries from topic config, queries SearXNG, fans out `ingest_article` | `services/worker/worker/tasks/search.py` |
| `ingest.py` | `ingest_article` -- URL dedup (Redis SET), fetch+extract (trafilatura), content hash dedup (SHA-256), store in MinIO, fan-out to summarize/embed/entities/sentiment | `services/worker/worker/tasks/ingest.py` |
| `embed.py` | `embed_article` -- Generate embedding, upsert to Qdrant, semantic dedup (cosine > 0.92) | `services/worker/worker/tasks/embed.py` |
| `summarize.py` | `summarize_article` -- 2-sentence summary via LLM | `services/worker/worker/tasks/summarize.py` |
| `entities.py` | `extract_entities` -- LLM extracts up to 15 entities, upserts Entity records, fans out `resolve_entity_ticker` | `services/worker/worker/tasks/entities.py` |
| `sentiment.py` | `classify_sentiment` -- LLM classifies -1.0 to 1.0 | `services/worker/worker/tasks/sentiment.py` |
| `cluster.py` | `recluster_topic` -- UMAP (20 dims) + HDBSCAN (min_cluster_size=5), LLM label generation | `services/worker/worker/tasks/cluster.py` |
| `briefing.py` | `generate_briefing` -- Tier 3 hierarchical summarization, caps at 12 clusters | `services/worker/worker/tasks/briefing.py` |
| `trends.py` | `update_trends` -- Compute trend_score = recent*3 + previous*1, velocity labels | `services/worker/worker/tasks/trends.py` |
| `sentiment_agg.py` | `compute_sentiment_history` -- Daily per-cluster sentiment average upsert | `services/worker/worker/tasks/sentiment_agg.py` |
| `coverage_gaps.py` | `detect_coverage_gaps` -- LLM identifies 3-5 uncovered areas | `services/worker/worker/tasks/coverage_gaps.py` |
| `resolve_ticker.py` | `resolve_entity_ticker` -- Step 1: reference lookup (ILIKE), Step 2: LLM inference (confidence >= 0.6) | `services/worker/worker/tasks/resolve_ticker.py` |
| `investment_analysis.py` | `generate_investment_analyses` -- LLM generates analysis per resolved asset | `services/worker/worker/tasks/investment_analysis.py` |
| `price_alerts.py` | `check_price_alerts` -- Evaluates conditions, publishes to Redis pub/sub | `services/worker/worker/tasks/price_alerts.py` |
| `correlation_signals.py` | `detect_correlation_signals` -- Compares 48h sentiment vs. price movement | `services/worker/worker/tasks/correlation_signals.py` |
| `maintenance.py` | Cleanup tasks: stale market data, old snapshots, expired tokens, orphaned Qdrant points; `fetch_market_data` (yfinance + CoinGecko) | `services/worker/worker/tasks/maintenance.py` |
| `periodic.py` | 9 beat dispatch tasks that fan out per-user/per-topic work | `services/worker/worker/tasks/periodic.py` |
| `version_check.py` | `check_service_versions` -- Queries GitHub/DockerHub/HuggingFace APIs | `services/worker/worker/tasks/version_check.py` |
| `utils.py` | `fetch_article_text` -- Reads raw text from MinIO | `services/worker/worker/tasks/utils.py` |

### Database Access Pattern

Workers use **synchronous** SQLAlchemy with `psycogreen` for gevent compatibility:

```python
# services/worker/worker/db.py
from psycogreen.gevent import patch_psycopg
patch_psycopg()

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
)
```

The `with_rls_context` decorator wraps each task with a session that sets the RLS context before execution.

---

## 10. Core Processing Pipeline

### Article Ingestion Pipeline

```
schedule_searches (beat, every 2h)
       |
       v
run_topic_search (per topic)
  - Build queries: topic.name + topic.config.search_terms
  - Query SearXNG /search?format=json
  - Deduplicate URLs within results
  - For each unique URL:
       |
       v
ingest_article (per URL, max_retries=2)
  |
  +-- Layer 1: URL Dedup (Redis SET "ttwatch:seen_urls:{user}:{topic}")
  |     If seen -> skip
  |
  +-- Fetch article (httpx, 20s timeout)
  +-- Extract content (trafilatura: include_tables, favor_precision)
  +-- Extract metadata: title, published_at from HTML
  |
  +-- Layer 2: Content Hash Dedup (SHA-256 of extracted text)
  |     Query PG for matching content_hash -> mark is_duplicate
  |
  +-- Store raw text in MinIO (key: "{user_id}/{topic_id}/{hash}.txt")
  +-- Insert Article record in PostgreSQL
  |
  +-- Fan-out 4 parallel tasks:
       |        |          |            |
       v        v          v            v
  summarize  embed    entities    sentiment
  _article   _article  _extract    _classify
```

**Source**: `services/worker/worker/tasks/ingest.py`, `services/worker/worker/tasks/search.py`

### Three-Layer Deduplication

| Layer | Mechanism | Scope | Speed | Source |
|-------|-----------|-------|-------|--------|
| 1. URL | Redis SET membership check | Per user + topic | O(1) | `ingest.py` -- `SADD/SISMEMBER` on `ttwatch:seen_urls:{user_id}:{topic_id}` |
| 2. Content Hash | SHA-256 of extracted text | Per user + topic | O(1) lookup | `ingest.py` -- Query `content_hash` column |
| 3. Semantic | Qdrant cosine similarity > 0.92 | Per user + topic | ~O(log n) ANN | `embed.py` -- Search with `score_threshold=0.92` |

### Embedding Pipeline

```
embed_article
  |
  +-- Build text: title + first 1500 chars of content
  +-- POST /embed to embedder service (batch_size=64)
  +-- Upsert to Qdrant "articles" collection:
  |     point_id = article UUID
  |     vector = 1024-dim float
  |     payload = {user_id, topic_id, title, source, ingested_at}
  |
  +-- Layer 3 Semantic Dedup:
       Search Qdrant (same user+topic filter, score_threshold=0.92)
       If similar article found -> mark is_duplicate, set duplicate_of
```

**Source**: `services/worker/worker/tasks/embed.py`

### Clustering Pipeline

```
recluster_topic (CPU queue, every 2h)
  |
  +-- Phase 1: Scroll Qdrant (payloads only) to get article IDs
  +-- Phase 2: Scroll top 2000 articles by ingested_at (with vectors)
  |
  +-- UMAP dimensionality reduction (1024-dim -> 20-dim)
  |     n_neighbors=15, min_dist=0.1, metric='cosine'
  |
  +-- HDBSCAN clustering
  |     min_cluster_size=5, min_samples=3, metric='euclidean'
  |
  +-- Preserve sentiment_history (SET cluster_id = NULL before delete)
  +-- Delete old cluster records
  +-- Create new cluster records
  |
  +-- For each cluster:
  |     Gather article titles (max 20)
  |     LLM generates 2-4 word keyword label
  |     Assign color from 15-color palette
  |
  +-- Update article.cluster_id assignments
  +-- Update cluster.article_count
```

**Cluster Colors** (15 predefined, `services/worker/worker/tasks/cluster.py`):
```
#3B82F6, #10B981, #F59E0B, #EF4444, #8B5CF6,
#EC4899, #06B6D4, #F97316, #14B8A6, #A855F7,
#6366F1, #22C55E, #E11D48, #0EA5E9, #D946EF
```

### Hierarchical Summarization

```
Tier 1: Article Summaries
  summarize_article -> 2-sentence summary (max_tokens=200)
  Input: first 2000 chars of article content
  Output: stored in articles.summary

Tier 2: Cluster Context (built at briefing time)
  For each cluster: collect article summaries (max 20 per cluster)
  Combined into cluster-level context for the briefing prompt

Tier 3: Intelligence Briefing
  generate_briefing:
  - Caps at 12 clusters (by trend_score)
  - Max 20 non-duplicate articles per cluster (with summaries)
  - Includes new entities from last 24 hours
  - LLM generates JSON: {summary, highlights, watch_items}
  - Stored in briefings table
```

**Source**: `services/worker/worker/tasks/briefing.py`, `services/worker/worker/tasks/summarize.py`

### Trend Scoring

```
update_trends (hourly)
  For each cluster:
    recent_count = articles from last 24h
    previous_count = articles from 24-48h ago
    trend_score = (recent_count * 3) + (previous_count * 1)

    velocity = based on ratio (recent_count / max(previous_count, 1)):
      >= 2.0 -> "surging"
      >= 1.2 -> "rising"
      >= 0.8 -> "stable"
      <  0.8 -> "declining"
```

**Source**: `services/worker/worker/tasks/trends.py`

---

## 11. Frontend

### Technology

- **Framework**: Next.js 14.2 (App Router)
- **Build**: Standalone output (`next.config.js`)
- **Styling**: Tailwind CSS 3.4 with custom design tokens
- **State**: Zustand 4.5
- **HTTP**: Axios with JWT auto-refresh interceptor
- **Charts**: Recharts (bar, line, composed charts)
- **Visualizations**: D3.js 7.9 (force-directed bubble clusters, entity networks)
- **Icons**: lucide-react
- **Dates**: date-fns

### Design System

**Dark theme** defined in `services/frontend/src/lib/design-tokens.ts` and `services/frontend/tailwind.config.ts`:

| Token | Value | Usage |
|-------|-------|-------|
| surface.DEFAULT | `#0f1117` | Page background |
| surface.raised | `#161923` | Card backgrounds |
| surface.overlay | `#1e2130` | Hover states |
| surface.border | `#2a2d3e` | Borders |
| accent.DEFAULT | `#3B82F6` | Primary interactive (blue) |
| accent.hover | `#2563EB` | Primary hover |
| font.sans | Inter, system-ui | Body text |
| font.mono | JetBrains Mono, Fira Code | Code, numbers |

**Reusable CSS Classes** (`services/frontend/src/app/globals.css`):
- `.card` -- raised surface with border, rounded corners
- `.input-field` -- dark input with focus ring
- `.btn-primary` -- blue accent button
- `.btn-ghost` -- transparent hover button
- `.animate-slide-in` -- right-to-left slide animation (0.25s)

### Route Structure

| Route | Page | Description |
|-------|------|-------------|
| `/` | `app/page.tsx` | Redirects to `/dashboard` |
| `/login` | `app/login/page.tsx` | Login form |
| `/register` | `app/register/page.tsx` | Registration with password rules |
| `/dashboard` | `app/dashboard/page.tsx` | Main dashboard with topic selector, stats, briefing |
| `/dashboard/topics/new` | `app/dashboard/topics/new/page.tsx` | Create topic form |
| `/dashboard/topics/[id]` | `app/dashboard/topics/[id]/page.tsx` | Topic detail with 5 tabs (overview, articles, briefings, entities, sentiment) |
| `/dashboard/articles` | `app/dashboard/articles/page.tsx` | Article list with filters and pagination |
| `/dashboard/investment` | `app/dashboard/investment/page.tsx` | Investment module with 5 sub-tabs |
| `/dashboard/search` | `app/dashboard/search/page.tsx` | Semantic search interface |
| `/dashboard/settings` | `app/dashboard/settings/page.tsx` | User profile + admin service updates |

### Component Inventory

| Component | File | Description |
|-----------|------|-------------|
| `AuthGuard` | `components/AuthGuard.tsx` | Client-side auth check, redirects to /login or /dashboard |
| `Sidebar` | `components/Sidebar.tsx` | Fixed left nav: Dashboard, Articles, Investment, Search, topic list, WebSocket indicator, settings |
| `BubbleCluster` | `components/BubbleCluster.tsx` | D3 force-directed bubble chart of clusters (size = article count, color = cluster color) |
| `TrendChart` | `components/TrendChart.tsx` | Recharts horizontal bar chart of cluster trend scores |
| `SentimentTimeline` | `components/SentimentTimeline.tsx` | Recharts multi-line time series with per-cluster toggle and drag-to-zoom |
| `ClusterDetail` | `components/ClusterDetail.tsx` | Slide-in panel with cluster articles, sortable by recency/relevance/sentiment |
| `BriefingView` | `components/BriefingView.tsx` | Accordion list of briefings with sections: summary, highlights, watch items, new entities, coverage gaps |
| `EntityNetwork` | `components/EntityNetwork.tsx` | D3 force-directed graph of entities (nodes = entities, links = co-occurrence, filterable by type) |
| `AnalysisCard` | `components/AnalysisCard.tsx` | Investment analysis card: recommendation badge, confidence meter, key signals, risk factors |
| `SymbolDetail` | `components/SymbolDetail.tsx` | Slide-in panel: price card, OHLCV chart (30/90/180/365d), metrics grid, latest analysis, related articles |
| `PriceAlerts` | `components/PriceAlerts.tsx` | CRUD for price alerts with WebSocket-delivered toast notifications |
| `CorrelationSignals` | `components/CorrelationSignals.tsx` | Timeline view of correlation signals with expandable detail panels |
| `AssetMappings` | `components/AssetMappings.tsx` | Table of entity-to-ticker mappings with verify/reject actions |

### State Management

**Zustand Store** (`services/frontend/src/lib/store.ts`):

```typescript
interface AppState {
  user: UserResponse | null;
  topics: TopicResponse[];
  selectedTopicId: string | null;
  clusters: ClusterResponse[];
  latestBriefing: BriefingResponse | null;
  pendingUpdates: number;
  // Actions
  setUser, setTopics, selectTopic, setClusters, setLatestBriefing, incrementPendingUpdates, resetPendingUpdates
}
```

### API Client

**Source**: `services/frontend/src/lib/api-client.ts`

- SSR-safe base URL: uses `INTERNAL_API_URL` server-side, `NEXT_PUBLIC_API_URL` client-side
- JWT interceptor: automatically attaches Bearer token from localStorage
- Auto-refresh on 401: deduplicates concurrent refresh attempts
- Complete API functions for all endpoints

### WebSocket Hook

**Source**: `services/frontend/src/hooks/useWebSocket.ts`

- Sends JWT auth message on connect
- Auto-reconnect with exponential backoff (1s initial, 30s max)
- Responds to server `ping` with `pong`
- Passes events to parent via `onMessage` callback

### Force Simulation Utilities

**Source**: `services/frontend/src/lib/force-simulation.ts`

Two D3 simulations are defined:

1. **BubbleSimulation** -- For cluster bubble charts
   - `computeRadius`: scaleSqrt with range [20, 80] based on article count
   - Forces: center (0.05 strength), charge (-30), collision (padding 3, strength 0.8), x/y centering
   - Velocity decay: 0.3

2. **NetworkSimulation** -- For entity network graphs
   - Forces: link (distance 100, strength 0.3), center, charge (-150), collision (padding 5, strength 0.9)

---

## 12. Investment Module

### Overview

The investment module bridges news intelligence with financial markets by:
1. Automatically resolving named entities to ticker symbols
2. Maintaining user watchlists
3. Fetching market data from yfinance (equities) and CoinGecko (crypto)
4. Generating AI-powered investment analyses
5. Detecting correlation signals between news sentiment and price movements
6. Delivering real-time price alerts via WebSocket

### Entity-to-Ticker Resolution

**Source**: `services/worker/worker/tasks/resolve_ticker.py`

Two-step resolution:

| Step | Method | Source | Confidence |
|------|--------|--------|------------|
| 1 | Reference lookup | `ticker_reference` table, ILIKE match | High (database match) |
| 2 | LLM inference | Qwen3-32B-AWQ prompt | >= 0.6 threshold required |

The resolution process:
1. When `extract_entities` discovers a new org/product/technology entity, it dispatches `resolve_entity_ticker`
2. Step 1: Search `ticker_reference` for the entity name using ILIKE
3. If no match found, Step 2: LLM inference with the entity name, asking for the most likely ticker symbol and confidence
4. If confidence >= 0.6, create an `asset_mapping` record
5. Users can verify or reject mappings via the UI

### Market Data Pipeline

**Source**: `services/worker/worker/tasks/maintenance.py` (`fetch_market_data`)

```
refresh_market_data (beat, every 30 min)
  |
  +-- Discover symbols from:
  |     - watchlist_items (all users)
  |     - asset_mappings (all users)
  |
  +-- For each unique symbol:
       |
       +-- Equities/ETFs: yfinance.Ticker(symbol).info
       |     Extract: price, change_pct, volume, market_cap,
       |              pe_ratio, eps, dividend_yield, beta,
       |              52w_high, 52w_low
       |     Also fetch 365d price history -> price_history table
       |
       +-- Crypto: CoinGecko /api/v3/coins/{id}
       |     Extract: current_price, price_change_24h,
       |              total_volume, market_cap
       |
       +-- Upsert to market_data_cache (ON CONFLICT UPDATE)
```

### Investment Analysis

**Source**: `services/worker/worker/tasks/investment_analysis.py`

```
schedule_investment_analyses (beat, daily 6 AM)
  |
  +-- For each user + topic with resolved asset_mappings:
       |
       generate_investment_analyses
         |
         +-- Gather: article summaries mentioning the entity
         +-- Gather: market_data_cache for the symbol
         +-- LLM generates:
              - analysis_text (detailed analysis)
              - recommendation (bullish/bearish/neutral)
              - confidence (0.0 to 1.0)
              - key_signals (list of strings)
              - risk_factors (list of strings)
         +-- Store in investment_analyses table
```

### Correlation Signal Detection

**Source**: `services/worker/worker/tasks/correlation_signals.py`

```
detect_correlation_signals (beat, every 4h)
  |
  +-- For each resolved asset mapping:
       |
       +-- Compute 48h sentiment trend (current avg vs. previous period)
       +-- Get price change over same period
       |
       +-- Detect signals:
            - Bullish divergence: positive sentiment + price declining
            - Bearish divergence: negative sentiment + price rising
            - Momentum confirmation: sentiment and price moving together
       |
       +-- Store in correlation_signals table
```

### Price Alert Pipeline

**Source**: `services/worker/worker/tasks/price_alerts.py`, `services/api/app/main.py`

```
check_price_alerts (beat, every 15 min)
  |
  +-- Load all active alerts
  +-- Get current prices from market_data_cache
  +-- Evaluate conditions:
  |     above: current_price > threshold
  |     below: current_price < threshold
  |     crosses_above: last_known < threshold AND current >= threshold
  |     crosses_below: last_known > threshold AND current <= threshold
  |
  +-- If triggered:
       +-- Mark alert as inactive (is_active = False)
       +-- Set triggered_at timestamp
       +-- Publish to Redis pub/sub channel: "ttwatch:alerts:triggered"
              payload: {user_id, alert_id, symbol, condition, threshold, current_price}

API Server (background coroutine, started on lifespan):
  ws_alert_listener:
  +-- Subscribe to Redis "ttwatch:alerts:triggered"
  +-- On message:
       +-- Parse user_id from payload
       +-- Find user's WebSocket connections
       +-- Send: {"type": "alert", "alert_type": "price_alert", ...}

Frontend (PriceAlerts component):
  +-- useWebSocket hook receives alert messages
  +-- Displays toast notification (auto-dismisses after 8s)
  +-- Reloads alert list to reflect trigger status
```

### Investment Frontend

The `/dashboard/investment` page has 5 sub-tabs:

| Tab | Component | Description |
|-----|-----------|-------------|
| Watchlist | `WatchlistTab` (inline) | Grid with symbol, type, price, change, market cap; add/remove items |
| Analyses | `AnalysesTab` (inline) | List of `AnalysisCard` components |
| Signals | `CorrelationSignals` | Timeline with expandable signal details |
| Alerts | `PriceAlerts` | CRUD form + live WebSocket toast notifications |
| Mappings | `AssetMappings` | Entity-to-ticker table with verify/reject |

Clicking any symbol opens the `SymbolDetail` slide-in panel with:
- Price card (current price, change %, stale indicator)
- Metrics grid (volume, market cap, P/E, EPS, beta, 52W range)
- OHLCV chart (Recharts ComposedChart with area + bar + line, 4 range options)
- Latest investment analysis
- Related articles

---

## 13. Data Flow Diagrams

### End-to-End Article Flow

```
SearXNG                Worker-IO              Worker-CPU
  |                      |                      |
  |<-- search query -----|                      |
  |-- results ---------->|                      |
                         |                      |
                   ingest_article               |
                         |                      |
              +----+-----+-----+----+           |
              |    |     |     |    |           |
              v    v     v     v    |           |
           embed summa- enti- senti-|           |
           _art  rize   ties  ment  |           |
              |    |     |     |    |           |
              v    v     v     v    |           |
           Qdrant  PG   PG    PG   |           |
                         |         |           |
                   resolve_ticker  |           |
                         |         |           |
                         v         |           |
                   asset_mappings  |           |
                                   |           |
                              (every 2h)       |
                                   |           |
                                   +---------->|
                                        recluster_topic
                                               |
                                               v
                                        UMAP + HDBSCAN
                                               |
                                        update_trends
                                               |
                                        (every 6h)
                                               |
                                        generate_briefing
```

### Real-Time Alert Flow

```
Worker (check_price_alerts, every 15m)
       |
       | Redis PUBLISH "ttwatch:alerts:triggered"
       v
Redis Pub/Sub -----> API (ws_alert_listener coroutine)
                            |
                     ConnectionManager.send(user_id)
                            |
                     WebSocket -----> Frontend (PriceAlerts toast)
```

### Authentication Token Flow

```
Client                    API                    PostgreSQL
  |                        |                        |
  | POST /auth/login       |                        |
  |----------------------->|                        |
  |                        | Verify Argon2id        |
  |                        |<----- user row --------|
  |                        |                        |
  |                        | Issue JWT (15m)        |
  |                        | Issue refresh (30d)    |
  |                        |---- store hash ------->|
  |<-- {access, refresh} --|                        |
  |                        |                        |
  | (15 min later...)      |                        |
  |                        |                        |
  | API call (expired JWT) |                        |
  |----------------------->|                        |
  |<-- 401 Unauthorized ---|                        |
  |                        |                        |
  | POST /auth/refresh     |                        |
  |----------------------->|                        |
  |                        |--- verify hash ------->|
  |                        |--- delete old token -->|
  |                        |--- store new token --->|
  |<-- {new_access, new_refresh}                    |
  |                        |                        |
  | Retry original request |                        |
  |--- Bearer new_token -->|                        |
  |                        |-- SET LOCAL user_id -->|
  |                        |-- query with RLS ----->|
  |<-- success ------------|                        |
```

---

## 14. Configuration Reference

### Environment Variables

**Source**: `.env.example`, `services/api/app/config.py`, `docker-compose.yml`

#### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL superuser password | `your_secure_password` |
| `APP_DB_PASSWORD` | API service database password | `your_app_password` |
| `WORKER_DB_PASSWORD` | Worker service database password | `your_worker_password` |
| `JWT_SECRET` | JWT signing key (256-bit hex) | `$(python3 -c "import secrets; print(secrets.token_hex(32))")` |

#### Optional -- Service URLs

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_URL` | `http://vllm:8000/v1` | vLLM inference endpoint |
| `EMBEDDER_URL` | `http://embedder:8001` | Embedding service endpoint |
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG search endpoint |
| `MINIO_URL` | `http://minio:9000` | MinIO S3 endpoint |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant vector DB |

#### Optional -- LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `local` | `local` or `cloud` |
| `LOCAL_MODEL_NAME` | `Qwen3-32B-AWQ` | Model directory name for vLLM |
| `EMBEDDING_MODEL_NAME` | `Qwen/Qwen3-Embedding-0.6B` | HuggingFace model ID |
| `EMBEDDING_DIMENSION` | `1024` | Vector dimension (1024 for Qwen3-Embed, 3072 for OpenAI) |
| `CLOUD_LLM_PROVIDER` | `openai` | Cloud LLM: openai, anthropic, openrouter |
| `CLOUD_LLM_API_KEY` | (empty) | API key for cloud LLM |
| `CLOUD_LLM_MODEL` | `gpt-4o-mini` | Cloud model name |
| `CLOUD_EMBEDDING_PROVIDER` | `openai` | Cloud embedding provider |
| `CLOUD_EMBEDDING_MODEL` | `text-embedding-3-large` | Cloud embedding model |

#### Optional -- Service Versions

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_VERSION` | `v0.16.0` | vLLM Docker image tag |
| `QDRANT_VERSION` | `v1.12.1` | Qdrant image tag |
| `SEARXNG_VERSION` | `latest` | SearXNG image tag |
| `POSTGRES_VERSION` | `16` | PostgreSQL image tag |
| `REDIS_VERSION` | `7-alpine` | Redis image tag |
| `MINIO_VERSION` | `RELEASE.2024-11-07T00-52-20Z` | MinIO image tag |

#### Optional -- Frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8080` | API URL for browser |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8080/ws` | WebSocket URL for browser |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated CORS origins |

### Redis Database Layout

| Database | Purpose | Source |
|----------|---------|--------|
| DB0 | Celery message broker | `docker-compose.yml:4` |
| DB1 | Celery result backend | `docker-compose.yml:5` |
| DB2 | URL deduplication (Redis SETs) | `docker-compose.yml:6` |
| DB3 | API cache (rate limiter, version checks) | `docker-compose.yml:7` |

### Redis Key Patterns

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `ttwatch:seen_urls:{user_id}:{topic_id}` | SET | None | URL dedup for article ingestion |
| `ttwatch:rate:{user_id}:{endpoint}` | Sorted SET | 60s | Sliding window rate limiter |
| `ttwatch:version_check` | String (JSON) | 24h | Cached service version check results |
| `ttwatch:alerts:triggered` | Pub/Sub channel | -- | Price alert trigger notifications |

### SearXNG Configuration

**Source**: `config/searxng/settings.yml`

- **Search Engines**: Google, Bing, DuckDuckGo, Google News, Bing News
- **Output Format**: JSON enabled
- **Timeout**: 10 seconds per query
- **Rate Limiter**: Disabled
- **Pool Connections**: 100

### Qdrant Configuration

**Source**: `services/api/app/services/init_services.py`

- **Collection**: `articles`
- **Distance Metric**: Cosine
- **Vector Dimension**: Configurable via `EMBEDDING_DIMENSION` (default 1024)
- **Payload Indexes**: `user_id` (keyword), `topic_id` (keyword)
- **Dimension Validation**: On startup, if existing collection has wrong dimension, it is recreated

### vLLM Configuration

**Source**: `docker-compose.gpu.yml:29-38`

| Parameter | Value |
|-----------|-------|
| `--model` | `/models/${LOCAL_MODEL_NAME:-Qwen3-32B-AWQ}` |
| `--quantization` | `awq` |
| `--gpu-memory-utilization` | `0.85` |
| `--max-model-len` | `32768` |
| `--max-num-seqs` | `8` |
| `--enable-prefix-caching` | enabled |
| `--reasoning-parser` | `deepseek_r1` |

---

## 15. Implementation Status

### Fully Implemented

| Feature | Status | Notes |
|---------|--------|-------|
| User registration and login | Complete | Argon2id, JWT, refresh rotation |
| API key authentication | Complete | `tw_live_` format, SHA-256 hashed |
| Row-Level Security | Complete | 15 tables, 2 policies per table |
| Topic CRUD | Complete | With max_topics enforcement |
| Article ingestion pipeline | Complete | 3-layer dedup, trafilatura extraction |
| Article embedding | Complete | Qdrant upsert + semantic dedup |
| Article summarization | Complete | LLM 2-sentence summaries |
| Entity extraction | Complete | LLM extracts up to 15 entities |
| Sentiment classification | Complete | LLM -1.0 to 1.0 scale |
| HDBSCAN clustering | Complete | UMAP + HDBSCAN with LLM labels |
| Intelligence briefings | Complete | Hierarchical summarization |
| Trend scoring | Complete | 24h/48h ratio-based velocity |
| Sentiment history | Complete | Daily aggregation, recluster-proof |
| Coverage gap detection | Complete | LLM identifies 3-5 gaps |
| Semantic search | Complete | Qdrant vector similarity |
| Entity-to-ticker resolution | Complete | Reference + LLM two-step |
| Watchlist management | Complete | CRUD with market data display |
| Market data integration | Complete | yfinance + CoinGecko |
| Investment analysis | Complete | LLM-generated per asset |
| Price alerts | Complete | 4 conditions, WebSocket delivery |
| Correlation signals | Complete | Sentiment-price divergence |
| Version tracking | Complete | GitHub/DockerHub/HuggingFace APIs |
| Backup and restore | Complete | pg_dump + Qdrant snapshots |
| Rate limiting | Complete | Redis sliding window (Lua) |
| Health checks | Complete | Basic + extended service connectivity |
| WebSocket real-time updates | Complete | Per-user connections, ping/pong |
| Frontend dashboard | Complete | Topics, clusters, articles, briefings |
| Frontend investment | Complete | 5 sub-tabs, SymbolDetail panel |
| Frontend semantic search | Complete | With relevance score bars |
| D3 bubble cluster viz | Complete | Force simulation, tooltips, click |
| D3 entity network viz | Complete | Force simulation, type filter, zoom |
| Sentiment timeline chart | Complete | Multi-line, toggleable, drag-to-zoom |
| Test suite | Complete | 5 test modules covering auth, topics, articles, search, investment |

### Placeholder / Future

| Feature | Status | Notes |
|---------|--------|-------|
| MCP Server | Placeholder | `services/api/app/mcp/__init__.py` is empty |
| Sources CRUD (RSS feeds) | API exists | No frontend page implemented for source management |
| Saved Queries | API exists | No frontend page implemented |

---

## 16. File & Directory Reference

### Root Directory

```
TTwatch/
  .env                          # Active environment variables (gitignored)
  .env.example                  # Template with placeholder values
  .gitignore                    # Git ignore rules
  .dockerignore                 # Docker build ignore rules
  Makefile                      # Build targets (16 targets)
  docker-compose.yml            # Main compose (11 services + volumes)
  docker-compose.gpu.yml        # GPU overlay (adds vllm + embedder)
  docker-compose.cloud.yml      # Cloud overlay (disables GPU services)
  docker-compose.dev.yml        # Dev overlay (hot reload)
  docker-compose.lan.yml        # LAN overlay (remote services)
  docker-compose.gpu-node.yml   # Standalone GPU node
  docker-compose.search-node.yml # Standalone SearXNG node
  config/
    alembic.ini                 # Alembic migration config
    searxng/
      settings.yml              # SearXNG engine configuration
  migrations/
    env.py                      # Alembic environment
    versions/
      001_create_users_and_auth.py
      002_create_intelligence_tables.py
      003_create_investment_tables.py
      004_add_rls_policies.py
      005_grants_app_role.py
      006_grants_worker_role.py
  scripts/
    init-db.sh                  # PostgreSQL role creation
    create-admin-user.py        # Interactive admin setup
    seed-topics.py              # Example topic seeder
    backup.sh                   # PG + Qdrant backup
    restore.sh                  # PG + Qdrant restore
    update.sh                   # Safe update procedure
    download-models.sh          # Model weight downloader
    benchmark-gpu.py            # GPU inference benchmark
  docs/
    api-reference.md            # API endpoint documentation
    deployment.md               # Deployment guide (4 modes)
    lan-deployment.md           # LAN deployment guide
  tests/
    conftest.py                 # SQLite test DB, fixtures, mocks
    test_auth.py                # Auth flow tests (12 tests)
    test_topics.py              # Topic CRUD + isolation tests (12 tests)
    test_search.py              # Semantic search tests (5 tests)
    test_ingestion.py           # Article listing + dedup tests (9 tests)
    test_investment.py          # Watchlist + alerts + analysis tests (14 tests)
```

### API Service (`services/api/`)

```
services/api/
  Dockerfile
  requirements.txt
  app/
    __init__.py
    main.py                     # FastAPI app, lifespan, WebSocket, ConnectionManager
    config.py                   # Pydantic Settings (all env vars)
    deps.py                     # DB engine, sessions, Redis, auth dependencies
    celery_client.py            # Lightweight Celery client for task dispatch
    auth/
      router.py                 # Register, login, refresh, logout
      schemas.py                # Auth Pydantic schemas
    models/
      __init__.py
      user.py                   # User, ApiKey, RefreshToken SQLAlchemy models
      intelligence.py           # Topic, Source, Cluster, Article, Entity, etc.
      investment.py             # TickerReference, MarketData, AssetMapping, etc.
    routers/
      health.py                 # /health, /health/services
      topics.py                 # Topic CRUD
      articles.py               # Article listing + detail
      clusters.py               # Cluster detail + articles
      briefings.py              # Briefing listing + generation trigger
      entities.py               # Entity listing
      sentiment.py              # Sentiment snapshot + history
      sources.py                # Source CRUD
      queries.py                # Saved query CRUD
      search.py                 # Semantic search (Qdrant)
      investment.py             # Watchlist, analyses, correlation signals
      market_data.py            # Market data + price history
      users.py                  # Profile + API key management
      admin.py                  # Version status (admin-only)
    schemas/
      intelligence.py           # Intelligence Pydantic schemas
      investment.py             # Investment Pydantic schemas
    services/
      init_services.py          # Qdrant collection + MinIO bucket init
      embedder.py               # Local + Cloud embedding providers
      llm_local.py              # vLLM client (OpenAI-compatible)
      llm_cloud.py              # Cloud LLM (OpenAI, Anthropic, OpenRouter)
      llm_utils.py              # JSON response parsing utility
      http_utils.py             # Retry config (tenacity)
      version_checker.py        # Service version tracking (8 services)
    middleware/
      rate_limit.py             # Redis sliding window rate limiter
    mcp/
      __init__.py               # Empty placeholder
```

### Worker Service (`services/worker/`)

```
services/worker/
  Dockerfile
  requirements.txt
  worker/
    __init__.py
    celeryconfig.py             # Broker, backend, task routing, beat schedule
    db.py                       # Synchronous SQLAlchemy engine (psycogreen)
    rls.py                      # with_rls_context decorator
    llm_sync.py                 # Synchronous LLM + embedding clients
    tasks/
      __init__.py
      search.py                 # SearXNG search + article dispatch
      ingest.py                 # Article ingestion (3-layer dedup)
      embed.py                  # Qdrant embedding + semantic dedup
      summarize.py              # LLM article summarization
      entities.py               # LLM entity extraction
      sentiment.py              # LLM sentiment classification
      cluster.py                # UMAP + HDBSCAN clustering
      briefing.py               # Hierarchical briefing generation
      trends.py                 # Trend scoring + velocity
      sentiment_agg.py          # Daily sentiment aggregation
      coverage_gaps.py          # LLM coverage gap detection
      resolve_ticker.py         # Entity-to-ticker resolution
      investment_analysis.py    # LLM investment analysis
      price_alerts.py           # Price alert evaluation + Redis pub
      correlation_signals.py    # Sentiment-price divergence
      maintenance.py            # Cleanup tasks + market data fetch
      periodic.py               # Beat dispatch (fan-out per user/topic)
      version_check.py          # Service version check
      utils.py                  # MinIO text reader
```

### Frontend Service (`services/frontend/`)

```
services/frontend/
  Dockerfile
  package.json
  next.config.js                # output: "standalone"
  tailwind.config.ts            # Custom dark theme colors, fonts
  tsconfig.json
  postcss.config.js
  src/
    app/
      layout.tsx                # Root layout (Inter font, globals.css)
      page.tsx                  # Redirect to /dashboard
      globals.css               # Tailwind imports, .card, .btn-primary, animations
      login/
        page.tsx                # Login form
      register/
        page.tsx                # Registration with password rules
      dashboard/
        layout.tsx              # AuthGuard + Sidebar + WebSocket
        page.tsx                # Main dashboard (stats, briefing, clusters)
        topics/
          new/
            page.tsx            # Create topic form
          [id]/
            page.tsx            # Topic detail (5 tabs)
        articles/
          page.tsx              # Article list with filters
        investment/
          page.tsx              # Investment module (5 sub-tabs)
        search/
          page.tsx              # Semantic search
        settings/
          page.tsx              # User profile + admin version check
    components/
      AuthGuard.tsx
      Sidebar.tsx
      BubbleCluster.tsx
      TrendChart.tsx
      SentimentTimeline.tsx
      ClusterDetail.tsx
      BriefingView.tsx
      EntityNetwork.tsx
      AnalysisCard.tsx
      SymbolDetail.tsx
      PriceAlerts.tsx
      CorrelationSignals.tsx
      AssetMappings.tsx
    hooks/
      useWebSocket.ts           # WebSocket with auto-reconnect
    lib/
      types.ts                  # TypeScript interfaces (mirrors Pydantic schemas)
      api-client.ts             # Axios client + all API functions
      store.ts                  # Zustand global state
      auth-storage.ts           # localStorage JWT management
      design-tokens.ts          # Theme colors, cluster colors, sentiment colors
      force-simulation.ts       # D3 bubble + network simulation configs
```

### Embedder Service (`services/embedder/`)

```
services/embedder/
  Dockerfile                    # nvidia/cuda:12.4.1-runtime-ubuntu22.04
  requirements.txt
  server.py                     # FastAPI: /embed (batch), /health
                                # Loads Qwen3-Embedding-0.6B via sentence-transformers
                                # batch_size=64, normalize=True, max 256 texts
                                # ~1.2 GB VRAM
```

---

## 17. Development Guide

### Prerequisites

- Docker Engine 24+ and Docker Compose V2
- Git
- 8 GB RAM minimum (16 GB for GPU mode)
- NVIDIA GPU + nvidia-container-toolkit (GPU modes only)

### Quick Start (Development Mode)

```bash
git clone <repo-url> && cd TTwatch
cp .env.example .env

# Edit .env: set POSTGRES_PASSWORD, APP_DB_PASSWORD, WORKER_DB_PASSWORD, JWT_SECRET
# For cloud mode: set LLM_PROVIDER=cloud, CLOUD_LLM_API_KEY=sk-...

make dev
# Services: postgres, qdrant, redis, minio, searxng, api (hot-reload),
#           worker (hot-reload), scheduler, frontend (hot-reload)

# Run migrations
make migrate

# Create admin user
make create-admin

# Optional: seed example topics
make seed-topics

# Verify health
make health
```

### Running Tests

```bash
# Tests use SQLite + aiosqlite with mocked external services
docker compose exec api pytest tests/ -v
```

**Test Architecture** (`tests/conftest.py`):
- Uses SQLite with `aiosqlite` as an in-memory database
- Mocks: LLM provider, embedding provider, rate limiter, Celery task dispatch, init_services
- Fixtures: `test_user`, `test_topic`, `test_article`, `auth_headers`, `db_session`

**Test Coverage**:

| Module | Tests | Covers |
|--------|-------|--------|
| `test_auth.py` | 12 | Register, login, refresh, logout, password validation, JWT security |
| `test_topics.py` | 12 | CRUD, max_topics enforcement, user isolation, cluster listing |
| `test_search.py` | 5 | Auth requirement, empty results, result matching, user_id filter, limit |
| `test_ingestion.py` | 9 | Article listing, pagination, duplicate filtering, detail, isolation, URL uniqueness |
| `test_investment.py` | 14 | Watchlist CRUD, duplicate prevention, price alerts (create, invalid, list, delete), isolation, analyses, correlation signals |

### Makefile Targets

```
make dev          # Development with hot reload (cloud LLM)
make dev-gpu      # Development with hot reload + local GPU
make prod         # Production (base compose only)
make gpu          # Production with GPU colocated
make lan          # Production with LAN-distributed GPU
make cloud        # Production with cloud LLM
make gpu-node     # Standalone GPU node
make search-node  # Standalone SearXNG node
make stop         # Stop all services
make logs         # Tail all service logs
make migrate      # Run Alembic migrations
make backup       # Backup PostgreSQL + Qdrant
make restore      # Restore from backup
make shell-api    # bash into API container
make shell-db     # psql into PostgreSQL
make health       # Check service health
make create-admin # Create admin user
make seed-topics  # Seed example topics
```

### Key Development Patterns

**LLM Provider Abstraction**:
The system supports both local and cloud LLM providers through a factory pattern:
- `services/api/app/services/llm_local.py` -- vLLM (OpenAI-compatible API)
- `services/api/app/services/llm_cloud.py` -- OpenAI, Anthropic (with prefill trick for JSON), OpenRouter
- `services/worker/worker/llm_sync.py` -- Synchronous wrapper for worker tasks

**RLS-Aware Task Pattern**:
Every worker task that accesses user-scoped data uses the `@with_rls_context` decorator:
```python
@app.task(bind=True)
@with_rls_context
def my_task(self, user_id: str, session, ...):
    # session already has SET LOCAL ttwatch.current_user_id
    results = session.execute(select(MyTable))  # Automatically filtered by RLS
```

**Fan-Out Pattern**:
Periodic beat tasks enumerate users/topics and dispatch individual work tasks:
```python
# periodic.py
@app.task
def schedule_searches():
    with db_session() as session:
        topics = session.execute(select(Topic).where(Topic.next_refresh_at <= now))
        for topic in topics:
            run_topic_search.delay(str(topic.user_id), str(topic.id))
```

**Gevent Compatibility**:
The worker-io pool uses gevent, requiring `psycogreen` to patch psycopg2:
```python
# db.py
from psycogreen.gevent import patch_psycopg
patch_psycopg()
```

---

## 18. Appendix: Key Design Decisions

### 1. PostgreSQL RLS over Application-Level Filtering

**Decision**: Row-Level Security at the database layer rather than `WHERE user_id = ?` in every query.

**Rationale**: RLS provides defense-in-depth -- even if application code omits a user_id filter (a common bug in multi-tenant systems), the database itself prevents cross-tenant data access. The `worker_bypass` policy allows workers to operate across users for periodic tasks.

**Trade-off**: Slightly more complex migration setup. Cannot use a single connection pool for multiple tenants -- must SET LOCAL per transaction.

**Source**: `migrations/versions/004_add_rls_policies.py`

### 2. Dual Worker Pools (gevent + prefork)

**Decision**: Separate I/O-bound (gevent, concurrency=32) and CPU-bound (prefork, concurrency=2) workers with task routing.

**Rationale**: Article ingestion involves many network calls (fetch URLs, call LLM API, write to MinIO) that benefit from cooperative multitasking. Clustering (UMAP, HDBSCAN) and briefing generation are CPU-intensive and benefit from true multiprocessing. Mixing these in one pool would either starve I/O tasks or waste resources on CPU tasks.

**Source**: `docker-compose.yml:116-170`, `services/worker/worker/celeryconfig.py:14-28`

### 3. Three-Layer Deduplication

**Decision**: URL check (Redis) -> Content hash (SHA-256 in PG) -> Semantic similarity (Qdrant cosine > 0.92).

**Rationale**: Each layer catches different types of duplicates. URL dedup is fastest (O(1) Redis). Content hash catches exact reposts at different URLs. Semantic dedup catches paraphrased or syndicated content with different wording but same meaning. The 0.92 threshold was chosen to be strict enough to avoid false positives while catching near-identical content.

**Source**: `services/worker/worker/tasks/ingest.py`, `services/worker/worker/tasks/embed.py`

### 4. Recluster-Proof Sentiment History

**Decision**: Store `cluster_keyword` alongside `cluster_id` in `sentiment_history`, and use keyword-based lookups for the timeline.

**Rationale**: HDBSCAN reclustering destroys and recreates cluster IDs every 2 hours. If sentiment history only referenced `cluster_id`, all historical data would become orphaned. By storing the keyword (a human-readable label), the timeline persists across recluster cycles. Before deleting old clusters, the code sets `cluster_id = NULL` on `sentiment_history` rows.

**Source**: `services/worker/worker/tasks/cluster.py` (cluster deletion section), `migrations/versions/002_create_intelligence_tables.py` (sentiment_history schema)

### 5. Local-First LLM with Cloud Fallback

**Decision**: Default to local vLLM inference with seamless cloud fallback.

**Rationale**: Local inference provides data privacy (no content sent to external APIs), lower latency for batch processing, and zero marginal cost after initial GPU investment. Cloud fallback enables GPU-less development and deployment for users without suitable hardware. The provider abstraction makes switching transparent.

**Source**: `services/api/app/config.py:24-46`, `services/worker/worker/llm_sync.py`

### 6. SearXNG over Direct API Calls

**Decision**: Use SearXNG meta-search engine instead of directly calling Google/Bing APIs.

**Rationale**: SearXNG aggregates results from multiple search engines without requiring API keys. It can be self-hosted for privacy. The `format=json` output provides structured results. It also serves as a privacy layer -- search queries are not associated with any account.

**Source**: `services/worker/worker/tasks/search.py`, `config/searxng/settings.yml`

### 7. JWT + API Key Dual Authentication

**Decision**: JWT for frontend sessions, API keys for programmatic access.

**Rationale**: JWTs provide stateless session management for the browser with automatic refresh. API keys provide long-lived access for scripts and agents without the complexity of OAuth. The prefix-based format (`tw_live_`) enables quick visual identification and the key is never stored in plaintext.

**Source**: `services/api/app/deps.py:55-110`, `services/api/app/routers/users.py`

### 8. MinIO for Raw Article Storage

**Decision**: Store raw article text in MinIO (S3-compatible) rather than PostgreSQL.

**Rationale**: Raw article text can be large (10-50KB per article). Storing it in PostgreSQL would bloat the database, slow backups, and increase WAL size. MinIO provides efficient object storage that can be backed up independently. The `raw_storage_key` in the articles table provides the reference.

**Source**: `services/worker/worker/tasks/ingest.py` (MinIO storage), `services/worker/worker/tasks/utils.py` (MinIO retrieval)

### 9. Redis Pub/Sub for Real-Time Alerts

**Decision**: Use Redis pub/sub channel for price alert delivery to WebSocket connections.

**Rationale**: The worker process that checks price alerts runs in a separate container from the API server that manages WebSocket connections. Redis pub/sub provides a lightweight bridge between them without adding another message queue. The API server runs a background coroutine that subscribes to the channel and routes messages to the correct user's WebSocket.

**Source**: `services/worker/worker/tasks/price_alerts.py`, `services/api/app/main.py:75-110`

### 10. Standalone Embedder Service

**Decision**: Custom FastAPI service wrapping sentence-transformers rather than using vLLM for embeddings.

**Rationale**: Embedding and LLM inference have very different resource profiles. The embedding model (Qwen3-Embedding-0.6B, ~1.2 GB VRAM) is much smaller than the LLM (Qwen3-32B-AWQ, ~20+ GB VRAM). Running them as separate services allows independent scaling, separate health checks, and ensures the embedding service starts before vLLM (which has a long model-loading time). The embedder must be healthy before vLLM starts (`depends_on` in compose).

**Source**: `services/embedder/server.py`, `docker-compose.gpu.yml:4-18`

---

*This document was generated from a comprehensive analysis of the TTwatch codebase at commit `7455806` (main branch). All file paths, line numbers, and technical details are based on the actual source code.*
