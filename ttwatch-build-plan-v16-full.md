# TTwatch — Definitive Build Plan & Infrastructure Architecture

> **Self-contained plan.** This document is the single source of truth for TTwatch. It contains the complete multi-tenant architecture, all infrastructure code, database schemas, visualization specifications, frontend design system, and build phases. Every code sample, design decision, and component specification is defined inline. No external documents are needed to implement this system.

> **v16 Changelog.** This revision fixes 5 additional issues identified during deep review of v15, including 1 critical bug (login handler's refresh token cap counts ALL tokens including expired ones — a user with 9 expired + 1 active token = 10 total, prematurely triggering the cap and potentially deleting the only active token instead of just limiting concurrent sessions), 1 runtime bug (`ws_alert_listener` contains dead code referencing undefined `os` module — the `hasattr(settings, 'REDIS_CACHE_URL')` check always returns True since the field is defined on Settings, making the `else os.environ.get(...)` fallback unreachable, but the code is misleading and will crash if the Settings field is ever removed), and 3 design gaps (`sentiment_history` lacks a `topic_id` column making topic-level historical queries impossible after recluster nullifies `cluster_id` — the `cluster_keyword` added in v15 preserves cluster identity but not topic association, v14/v15 technical decisions documented in appendices but not actually placed in §18 as claimed by the changelog closing lines, and structural ordering issue where the v14 changelog appears after the v15 changelog breaking chronological order). A summary of v16 changes is in [Appendix O — v16 Change Log](#appendix-o--v16-change-log). Previous changelogs remain in [Appendix A](#appendix-a--v2-change-log), [Appendix B](#appendix-b--v3-change-log), [Appendix C](#appendix-c--v4-change-log), [Appendix D](#appendix-d--v5-change-log), [Appendix E](#appendix-e--v6-change-log), [Appendix F](#appendix-f--v7-change-log), [Appendix G](#appendix-g--v8-change-log), [Appendix H](#appendix-h--v9-change-log), [Appendix I](#appendix-i--v10-change-log), [Appendix J](#appendix-j--v11-change-log), [Appendix K](#appendix-k--v12-change-log), [Appendix L](#appendix-l--v13-change-log), [Appendix M](#appendix-m--v14-change-log), [Appendix N](#appendix-n--v15-change-log).

---

## 1. Multi-Tenancy Model

TTwatch is a **per-user isolated platform**. Every user has their own topics, searches, articles, clusters, briefings, investment analyses, and agent connections. User 1's OpenClaw agent can never see User 2's data.

### Isolation Boundaries

```
┌──────────────────────────────────────────────────────────┐
│                      TTwatch Server                       │
│                                                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │
│  │   User 1    │  │   User 2    │  │   User 3    │      │
│  │             │  │             │  │             │      │
│  │ Topics      │  │ Topics      │  │ Topics      │      │
│  │ Sources     │  │ Sources     │  │ Sources     │      │
│  │ Clusters    │  │ Clusters    │  │ Clusters    │      │
│  │ Articles    │  │ Articles    │  │ Articles    │      │
│  │ Briefings   │  │ Briefings   │  │ Briefings   │      │
│  │ Watchlist   │  │ Watchlist   │  │ Watchlist   │      │
│  │ Analyses    │  │ Analyses    │  │ Analyses    │      │
│  │ Agent Key   │  │ Agent Key   │  │ Agent Key   │      │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘      │
│         │                │                │              │
│         ▼                ▼                ▼              │
│  ┌──────────────────────────────────────────────┐       │
│  │          Shared Infrastructure               │       │
│  │  PostgreSQL · Qdrant · Redis · vLLM · BGE-M3 │       │
│  └──────────────────────────────────────────────┘       │
│                                                          │
│  ┌──────────────────────────────────────────────┐       │
│  │      Shared Reference Data (read-only)       │       │
│  │  ticker_reference · price_history ·           │       │
│  │  theme_etf_map · market_data_cache            │       │
│  └──────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘

OpenClaw Agent 1 ──► API Key A ──► User 1 data ONLY
OpenClaw Agent 2 ──► API Key B ──► User 2 data ONLY
```

**Isolation is enforced at the database query level, NOT via separate databases.** Every user-scoped table carries a `user_id` column and every query filters by it. PostgreSQL Row-Level Security (RLS) provides defense-in-depth.

---

## 2. System Architecture Overview

### Single-Machine Deployment

```
┌─────────────────────────────────────────────────────────────────────┐
│                     TTwatch Platform (Multi-Tenant)                  │
├─────────────┬──────────────┬──────────────┬────────────┬────────────┤
│  Frontend   │  API Gateway │  Processing  │  Storage   │  External  │
│  (Next.js)  │  (FastAPI)   │  Pipeline    │  Layer     │  Agents    │
│             │              │              │            │            │
│ Auth Pages  │ JWT Auth     │ Ingestion    │ PostgreSQL │ OpenClaw   │
│ Dashboard   │ REST API     │ Embedding    │ Qdrant     │ Per-user   │
│ Investment  │ WebSocket    │ Clustering   │ Redis      │ MCP Server │
│ User Prefs  │ MCP Server   │ LLM Tasks    │ MinIO      │ API Keys   │
│             │ Rate Limiter │ Market Data  │ User-part. │            │
└─────────────┴──────────────┴──────────────┴────────────┴────────────┘
                                    │
                         ┌──────────┴──────────┐
                         │   Local GPU Layer    │
                         │   RTX 5090 (32GB)    │
                         │                      │
                         │  vLLM: Qwen 2.5 32B  │
                         │  BGE-M3 Embeddings   │
                         └──────────────────────┘
```

### Distributed LAN Deployment

TTwatch supports splitting heavy services across multiple machines on the same LAN. This allows the GPU-intensive services (vLLM, embedder) and web search (SearXNG) to run on dedicated hardware while the core platform runs on the main server.

```
┌─────────────────────────────────────┐     ┌──────────────────────────────┐
│   MAIN SERVER (192.168.1.100)       │     │   GPU SERVER (192.168.1.200) │
│                                     │     │                              │
│  ┌─────────┐ ┌──────────┐          │     │  ┌────────────────────────┐  │
│  │ Frontend │ │ API      │          │ LAN │  │ vLLM (Qwen 2.5 32B)   │  │
│  │ :3000   │ │ :8080    │◄─────────┼─────┼──│ :8000                  │  │
│  └─────────┘ └──────────┘          │     │  └────────────────────────┘  │
│  ┌──────────┐ ┌──────────┐         │     │  ┌────────────────────────┐  │
│  │ Workers  │ │ Scheduler│         │     │  │ BGE-M3 Embedder        │  │
│  │ (Celery) │ │ (Beat)   │         │     │  │ :8001                  │  │
│  └──────────┘ └──────────┘         │     │  └────────────────────────┘  │
│  ┌──────────┐ ┌──────────┐         │     └──────────────────────────────┘
│  │PostgreSQL│ │ Qdrant   │         │
│  │ :5432   │ │ :6333    │         │     ┌──────────────────────────────┐
│  └──────────┘ └──────────┘         │     │  SEARCH SERVER (192.168.1.201)│
│  ┌──────────┐ ┌──────────┐         │     │  (optional, can be any PC)  │
│  │ Redis    │ │ MinIO    │         │ LAN │  ┌────────────────────────┐  │
│  │ :6379   │ │ :9000    │         │─────┼──│ SearXNG                │  │
│  └──────────┘ └──────────┘         │     │  │ :8080                  │  │
└─────────────────────────────────────┘     │  └────────────────────────┘  │
                                            └──────────────────────────────┘
```

**The architecture is controlled entirely by environment variables.** All service URLs (vLLM, embedder, SearXNG) are configurable, so switching between single-machine and distributed deployment requires only changing `.env` values — no code changes.

---

## 3. Local LLM Infrastructure & Context Window Strategy

### Hardware: RTX 5090 (32GB VRAM)

### VRAM Budget

| Component | Quantization | VRAM (est.) | Notes |
|-----------|-------------|-------------|-------|
| Qwen 2.5 32B | AWQ 4-bit | ~18–20 GB | Primary reasoning/summarization model |
| BGE-M3 (568M params) | FP16 | ~1.2 GB | Embedding model, always loaded |
| KV Cache (Qwen) | — | ~4–8 GB | Depends on context length & batch |
| OS / CUDA overhead | — | ~1.5 GB | |
| **Total** | | **~25–31 GB** | Fits in 32GB with headroom |

### Context Window Planning

**Tier 1 — Short-context tasks (≤4K tokens): ~70% of all calls**
- Article summarization, entity extraction, sentiment classification, query intent parsing

**Tier 2 — Medium-context tasks (4K–16K tokens): ~25% of calls**
- Cluster summarization, coverage gap detection, search plan generation, investment analysis

**Tier 3 — Long-context tasks (16K–32K tokens): ~5% of calls**
- Full topic briefings, cross-cluster relationship analysis, weekly intelligence reports

**Key design principle: NEVER send raw articles to the LLM for briefings.** Hierarchical summarization:

```
Raw Article (2K–10K tokens)
    → Article Summary (100–200 tokens)        [Tier 1]
        → Cluster Summary (300–500 tokens)     [Tier 2, from ~20 article summaries]
            → Topic Briefing (500–1000 tokens)  [Tier 3, from ~8 cluster summaries]
```

### vLLM Configuration

```yaml
# vllm serve config
model: Qwen/Qwen2.5-32B-Instruct-AWQ
quantization: awq
gpu_memory_utilization: 0.85
max_model_len: 32768
max_num_seqs: 8
enable_prefix_caching: true
dtype: auto
tensor_parallel_size: 1
```

### BGE-M3 Deployment

Run as a separate process using `sentence-transformers`, NOT through vLLM. Always-resident in VRAM (~1.2GB).

```python
"""BGE-M3 embedding service.

Runs as a standalone FastAPI server on port 8001.
Always resident in GPU VRAM (~1.2GB for FP16).
Supports batch embedding up to 256 texts per call.
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    from sentence_transformers import SentenceTransformer

    model_name = os.environ.get("MODEL_NAME", "BAAI/bge-m3")
    logger.info(f"Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name, device="cuda")
    logger.info(f"Model loaded. Embedding dimension: {model.get_sentence_embedding_dimension()}")
    yield
    logger.info("Shutting down embedder")


app = FastAPI(title="TTwatch Embedder", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dimension: int


@app.get("/health")
async def health():
    if model is None:
        raise HTTPException(503, "Model not loaded")
    return {"status": "ok", "model": os.environ.get("MODEL_NAME", "BAAI/bge-m3")}


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest):
    if model is None:
        raise HTTPException(503, "Model not loaded")
    if not request.texts:
        return EmbedResponse(embeddings=[], dimension=model.get_sentence_embedding_dimension())
    if len(request.texts) > 256:
        raise HTTPException(400, "Maximum 256 texts per batch")

    embeddings = model.encode(
        request.texts,
        batch_size=64,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return EmbedResponse(
        embeddings=embeddings.tolist(),
        dimension=embeddings.shape[1],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
```

**`services/embedder/Dockerfile`:**
```dockerfile
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8001
CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
```

**`services/embedder/requirements.txt`:**
```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
sentence-transformers>=3.3.0
torch>=2.4.0
pydantic>=2.0
```

### GPU Startup Ordering

**Critical:** Both vLLM and BGE-M3 share the same physical GPU. vLLM's `gpu-memory-utilization: 0.85` means it will attempt to allocate ~27.2GB. BGE-M3 needs ~1.2GB. Total ~28.4GB fits in 32GB, but startup order matters — if both attempt simultaneous allocation, an OOM race can occur.

**Solution (colocated GPU):** The `embedder` service must start and finish loading before vLLM begins loading. In `docker-compose.gpu.yml`, vLLM depends on the embedder's healthcheck (see Section 5). This ensures BGE-M3 claims its 1.2GB first, and vLLM's 0.85 fraction of the remaining ~30.8GB (~26.2GB) stays within budget.

**Solution (distributed LAN):** When vLLM and the embedder run on a remote GPU machine, Docker Compose on the main server has no `depends_on` relationship. Instead, the main server's services configure health-aware retry logic — they wait for the remote vLLM/embedder to report healthy before starting work (see Section 4, HTTP Retry Strategy). The GPU machine runs its own compose file (`docker-compose.gpu-node.yml`) that enforces the embedder→vLLM startup order locally.

### Multi-User GPU Impact

vLLM batches automatically across users. No data leaks between batched requests. Fairness is handled at the queue level (see Section 7).

---

## 4. LLM Provider Abstraction

### Application Settings

```python
# services/api/app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    """Application settings loaded from environment variables.
    
    Pydantic-settings automatically reads env vars matching field names
    (case-insensitive). All service URLs are configurable for LAN distribution.
    """
    # Database
    DATABASE_URL: str = "postgresql://ttwatch_app:changeme@postgres:5432/ttwatch"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    REDIS_DEDUP_URL: str = "redis://redis:6379/2"
    REDIS_CACHE_URL: str = "redis://redis:6379/3"

    # Qdrant
    QDRANT_URL: str = "http://qdrant:6333"

    # LLM — can point to local Docker service OR remote LAN machine
    LLM_PROVIDER: str = "local"  # "local" or "cloud"
    VLLM_URL: str = "http://vllm:8000/v1"
    LOCAL_MODEL_NAME: str = "Qwen2.5-32B-Instruct-AWQ"

    # Embedder — can point to local Docker service OR remote LAN machine
    EMBEDDER_URL: str = "http://embedder:8001"
    EMBEDDING_DIMENSION: int = 1024  # BGE-M3 = 1024, OpenAI large = 3072

    # SearXNG — can point to local Docker service OR remote LAN machine
    SEARXNG_URL: str = "http://searxng:8080"

    # MinIO — can point to local Docker service OR remote LAN machine
    MINIO_URL: str = "http://minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ttwatch-content"

    # Cloud LLM (fallback or primary for GPU-less)
    CLOUD_LLM_PROVIDER: str = "openai"
    CLOUD_LLM_API_KEY: str = ""
    CLOUD_LLM_MODEL: str = "gpt-4o-mini"
    CLOUD_EMBEDDING_PROVIDER: str = "openai"
    CLOUD_EMBEDDING_MODEL: str = "text-embedding-3-large"

    # Auth
    JWT_SECRET: str = "change-me"
    CORS_ORIGINS: str = "http://localhost:3000"  # comma-separated for multiple

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

### Provider Interface

```python
# services/api/app/services/llm.py
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    """Async LLM provider — used by FastAPI API handlers."""
    @abstractmethod
    async def generate(self, messages: list[dict], **kwargs) -> str: ...

    @abstractmethod
    async def generate_json(self, messages: list[dict], **kwargs) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...
```

### Synchronous LLM Provider for Workers

```python
# services/worker/worker/llm_sync.py
"""Synchronous LLM client for Celery worker tasks.

Celery tasks MUST be synchronous (def, not async def).
This module provides httpx.Client-based sync wrappers.
"""
import os
import copy
import httpx
import json
import re
import tenacity

def parse_json_response(raw: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences and preamble.
    
    Handles: bare JSON, ```json fences, text before/after JSON block.
    SHARED: Imported from app.services.llm_utils in production.
    This inline copy exists only as documentation — the worker Dockerfile
    copies services/api/app to /app/app, so `from app.services.llm_utils
    import parse_json_response` works at runtime. Both files use this
    identical implementation.
    """
    text = raw.strip()

    # Strategy 1: Strip markdown fences (```json ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Strategy 2: Find first { ... last } in the string
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    return json.loads(text)

# Prefer the shared implementation if available (worker Dockerfile
# copies api/app to /app/app). Falls back to inline copy above.
try:
    from app.services.llm_utils import parse_json_response  # noqa: F811
except ImportError:
    pass  # Use inline implementation above


# Extended retry for LAN service startup
_lan_startup_retry = dict(
    stop=tenacity.stop_after_attempt(30),
    wait=tenacity.wait_exponential(multiplier=2, min=5, max=60),
    retry=tenacity.retry_if_exception(
        lambda e: isinstance(e, (httpx.ConnectError, httpx.TimeoutException, ConnectionError))
    ),
    before_sleep=lambda rs: None,
)


class SyncLLMClient:
    """Synchronous LLM client that talks to vLLM or cloud providers."""

    def __init__(self):
        self.provider = os.environ.get("LLM_PROVIDER", "local")
        self.vllm_url = os.environ.get("VLLM_URL", "http://vllm:8000/v1")
        self.model = os.environ.get("LOCAL_MODEL_NAME", "Qwen2.5-32B-Instruct-AWQ")
        self._verified = False

        if self.provider == "cloud":
            cloud_provider = os.environ.get("CLOUD_LLM_PROVIDER", "openai")
            api_key = os.environ.get("CLOUD_LLM_API_KEY", "")
            self.model = os.environ.get("CLOUD_LLM_MODEL", "gpt-4o-mini")

            if cloud_provider == "anthropic":
                base_url = "https://api.anthropic.com"
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2024-10-22",
                    "content-type": "application/json",
                }
            elif cloud_provider == "openrouter":
                base_url = "https://openrouter.ai/api"
                headers = {"Authorization": f"Bearer {api_key}"}
            else:
                base_url = "https://api.openai.com"
                headers = {"Authorization": f"Bearer {api_key}"}

            self._client = httpx.Client(
                base_url=base_url,
                headers=headers,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            self._is_anthropic = cloud_provider == "anthropic"
        else:
            self._client = httpx.Client(
                base_url=self.vllm_url,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            self._is_anthropic = False

    @tenacity.retry(**_lan_startup_retry)
    def _verify_connectivity(self):
        """Called on first generate() call. Retries with backoff for LAN startup."""
        if self.provider == "local":
            resp = self._client.get("/models")
            resp.raise_for_status()
        self._verified = True

    def generate(self, messages: list[dict], **kwargs) -> str:
        if not self._verified:
            self._verify_connectivity()

        if self._is_anthropic:
            system = ""
            filtered = []
            for m in messages:
                if m["role"] == "system":
                    system = m["content"]
                else:
                    filtered.append(m)
            body = {
                "model": self.model,
                "system": system,
                "messages": filtered,
                "max_tokens": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.3),
            }
            resp = self._client.post("/v1/messages", json=body)
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        else:
            body = {
                "model": self.model,
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.3),
            }
            if "response_format" in kwargs:
                body["response_format"] = kwargs["response_format"]
            resp = self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    def generate_json(self, messages: list[dict], **kwargs) -> dict:
        messages = copy.deepcopy(messages)
        kwargs.setdefault("temperature", 0.1)

        if self._is_anthropic:
            json_instruction = (
                "You must respond with ONLY a valid JSON object. "
                "No preamble, no explanation, no markdown fences. "
                "Start your response with '{' and end with '}'."
            )
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += f"\n\n{json_instruction}"
            else:
                messages.insert(0, {"role": "system", "content": json_instruction})
            messages.append({"role": "assistant", "content": "{"})
            raw = self.generate(messages, **kwargs)
            raw = "{" + raw
        elif self.provider == "cloud":
            kwargs["response_format"] = {"type": "json_object"}
            raw = self.generate(messages, **kwargs)
        else:
            raw = self.generate(messages, **kwargs)

        return parse_json_response(raw)

    def close(self):
        self._client.close()


class SyncEmbeddingClient:
    """Synchronous embedding client for Celery workers."""

    def __init__(self):
        provider = os.environ.get("LLM_PROVIDER", "local")
        embedder_url = os.environ.get("EMBEDDER_URL", "")

        if provider == "cloud" or not embedder_url:
            api_key = os.environ.get("CLOUD_LLM_API_KEY", "")
            self.model = os.environ.get(
                "CLOUD_EMBEDDING_MODEL", "text-embedding-3-large"
            )
            cloud_provider = os.environ.get("CLOUD_EMBEDDING_PROVIDER", "openai")
            if cloud_provider == "openai":
                base_url = "https://api.openai.com"
            else:
                base_url = "https://api.openai.com"  # Default to OpenAI-compatible
            self._client = httpx.Client(
                base_url=base_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            self._is_local = False
        else:
            self._client = httpx.Client(
                base_url=embedder_url,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            self._is_local = True
        self._verified = False

    @tenacity.retry(**_lan_startup_retry)
    def _verify_connectivity(self):
        """Called on first embed() call. Retries with backoff for LAN startup."""
        if self._is_local:
            resp = self._client.get("/health")
            resp.raise_for_status()
        self._verified = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._verified:
            self._verify_connectivity()
        if self._is_local:
            resp = self._client.post("/embed", json={"texts": texts})
            resp.raise_for_status()
            return resp.json()["embeddings"]
        else:
            resp = self._client.post("/v1/embeddings", json={
                "model": self.model,
                "input": texts,
            })
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])
            return [d["embedding"] for d in data]

    def close(self):
        self._client.close()
```

### Shared JSON Parser

```python
# services/api/app/services/llm_utils.py
import json, re

def parse_json_response(raw: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences and preamble.
    
    Handles: bare JSON, ```json fences, text before/after JSON block.
    CANONICAL IMPLEMENTATION: Worker imports this via `from app.services.llm_utils
    import parse_json_response` (worker Dockerfile copies api/app to /app/app).
    """
    text = raw.strip()

    # Strategy 1: Strip markdown fences (```json ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Strategy 2: Find first { ... last } in the string
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    return json.loads(text)
```

### Local vLLM Provider (async, for API)

```python
# services/api/app/services/llm_local.py
import httpx
from app.services.llm import LLMProvider
from app.services.llm_utils import parse_json_response
from app.config import settings

class LocalVLLMProvider(LLMProvider):
    def __init__(self):
        self.base_url = settings.VLLM_URL
        self.model = settings.LOCAL_MODEL_NAME
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def generate(self, messages, **kwargs):
        resp = await self._client.post("/chat/completions", json={
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.3),
        })
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def generate_json(self, messages, **kwargs):
        kwargs.setdefault("temperature", 0.1)
        raw = await self.generate(messages, **kwargs)
        return parse_json_response(raw)

    async def close(self):
        await self._client.aclose()
```

### Cloud LLM Provider (async, for API)

```python
# services/api/app/services/llm_cloud.py
import copy
import httpx
from app.services.llm import LLMProvider
from app.services.llm_utils import parse_json_response
from app.config import settings

class CloudLLMProvider(LLMProvider):
    def __init__(self):
        self.provider = settings.CLOUD_LLM_PROVIDER  # "openai", "openrouter", "anthropic"
        self.api_key = settings.CLOUD_LLM_API_KEY
        self.model = settings.CLOUD_LLM_MODEL

        if self.provider == "anthropic":
            base_url = "https://api.anthropic.com"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2024-10-22",
                "content-type": "application/json",
            }
        elif self.provider == "openrouter":
            base_url = "https://openrouter.ai/api"
            headers = {"Authorization": f"Bearer {self.api_key}"}
        else:
            base_url = "https://api.openai.com"
            headers = {"Authorization": f"Bearer {self.api_key}"}

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def generate(self, messages, **kwargs):
        if self.provider == "anthropic":
            system = ""
            filtered = []
            for m in messages:
                if m["role"] == "system":
                    system = m["content"]
                else:
                    filtered.append(m)
            body = {
                "model": self.model,
                "system": system,
                "messages": filtered,
                "max_tokens": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.3),
            }
            resp = await self._client.post("/v1/messages", json=body)
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        else:
            body = {
                "model": self.model,
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.3),
            }
            if "response_format" in kwargs:
                body["response_format"] = kwargs["response_format"]
            resp = await self._client.post("/v1/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def generate_json(self, messages, **kwargs):
        messages = copy.deepcopy(messages)

        if self.provider in ("openai", "openrouter"):
            kwargs["response_format"] = {"type": "json_object"}
        elif self.provider == "anthropic":
            json_instruction = (
                "You must respond with ONLY a valid JSON object. "
                "No preamble, no explanation, no markdown fences. "
                "Start your response with '{' and end with '}'."
            )
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += f"\n\n{json_instruction}"
            else:
                messages.insert(0, {"role": "system", "content": json_instruction})
            messages.append({"role": "assistant", "content": "{"})

        raw = await self.generate(messages, **kwargs)
        if self.provider == "anthropic":
            raw = "{" + raw
        return parse_json_response(raw)

    async def close(self):
        await self._client.aclose()
```

### Embedding Provider (async, for API)

```python
# services/api/app/services/embedder.py
import httpx
from app.config import settings

class LocalEmbeddingProvider:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=settings.EMBEDDER_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post("/embed", json={"texts": texts})
        resp.raise_for_status()
        return resp.json()["embeddings"]

    async def close(self):
        await self._client.aclose()


class CloudEmbeddingProvider:
    """Embedding provider for GPU-less deployments. Uses OpenAI-compatible API."""
    def __init__(self):
        self.provider = settings.CLOUD_EMBEDDING_PROVIDER
        self.model = settings.CLOUD_EMBEDDING_MODEL
        self.api_key = settings.CLOUD_LLM_API_KEY

        base_url = "https://api.openai.com"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post("/v1/embeddings", json={
            "model": self.model,
            "input": texts,
        })
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        return [d["embedding"] for d in data]

    async def close(self):
        await self._client.aclose()


def get_embedding_provider():
    """Return the configured embedding provider."""
    if settings.LLM_PROVIDER == "cloud" or not settings.EMBEDDER_URL:
        return CloudEmbeddingProvider()
    return LocalEmbeddingProvider()
```

### LLM Provider Factory

```python
# services/api/app/services/llm_factory.py
from app.config import settings
from app.services.llm_local import LocalVLLMProvider
from app.services.llm_cloud import CloudLLMProvider

def get_llm_provider():
    """Return the configured LLM provider based on environment settings."""
    if settings.LLM_PROVIDER == "cloud" or not settings.VLLM_URL:
        return CloudLLMProvider()
    return LocalVLLMProvider()
```

### Service Initialization (Qdrant + MinIO)

```python
# services/api/app/services/init_services.py
"""One-time initialization for external services (Qdrant, MinIO)."""
import logging
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance
from minio import Minio
from app.config import settings

logger = logging.getLogger(__name__)


def init_qdrant():
    """Create the articles collection if it doesn't exist.
    
    If the collection already exists, validates that the vector dimension
    matches EMBEDDING_DIMENSION. Mismatches (e.g., switching from local
    BGE-M3 1024-dim to cloud OpenAI 3072-dim) would cause silent search
    failures — vectors would be stored but similarity scores would be
    meaningless across different dimensions.
    """
    client = QdrantClient(url=settings.QDRANT_URL, timeout=30)
    collections = [c.name for c in client.get_collections().collections]

    if "articles" not in collections:
        client.create_collection(
            collection_name="articles",
            vectors_config=VectorParams(
                size=settings.EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        client.create_payload_index("articles", "user_id", field_schema="keyword")
        client.create_payload_index("articles", "topic_id", field_schema="keyword")
        logger.info(
            f"Created Qdrant 'articles' collection (dim={settings.EMBEDDING_DIMENSION})"
        )
    else:
        # Validate dimension matches current configuration
        collection_info = client.get_collection("articles")
        existing_dim = collection_info.config.params.vectors.size
        if existing_dim != settings.EMBEDDING_DIMENSION:
            logger.error(
                f"DIMENSION MISMATCH: Qdrant 'articles' collection has dimension "
                f"{existing_dim} but EMBEDDING_DIMENSION is {settings.EMBEDDING_DIMENSION}. "
                f"This will cause incorrect search results. Either change "
                f"EMBEDDING_DIMENSION to {existing_dim} or delete and recreate "
                f"the collection (WARNING: deletes all vectors)."
            )
            raise RuntimeError(
                f"Qdrant dimension mismatch: collection={existing_dim}, "
                f"config={settings.EMBEDDING_DIMENSION}"
            )
        logger.info(
            f"Qdrant 'articles' collection verified (dim={existing_dim})"
        )


def init_minio():
    """Create the content bucket if it doesn't exist."""
    client = Minio(
        settings.MINIO_URL.replace("http://", "").replace("https://", ""),
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_URL.startswith("https"),
    )
    if not client.bucket_exists(settings.MINIO_BUCKET):
        client.make_bucket(settings.MINIO_BUCKET)
        logger.info(f"Created MinIO bucket '{settings.MINIO_BUCKET}'")
    else:
        logger.info(f"MinIO bucket '{settings.MINIO_BUCKET}' already exists")


def init_all():
    """Initialize all external services. Safe to call multiple times.
    
    Re-raises RuntimeError from dimension validation — this is a critical
    misconfiguration that must prevent startup. Other initialization failures
    are logged but don't prevent startup (services may become available later).
    """
    try:
        init_qdrant()
    except RuntimeError:
        # Dimension mismatch — MUST fail fast. Don't swallow this.
        raise
    except Exception as e:
        logger.error(f"Qdrant initialization failed: {e}")

    try:
        init_minio()
    except Exception as e:
        logger.error(f"MinIO initialization failed: {e}")
```

### FastAPI Lifespan (startup/shutdown)

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.services.llm_factory import get_llm_provider
from app.services.embedder import get_embedding_provider
from app.services.init_services import init_all
from app.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize external services and persistent clients
    init_all()  # Qdrant collection + MinIO bucket (idempotent)
    app.state.llm = get_llm_provider()
    app.state.embedder = get_embedding_provider()

    # Start background Redis pub/sub listener for price alert notifications
    import asyncio
    alert_task = asyncio.create_task(ws_alert_listener())

    yield

    # Shutdown: cancel background tasks and close persistent clients
    alert_task.cancel()
    try:
        await alert_task
    except asyncio.CancelledError:
        pass
    await app.state.llm.close()
    await app.state.embedder.close()

app = FastAPI(title="TTwatch API", lifespan=lifespan)

# CORS — supports multiple origins for LAN access
# Set CORS_ORIGINS="http://localhost:3000,http://192.168.1.100:3000"
cors_origins = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
from app.routers import health, topics, clusters, articles, search, briefings
from app.routers import entities, sentiment, sources, queries, investment, market_data
from app.routers import users
from app.auth.router import router as auth_router

app.include_router(health.router, tags=["health"])
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(topics.router, prefix="/api", tags=["topics"])
app.include_router(clusters.router, prefix="/api", tags=["clusters"])
app.include_router(articles.router, prefix="/api", tags=["articles"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(briefings.router, prefix="/api", tags=["briefings"])
app.include_router(entities.router, prefix="/api", tags=["entities"])
app.include_router(sentiment.router, prefix="/api", tags=["sentiment"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(queries.router, prefix="/api", tags=["queries"])
app.include_router(investment.router, prefix="/api", tags=["investment"])
app.include_router(market_data.router, prefix="/api", tags=["market_data"])


# === WebSocket endpoint for real-time dashboard updates ===
from fastapi import WebSocket, WebSocketDisconnect
import json

class ConnectionManager:
    """Manages active WebSocket connections per user."""
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        self.connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.connections:
            self.connections[user_id] = [
                ws for ws in self.connections[user_id] if ws != websocket
            ]

    async def notify_user(self, user_id: str, event: dict):
        for ws in self.connections.get(user_id, []):
            try:
                await ws.send_json(event)
            except Exception:
                pass

ws_manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates (new articles, cluster changes, briefings).
    
    Client sends: {"type": "auth", "token": "<jwt>"}
    Server sends: {"type": "article_ingested", ...}, {"type": "cluster_updated", ...}
    
    Heartbeat: Server sends {"type": "ping"} every 30s.
    Client should respond with {"type": "pong"} to keep the connection alive.
    Connections idle for >90s without pong are terminated.
    """
    import asyncio
    user_id = None
    try:
        await websocket.accept()
        # Wait for auth message (with timeout to prevent dangling connections)
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        except asyncio.TimeoutError:
            await websocket.close(code=4001, reason="Auth timeout")
            return
        if auth_msg.get("type") != "auth" or not auth_msg.get("token"):
            await websocket.close(code=4001, reason="Auth required")
            return

        try:
            import jwt as pyjwt
            payload = pyjwt.decode(
                auth_msg["token"], settings.JWT_SECRET, algorithms=["HS256"]
            )
            user_id = payload.get("sub")
        except Exception:
            await websocket.close(code=4001, reason="Invalid token")
            return

        await ws_manager.connect(user_id, websocket)
        await websocket.send_json({"type": "connected", "user_id": user_id})

        # Heartbeat + message loop
        last_pong = asyncio.get_running_loop().time()
        while True:
            try:
                # Wait for client message with 30s timeout (heartbeat interval)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(data) if data else {}
                if msg.get("type") == "pong":
                    last_pong = asyncio.get_running_loop().time()
            except asyncio.TimeoutError:
                # No message received — send ping and check staleness
                now = asyncio.get_running_loop().time()
                if now - last_pong > 90.0:
                    # Client hasn't responded in 90s — assume dead connection
                    await websocket.close(code=4002, reason="Heartbeat timeout")
                    break
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        if user_id:
            ws_manager.disconnect(user_id, websocket)


# === Redis pub/sub listener for price alert notifications ===
async def ws_alert_listener():
    """Background task: subscribe to Redis pub/sub for triggered price alerts
    and forward them to the appropriate user's WebSocket connections.
    
    Workers publish to 'ttwatch:alerts:triggered' (synchronous Redis).
    This coroutine subscribes asynchronously and bridges to ws_manager.
    Started during API lifespan; cancelled on shutdown.
    """
    import redis.asyncio as aioredis
    alert_redis = aioredis.from_url(settings.REDIS_CACHE_URL)
    pubsub = alert_redis.pubsub()
    await pubsub.subscribe("ttwatch:alerts:triggered")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    user_id = data.pop("user_id", None)
                    if user_id:
                        await ws_manager.notify_user(user_id, data)
                except (json.JSONDecodeError, KeyError):
                    pass
    finally:
        await pubsub.unsubscribe("ttwatch:alerts:triggered")
        await alert_redis.close()
```

### HTTP Retry Strategy

```python
# services/api/app/services/http_utils.py
import logging
import httpx
import tenacity

logger = logging.getLogger(__name__)

def _is_retryable(exception: BaseException) -> bool:
    """Only retry server errors, rate limits, and connection failures.
    Never retry 4xx client errors.
    """
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500 or \
               exception.response.status_code == 429
    return isinstance(exception, (httpx.TimeoutException, httpx.ConnectError, ConnectionError))

retry_config = dict(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=30),
    retry=tenacity.retry_if_exception(_is_retryable),
    before_sleep=lambda rs: logger.warning(
        f"Retry {rs.attempt_number} for {rs.fn.__name__}"
    ),
)

# Extended retry for LAN service startup — used by workers during boot
lan_startup_retry = dict(
    stop=tenacity.stop_after_attempt(30),
    wait=tenacity.wait_exponential(multiplier=2, min=5, max=60),
    retry=tenacity.retry_if_exception(_is_retryable),
    before_sleep=lambda rs: logger.info(
        f"Waiting for remote service ({rs.attempt_number}/30)..."
    ),
)
```

---

## 5. Service Architecture (Docker Compose)

### Environment Anchor

```yaml
# docker-compose.yml — NO `version:` key (Compose V2 infers spec automatically)

x-common-env: &common-env
  REDIS_URL: redis://redis:6379/0
  CELERY_RESULT_BACKEND: redis://redis:6379/1
  REDIS_DEDUP_URL: redis://redis:6379/2
  REDIS_CACHE_URL: redis://redis:6379/3
  QDRANT_URL: http://qdrant:6333
  VLLM_URL: ${VLLM_URL:-http://vllm:8000/v1}
  EMBEDDER_URL: ${EMBEDDER_URL:-http://embedder:8001}
  SEARXNG_URL: ${SEARXNG_URL:-http://searxng:8080}
  MINIO_URL: ${MINIO_URL:-http://minio:9000}
  MINIO_ACCESS_KEY: ${MINIO_ROOT_USER:-minioadmin}
  MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD:-minioadmin}
  MINIO_BUCKET: ${MINIO_BUCKET:-ttwatch-content}
  LLM_PROVIDER: ${LLM_PROVIDER:-local}
  LOCAL_MODEL_NAME: ${LOCAL_MODEL_NAME:-Qwen2.5-32B-Instruct-AWQ}
  EMBEDDING_DIMENSION: ${EMBEDDING_DIMENSION:-1024}
  JWT_SECRET: ${JWT_SECRET}
```

**Note:** `DATABASE_URL` is intentionally NOT in the common anchor — each service sets its own with the correct role credentials. This prevents accidental superuser access. All service URLs use env vars with defaults, enabling LAN overrides.

### Storage Services

```yaml
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/init-db.sh:/docker-entrypoint-initdb.d/01-init.sh:ro
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-ttwatch}
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      APP_DB_PASSWORD: ${APP_DB_PASSWORD}
      WORKER_DB_PASSWORD: ${WORKER_DB_PASSWORD}
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-postgres}"]
      interval: 5s
      timeout: 5s
      retries: 5

  qdrant:
    image: qdrant/qdrant:v1.12.1
    restart: unless-stopped
    volumes: [qdrant_data:/qdrant/storage]
    ports: ["6333:6333"]
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:6333/healthz"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: >
      redis-server
      --appendonly yes
      --maxmemory 512mb
      --maxmemory-policy volatile-lru
    volumes: [redis_data:/data]
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  minio:
    image: minio/minio:RELEASE.2024-11-07T00-52-20Z
    restart: unless-stopped
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]
    volumes: [minio_data:/data]
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5
```

### Search Service

```yaml
  searxng:
    image: searxng/searxng:2024.11.17-b17f4d04a
    restart: unless-stopped
    ports: ["8888:8080"]
    volumes: [./config/searxng:/etc/searxng]
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/healthz"]
      interval: 10s
      timeout: 5s
      retries: 3
```

### Application Services

```yaml
  api:
    build:
      context: .
      dockerfile: services/api/Dockerfile
    ports: ["8080:8080"]
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    environment:
      <<: *common-env
      DATABASE_URL: postgresql://ttwatch_app:${APP_DB_PASSWORD}@postgres:5432/${POSTGRES_DB:-ttwatch}
      CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000}
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/health"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  worker-io:
    build:
      context: .
      dockerfile: services/worker/Dockerfile
    command: >
      celery -A worker.celeryconfig worker
      --loglevel=info
      --pool=gevent
      --concurrency=32
      --prefetch-multiplier=1
      -Q ttwatch:default
      -n io-worker@%h
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    environment:
      <<: *common-env
      DATABASE_URL: postgresql://ttwatch_worker:${WORKER_DB_PASSWORD}@postgres:5432/${POSTGRES_DB:-ttwatch}
    restart: unless-stopped

  worker-cpu:
    build:
      context: .
      dockerfile: services/worker/Dockerfile
    command: >
      celery -A worker.celeryconfig worker
      --loglevel=info
      --pool=prefork
      --concurrency=2
      --prefetch-multiplier=1
      -Q ttwatch:compute
      -n cpu-worker@%h
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    environment:
      <<: *common-env
      DATABASE_URL: postgresql://ttwatch_worker:${WORKER_DB_PASSWORD}@postgres:5432/${POSTGRES_DB:-ttwatch}
    restart: unless-stopped

  scheduler:
    build:
      context: .
      dockerfile: services/worker/Dockerfile
    command: >
      celery -A worker.celeryconfig beat
      --loglevel=info
      --schedule=/tmp/celerybeat-schedule
    depends_on:
      redis: { condition: service_healthy }
    environment:
      <<: *common-env
      DATABASE_URL: postgresql://ttwatch_worker:${WORKER_DB_PASSWORD}@postgres:5432/${POSTGRES_DB:-ttwatch}
    restart: unless-stopped

  frontend:
    build:
      context: .
      dockerfile: services/frontend/Dockerfile
    ports: ["3000:3000"]
    depends_on: [api]
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8080}
      INTERNAL_API_URL: http://api:8080
      NEXT_PUBLIC_WS_URL: ${NEXT_PUBLIC_WS_URL:-ws://localhost:8080/ws}
    restart: unless-stopped

volumes:
  pgdata:
  qdrant_data:
  redis_data:
  minio_data:
```

### GPU Compose Override — Colocated (docker-compose.gpu.yml)

Use when vLLM and embedder run on the **same machine** as the platform.

```yaml
services:
  embedder:
    build: ./services/embedder
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          devices: [{capabilities: [gpu]}]
    ports: ["8101:8001"]
    environment:
      CUDA_VISIBLE_DEVICES: "0"
      MODEL_NAME: "BAAI/bge-m3"
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8001/health"]
      interval: 10s
      timeout: 10s
      retries: 5

  vllm:
    image: vllm/vllm-openai:v0.7.3
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          devices: [{capabilities: [gpu]}]
    volumes: [./models:/models]
    command: >
      --model /models/Qwen2.5-32B-Instruct-AWQ
      --quantization awq
      --gpu-memory-utilization 0.85
      --max-model-len 32768
      --max-num-seqs 8
      --enable-prefix-caching
      --port 8000
    ports: ["8100:8000"]
    depends_on:
      embedder: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8000/health"]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 120s

  # Override dependencies to include GPU services
  api:
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
      vllm: { condition: service_healthy }
      embedder: { condition: service_healthy }

  worker-io:
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
      vllm: { condition: service_healthy }
      embedder: { condition: service_healthy }

  worker-cpu:
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
      vllm: { condition: service_healthy }
      embedder: { condition: service_healthy }
```

### GPU Node Compose — Remote GPU Machine (docker-compose.gpu-node.yml)

**Runs on the GPU machine only.** Hosts vLLM and the embedder as standalone services accessible over the LAN.

```yaml
# docker-compose.gpu-node.yml
# Run on the GPU machine: docker compose -f docker-compose.gpu-node.yml up -d

services:
  embedder:
    build: ./services/embedder
    deploy:
      resources:
        reservations:
          devices: [{capabilities: [gpu]}]
    ports: ["8001:8001"]
    environment:
      CUDA_VISIBLE_DEVICES: "0"
      MODEL_NAME: "BAAI/bge-m3"
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8001/health"]
      interval: 10s
      timeout: 10s
      retries: 5
    restart: unless-stopped

  vllm:
    image: vllm/vllm-openai:v0.7.3
    deploy:
      resources:
        reservations:
          devices: [{capabilities: [gpu]}]
    volumes: [./models:/models]
    command: >
      --model /models/Qwen2.5-32B-Instruct-AWQ
      --quantization awq
      --gpu-memory-utilization 0.85
      --max-model-len 32768
      --max-num-seqs 8
      --enable-prefix-caching
      --port 8000
      --host 0.0.0.0
    ports: ["8000:8000"]
    depends_on:
      embedder: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8000/health"]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 120s
    restart: unless-stopped
```

### Search Node Compose — Remote Search Machine (docker-compose.search-node.yml)

```yaml
# docker-compose.search-node.yml
services:
  searxng:
    image: searxng/searxng:2024.11.17-b17f4d04a
    ports: ["8080:8080"]
    volumes: [./config/searxng:/etc/searxng]
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/healthz"]
      interval: 10s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

### LAN Compose Override — Main Server (docker-compose.lan.yml)

```yaml
# docker-compose.lan.yml
# Usage: docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d

services:
  searxng:
    profiles: ["disabled"]

  worker-io:
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }

  api:
    depends_on:
      postgres: { condition: service_healthy }
      qdrant: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
```

### Cloud Compose Override (docker-compose.cloud.yml)

```yaml
services:
  api:
    environment:
      VLLM_URL: ""
      LLM_PROVIDER: cloud
      CLOUD_LLM_PROVIDER: ${CLOUD_LLM_PROVIDER:-openai}
      CLOUD_LLM_API_KEY: ${CLOUD_LLM_API_KEY}
      CLOUD_LLM_MODEL: ${CLOUD_LLM_MODEL:-gpt-4o-mini}
      EMBEDDER_URL: ""
      CLOUD_EMBEDDING_PROVIDER: ${CLOUD_EMBEDDING_PROVIDER:-openai}
      CLOUD_EMBEDDING_MODEL: ${CLOUD_EMBEDDING_MODEL:-text-embedding-3-large}
      EMBEDDING_DIMENSION: "3072"

  worker-io:
    environment:
      VLLM_URL: ""
      LLM_PROVIDER: cloud
      CLOUD_LLM_PROVIDER: ${CLOUD_LLM_PROVIDER:-openai}
      CLOUD_LLM_API_KEY: ${CLOUD_LLM_API_KEY}
      CLOUD_LLM_MODEL: ${CLOUD_LLM_MODEL:-gpt-4o-mini}
      EMBEDDER_URL: ""
      CLOUD_EMBEDDING_PROVIDER: ${CLOUD_EMBEDDING_PROVIDER:-openai}
      CLOUD_EMBEDDING_MODEL: ${CLOUD_EMBEDDING_MODEL:-text-embedding-3-large}
      EMBEDDING_DIMENSION: "3072"

  worker-cpu:
    environment:
      VLLM_URL: ""
      LLM_PROVIDER: cloud
      CLOUD_LLM_PROVIDER: ${CLOUD_LLM_PROVIDER:-openai}
      CLOUD_LLM_API_KEY: ${CLOUD_LLM_API_KEY}
      CLOUD_LLM_MODEL: ${CLOUD_LLM_MODEL:-gpt-4o-mini}
      EMBEDDER_URL: ""
      CLOUD_EMBEDDING_PROVIDER: ${CLOUD_EMBEDDING_PROVIDER:-openai}
      CLOUD_EMBEDDING_MODEL: ${CLOUD_EMBEDDING_MODEL:-text-embedding-3-large}
      EMBEDDING_DIMENSION: "3072"

  scheduler:
    environment:
      VLLM_URL: ""
      LLM_PROVIDER: cloud
      CLOUD_LLM_PROVIDER: ${CLOUD_LLM_PROVIDER:-openai}
      CLOUD_LLM_API_KEY: ${CLOUD_LLM_API_KEY}
      CLOUD_LLM_MODEL: ${CLOUD_LLM_MODEL:-gpt-4o-mini}
      EMBEDDER_URL: ""
      CLOUD_EMBEDDING_PROVIDER: ${CLOUD_EMBEDDING_PROVIDER:-openai}
      CLOUD_EMBEDDING_MODEL: ${CLOUD_EMBEDDING_MODEL:-text-embedding-3-large}
      EMBEDDING_DIMENSION: "3072"
```

**Usage:**
- Single machine + local GPU: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up`
- **LAN distributed (GPU on remote):** `docker compose -f docker-compose.yml -f docker-compose.lan.yml up`
- Cloud only: `docker compose -f docker-compose.yml -f docker-compose.cloud.yml up`
- Development: add `-f docker-compose.dev.yml` to any of the above

### Dev Compose Override (docker-compose.dev.yml)

```yaml
services:
  api:
    build:
      context: .
      dockerfile: services/api/Dockerfile
      target: dev
    volumes:
      - ./services/api/app:/app/app:ro
    command: >
      uvicorn app.main:app
      --host 0.0.0.0 --port 8080
      --reload --reload-dir /app/app
    environment:
      # NOTE: Cannot use *common-env here — YAML anchors don't cross file
      # boundaries. Instead, the base compose already sets environment via
      # merge. Only override or add dev-specific vars here.
      DEBUG: "true"

  worker-io:
    volumes:
      - ./services/worker/worker:/app/worker:ro
    command: >
      watchmedo auto-restart
      --directory=/app/worker --pattern="*.py" --recursive --
      celery -A worker.celeryconfig worker
      --loglevel=debug --pool=solo --concurrency=1
      -Q ttwatch:default

  frontend:
    volumes:
      - ./services/frontend/src:/app/src:ro
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8080}
      INTERNAL_API_URL: http://api:8080
      WATCHPACK_POLLING: "true"
```

### SearXNG Configuration

```yaml
# config/searxng/settings.yml
use_default_settings: true

general:
  instance_name: "TTwatch Search"
  enable_metrics: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json

server:
  secret_key: "change-me-in-production"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false

ui:
  static_use_hash: true
  default_theme: simple

outgoing:
  request_timeout: 10.0
  max_request_timeout: 15.0
  useragent_suffix: "TTwatch"
  pool_connections: 100
  pool_maxsize: 20

engines:
  - name: google
    engine: google
    shortcut: g
    disabled: false
  - name: bing
    engine: bing
    shortcut: b
    disabled: false
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    disabled: false
  - name: google news
    engine: google_news
    shortcut: gn
    disabled: false
  - name: bing news
    engine: bing_news
    shortcut: bn
    disabled: false
```

### Redis DB Separation Strategy

| DB | Purpose | TTL | Eviction Behavior |
|----|---------|-----|-------------------|
| 0 | Celery broker (task queues) | No TTL | Never evicted (volatile-lru only evicts keys with TTL) |
| 1 | Celery results backend | 1 hour TTL | Can be evicted under memory pressure |
| 2 | Dedup sets, sessions | No TTL | Never evicted |
| 3 | Market data cache, rate limits | TTL per key | Can be evicted under memory pressure |

### Database Initialization (safe password handling)

```bash
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
```

### .env.example

```bash
# ================================================================
# TTwatch Environment Configuration
# Copy to .env and customize before first launch.
# ================================================================

# === PostgreSQL ===
POSTGRES_USER=postgres
POSTGRES_PASSWORD=changeme_super
POSTGRES_DB=ttwatch
APP_DB_PASSWORD=changeme_app
WORKER_DB_PASSWORD=changeme_worker

# === JWT ===
JWT_SECRET=generate-a-256-bit-random-secret-here

# === Redis ===
REDIS_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
REDIS_DEDUP_URL=redis://redis:6379/2
REDIS_CACHE_URL=redis://redis:6379/3

# === MinIO ===
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=changeme_minio
MINIO_BUCKET=ttwatch-content

# === LLM (defaults for local mode) ===
LLM_PROVIDER=local
VLLM_URL=http://vllm:8000/v1
LOCAL_MODEL_NAME=Qwen2.5-32B-Instruct-AWQ
EMBEDDER_URL=http://embedder:8001
EMBEDDING_DIMENSION=1024

# === SearXNG ===
SEARXNG_URL=http://searxng:8080

# === Cloud LLM (fallback — optional) ===
CLOUD_LLM_PROVIDER=openai
CLOUD_LLM_API_KEY=
CLOUD_LLM_MODEL=gpt-4o-mini
CLOUD_EMBEDDING_PROVIDER=openai
CLOUD_EMBEDDING_MODEL=text-embedding-3-large

# === Frontend URLs ===
NEXT_PUBLIC_API_URL=http://localhost:8080
NEXT_PUBLIC_WS_URL=ws://localhost:8080/ws

# === CORS ===
CORS_ORIGINS=http://localhost:3000
```

---

## 6. Authentication & User Management

### Auth Strategy

```
Frontend (browser)  ──► Login ──► JWT (short-lived, 15min + refresh token 30d)
OpenClaw Agent      ──► X-API-Key header ──► resolves to user_id
```

### Password Hashing & Security

```python
import os
from datetime import timedelta
from argon2 import PasswordHasher

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE = timedelta(days=30)
```

### API Key Design

```
Format:  tw_live_{user_short_id}_{random_32_chars}
Example: tw_live_u7x2_a8f3b2c1d4e5f6a7b8c9d0e1f2a3b4c5

Storage: SHA-256 hash of the key (never store plaintext)
Lookup:  prefix → candidate rows → hash comparison
```

### Database Dependencies

```python
# services/api/app/deps.py
import os
import hashlib
import uuid
from datetime import datetime, timezone

import jwt
import redis.asyncio as aioredis
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from fastapi import Depends, HTTPException, Security, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings
from app.models import User, ApiKey

# === Database Engine ===
engine = create_async_engine(
    settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://"),
    pool_size=20,
    max_overflow=10,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    """FastAPI dependency that yields a DB session per request."""
    async with async_session() as session:
        async with session.begin():
            yield session

# === Redis Connections ===
dedup_redis = aioredis.from_url(
    os.environ.get("REDIS_DEDUP_URL", "redis://redis:6379/2")
)
cache_redis = aioredis.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)
```

```python
# services/worker/worker/db.py
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import os

# Patch psycopg2 for gevent compatibility.
# This MUST happen before any database connections are created.
# It's safe to call in prefork workers too (no-op if gevent isn't active).
try:
    from psycogreen.gevent import patch_psycopg
    patch_psycopg()
except ImportError:
    pass  # psycogreen not installed — not using gevent pool

_engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql://ttwatch_worker:changeme@postgres:5432/ttwatch"),
    pool_size=5,
    max_overflow=5,
)
_SessionFactory = sessionmaker(bind=_engine)

@contextmanager
def db_session() -> Session:
    """Synchronous session for Celery worker tasks."""
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

### Authentication Dependency

```python
# services/api/app/deps.py (continued)

bearer_scheme = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve user from JWT token OR API key. Exactly one must be present."""

    if credentials and credentials.credentials:
        try:
            payload = jwt.decode(
                credentials.credentials, settings.JWT_SECRET, algorithms=["HS256"]
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Invalid token")

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Invalid token payload")
        try:
            user_id = uuid.UUID(user_id)
        except (ValueError, AttributeError):
            raise HTTPException(401, "Invalid token payload")
        user = await db.get(User, user_id)
        if not user or not user.is_active:
            raise HTTPException(401, "User not found or inactive")
        return user

    elif api_key:
        prefix = api_key[:14]
        candidates = await db.execute(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.is_active == True
            )
        )
        for candidate in candidates.scalars():
            if hashlib.sha256(api_key.encode()).hexdigest() == candidate.key_hash:
                candidate.last_used_at = datetime.now(timezone.utc)
                user = await db.get(User, candidate.user_id)
                if not user or not user.is_active:
                    raise HTTPException(401, "User inactive")
                return user
        raise HTTPException(401, "Invalid API key")

    raise HTTPException(401, "Authentication required")


async def set_rls_context(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set PostgreSQL RLS context for this request.

    Uses f-string formatting, which is safe here because the UUID is
    validated via round-trip: str(uuid.UUID(...)) guarantees only
    [0-9a-f-] characters. PostgreSQL SET does not accept bind parameters.
    """
    validated_id = str(uuid.UUID(str(user.id)))
    await db.execute(text(
        f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
    ))
    return user
```

### Rate Limiting (atomic Lua script with orphan healing)

```python
# app/middleware/rate_limit.py
import redis.asyncio as aioredis
from fastapi import HTTPException

RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
else
    local ttl = redis.call('TTL', key)
    if ttl == -1 then
        redis.call('EXPIRE', key, window)
    end
end

if current > limit then
    return 0
end
return 1
"""

class RateLimiter:
    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url)
        self._script = self.redis.register_script(RATE_LIMIT_SCRIPT)

    async def check(self, user_id: str, endpoint: str,
                    limit: int = 60, window: int = 60) -> bool:
        key = f"ttwatch:rate:{user_id}:{endpoint}"
        allowed = await self._script(keys=[key], args=[limit, window])
        if not allowed:
            raise HTTPException(429, detail="Rate limit exceeded")
        return True
```

```python
# In app/deps.py (after Redis connections) — wire the rate limiter
from app.middleware.rate_limit import RateLimiter

rate_limiter = RateLimiter(
    redis_url=os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)

async def rate_limit_dependency(
    user: User = Depends(get_current_user),
    request: Request = None,
):
    """Apply rate limiting per user per endpoint."""
    endpoint = request.url.path if request else "unknown"
    await rate_limiter.check(str(user.id), endpoint)
    return user
```

### Auth Router

```python
# services/api/app/auth/router.py
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db, get_current_user
from app.models import User, ApiKey, RefreshToken

router = APIRouter()
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

ACCESS_TOKEN_EXPIRE = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE = timedelta(days=30)


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        """Validate password meets minimum strength requirements."""
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


def _create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + ACCESS_TOKEN_EXPIRE,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def _create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Email already registered")

    user = User(
        email=req.email,
        display_name=req.display_name,
        password_hash=ph.hash(req.password),
    )
    db.add(user)

    # flush() can raise IntegrityError if a concurrent request registered
    # the same email between our SELECT check and this INSERT. Handle it
    # gracefully instead of letting it bubble as HTTP 500.
    try:
        await db.flush()
    except Exception as e:
        # Check for unique constraint violation (asyncpg UniqueViolationError)
        error_str = str(e).lower()
        if "unique" in error_str or "duplicate" in error_str or "23505" in error_str:
            raise HTTPException(409, "Email already registered")
        raise

    refresh_raw = _create_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hashlib.sha256(refresh_raw.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRE,
    )
    db.add(rt)

    return TokenResponse(
        access_token=_create_access_token(str(user.id)),
        refresh_token=refresh_raw,
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "Invalid credentials")

    try:
        ph.verify(user.password_hash, req.password)
    except VerifyMismatchError:
        raise HTTPException(401, "Invalid credentials")

    if ph.check_needs_rehash(user.password_hash):
        user.password_hash = ph.hash(req.password)

    user.last_login_at = datetime.now(timezone.utc)

    refresh_raw = _create_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hashlib.sha256(refresh_raw.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRE,
    )
    db.add(rt)

    # Cap active (unexpired) refresh tokens per user at 10. Without this, each login
    # creates a new token indefinitely (multiple devices, page refreshes).
    # Delete oldest tokens beyond the cap to prevent unbounded accumulation.
    # IMPORTANT: Only count unexpired tokens — expired tokens are functionally dead
    # (rejected by /auth/refresh) and cleaned up by the daily cleanup task.
    # Counting all tokens would prematurely trigger the cap when expired tokens
    # accumulate, potentially deleting the user's only active session.
    from sqlalchemy import func as sa_func
    token_count_result = await db.execute(
        select(sa_func.count(RefreshToken.id)).where(
            RefreshToken.user_id == user.id,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    active_count = token_count_result.scalar()
    if active_count > 10:
        oldest_tokens = await db.execute(
            select(RefreshToken.id).where(
                RefreshToken.user_id == user.id,
                RefreshToken.expires_at > datetime.now(timezone.utc),
            ).order_by(RefreshToken.created_at.asc()).limit(
                active_count - 10
            )
        )
        old_ids = [row[0] for row in oldest_tokens.all()]
        if old_ids:
            await db.execute(
                RefreshToken.__table__.delete().where(RefreshToken.id.in_(old_ids))
            )

    return TokenResponse(
        access_token=_create_access_token(str(user.id)),
        refresh_token=refresh_raw,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hashlib.sha256(req.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    rt = result.scalar_one_or_none()
    if not rt:
        raise HTTPException(401, "Invalid or expired refresh token")

    user = await db.get(User, rt.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User inactive")

    # Rotate: delete old, issue new
    await db.delete(rt)

    new_refresh_raw = _create_refresh_token()
    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=hashlib.sha256(new_refresh_raw.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRE,
    )
    db.add(new_rt)

    return TokenResponse(
        access_token=_create_access_token(str(user.id)),
        refresh_token=new_refresh_raw,
    )


@router.post("/logout")
async def logout(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Invalidate a refresh token on explicit logout.
    
    Accepts the refresh token and deletes it from the database,
    preventing it from being used to generate new access tokens.
    The current access token will expire naturally (15 min).
    """
    token_hash = hashlib.sha256(req.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()
    if rt:
        await db.delete(rt)
    # Always return 200 — don't reveal whether the token existed
    return {"status": "logged_out"}
```

### Health Check Endpoint

```python
# app/routers/health.py
import httpx
from fastapi import APIRouter, Request
from app.config import settings

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/health/services")
async def service_health(request: Request):
    """Extended health check — reports connectivity to all services."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        if settings.VLLM_URL:
            try:
                base = settings.VLLM_URL.replace("/v1", "")
                resp = await client.get(f"{base}/health")
                results["vllm"] = resp.status_code == 200
            except Exception:
                results["vllm"] = False

        if settings.EMBEDDER_URL:
            try:
                resp = await client.get(f"{settings.EMBEDDER_URL}/health")
                results["embedder"] = resp.status_code == 200
            except Exception:
                results["embedder"] = False

        if settings.SEARXNG_URL:
            try:
                resp = await client.get(f"{settings.SEARXNG_URL}/healthz")
                results["searxng"] = resp.status_code == 200
            except Exception:
                results["searxng"] = False

        try:
            resp = await client.get(f"{settings.QDRANT_URL}/healthz")
            results["qdrant"] = resp.status_code == 200
        except Exception:
            results["qdrant"] = False

    # Check PostgreSQL
    try:
        from app.deps import engine
        from sqlalchemy import text as sa_text
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
        results["postgres"] = True
    except Exception:
        results["postgres"] = False

    # Check Redis
    try:
        from app.deps import cache_redis
        await cache_redis.ping()
        results["redis"] = True
    except Exception:
        results["redis"] = False

    results["mode"] = settings.LLM_PROVIDER
    return results
```

### API Service Dockerfile & Dependencies

```dockerfile
# services/api/Dockerfile
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/api/app /app/app
COPY config/alembic.ini /app/alembic.ini
COPY migrations /app/migrations

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]

# Dev target: single worker, no copy (volumes mounted)
FROM base AS dev
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
```

**`services/api/requirements.txt`:**
```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic>=2.0
pydantic-settings>=2.0
email-validator>=2.0.0
sqlalchemy[asyncio]>=2.0
asyncpg>=0.30.0
alembic>=1.14.0
httpx>=0.27.0
qdrant-client>=1.12.0
aiohttp>=3.9.0
redis>=5.0.0
minio>=7.2.0
argon2-cffi>=23.1.0
PyJWT>=2.9.0
tenacity>=9.0.0
trafilatura>=1.12.0
python-multipart>=0.0.12
```

### Worker Service Dockerfile & Dependencies

```dockerfile
# services/worker/Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Worker needs app.models for ORM — copy the API app models
COPY services/api/app /app/app
COPY services/worker/worker /app/worker

CMD ["celery", "-A", "worker.celeryconfig", "worker", "--loglevel=info"]
```

**`services/worker/requirements.txt`:**
```
celery[redis,gevent]>=5.4.0
sqlalchemy>=2.0
psycopg2-binary>=2.9.0
psycogreen>=1.0.2
httpx>=0.27.0
qdrant-client>=1.12.0
redis>=5.0.0
minio>=7.2.0
tenacity>=9.0.0
trafilatura>=1.12.0
watchdog>=5.0.0
hdbscan>=0.8.38
umap-learn>=0.5.7
numpy>=1.26.0
yfinance>=0.2.40
pydantic>=2.0
pydantic-settings>=2.0
```

### Frontend Dockerfile

```dockerfile
# services/frontend/Dockerfile
FROM node:20-alpine AS base
WORKDIR /app

FROM base AS deps
COPY services/frontend/package.json services/frontend/package-lock.json* ./
RUN npm ci

FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY services/frontend/ .
RUN npm run build

FROM base AS runner
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
```

---

## 7. Data Pipeline Architecture

### 7.1 Ingestion Pipeline

Every job carries a `user_id` from the moment it enters the queue.

```
User triggers search (via UI or OpenClaw agent)
    │  user_id attached at entry point
    ▼
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────┐
│ SearXNG Search   │────▶│ URL Queue             │────▶│ Trafilatura   │
│ + RSS Feeds      │     │ (Redis DB 2)          │     │ Extraction    │
│ + Custom Sources │     │ dedup by user+URL     │     │              │
└─────────────────┘     └──────────────────────┘     └──────┬───────┘
                                                            │
                         user_id propagates through ────────┤
                                                            ▼
                                                     ┌──────────────┐
                                                     │ Content Store │
                                                     │ (MinIO)      │
                                                     │ /{user_id}/  │
                                                     └──────┬───────┘
                                                            │
                                                            ▼
                                                     ┌──────────────┐
                                                     │ Processing   │
                                                     │ Queue (Redis)│
                                                     │ user_id in   │
                                                     │ every task   │
                                                     └──────────────┘
```

### 7.2 Processing Pipeline (Worker Tasks)

**Intelligence tasks (per-article, routed to `ttwatch:default` I/O worker):**

| # | Task | Tier | Input |
|---|------|------|-------|
| 1 | `embed_article` | — | BGE-M3 → Qdrant |
| 2 | `summarize_article` | 1 (≤4K) | Qwen |
| 3 | `extract_entities` | 1 (≤4K) | Qwen |
| 4 | `classify_sentiment` | 1 (≤2K) | Qwen |
| 5 | `dedup_check` | — | Qdrant similarity |
| 6 | `store_metadata` | — | PostgreSQL |

**Investment tasks (per-article, routed to `ttwatch:default` I/O worker):**

| # | Task | Tier | Input |
|---|------|------|-------|
| 7 | `resolve_entity_ticker` | 1 (≤4K) | Qwen JSON |
| 8 | `fetch_market_data` | — | yfinance/CoinGecko |
| 9 | `generate_asset_analysis` | 2 (≤8K) | Qwen |
| 10 | `detect_correlation_signals` | 2 (≤6K) | Qwen |
| 11 | `check_price_alerts` | — | PostgreSQL |

**Periodic aggregate tasks (per user, per topic):**

| # | Task | Queue | Frequency |
|---|------|-------|-----------|
| 12 | `recluster_topic` | `ttwatch:compute` (CPU) | Every 2 hours |
| 13 | `update_trends` | `ttwatch:compute` (CPU) | Every 2 hours |
| 14 | `generate_briefing` | `ttwatch:default` (I/O) | Every 2 hours |
| 15 | `detect_coverage_gaps` | `ttwatch:default` (I/O) | Every 2 hours |
| 16 | `compute_sentiment_history` | `ttwatch:default` (I/O) | Every 2 hours |
| 17 | `generate_investment_analyses` | `ttwatch:default` (I/O) | Daily |
| 18 | `cleanup_stale_snapshots` | `ttwatch:default` (I/O) | Daily |
| 19 | `refresh_market_data` | `ttwatch:default` (I/O) | Hourly |
| 20 | `cleanup_orphaned_qdrant_points` | `ttwatch:default` (I/O) | Daily |
| 21 | `check_price_alerts` | `ttwatch:default` (I/O) | Every 15 min |
| 22 | `schedule_correlation_signals` | `ttwatch:default` (I/O) | Every 4 hours |
| 23 | `cleanup_expired_refresh_tokens` | `ttwatch:default` (I/O) | Daily |

**Why two worker pools:**
- `worker-io` (gevent, concurrency=32): Handles I/O-bound tasks — HTTP calls to vLLM, embedder, SearXNG, market APIs.
- `worker-cpu` (prefork, concurrency=2): Handles CPU-bound tasks — HDBSCAN clustering, UMAP dimensionality reduction.

### Celery Configuration

```python
# worker/celeryconfig.py
import os
from celery import Celery
from celery.schedules import crontab

app = Celery("ttwatch")

app.conf.broker_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
app.conf.result_backend = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
app.conf.result_expires = 3600

# Task routing: CPU-bound tasks to compute queue, everything else to default
app.conf.task_routes = {
    "recluster_topic": {"queue": "ttwatch:compute"},
    "update_trends": {"queue": "ttwatch:compute"},
}

app.conf.task_default_queue = "ttwatch:default"

# Task serialization
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]

# Task discovery
app.autodiscover_tasks(["worker.tasks"])

# Beat schedule
app.conf.beat_schedule = {
    "discover-queues": {
        "task": "discover_queues",
        "schedule": 120.0,  # Every 2 minutes (not 15s — reduces Redis SCAN load)
    },
    "schedule-reclustering": {
        "task": "schedule_reclustering",
        "schedule": crontab(minute=0, hour="*/2"),
    },
    "schedule-trend-updates": {
        "task": "schedule_trend_updates",
        "schedule": crontab(minute=5, hour="*/2"),
    },
    "schedule-briefings": {
        "task": "schedule_briefings",
        "schedule": crontab(minute=10, hour="*/2"),
    },
    "schedule-coverage-gaps": {
        "task": "schedule_coverage_gaps",
        "schedule": crontab(minute=15, hour="*/2"),
    },
    "schedule-sentiment-history": {
        "task": "schedule_sentiment_history",
        "schedule": crontab(minute=20, hour="*/2"),
    },
    "refresh-market-data": {
        "task": "refresh_market_data",
        "schedule": crontab(minute=0),
    },
    "schedule-investment-analyses": {
        "task": "schedule_investment_analyses",
        "schedule": crontab(hour=6, minute=0),
    },
    "cleanup-market-data": {
        "task": "cleanup_stale_market_data",
        "schedule": crontab(hour=3, minute=0),
    },
    "cleanup-stale-snapshots": {
        "task": "cleanup_stale_snapshots",
        "schedule": crontab(hour=3, minute=30),
    },
    "cleanup-orphaned-qdrant": {
        "task": "cleanup_orphaned_qdrant_points",
        "schedule": crontab(hour=4, minute=0),
    },
    "check-price-alerts": {
        "task": "check_price_alerts",
        "schedule": crontab(minute="*/15"),  # Every 15 minutes
    },
    "schedule-correlation-signals": {
        "task": "schedule_correlation_signals",
        "schedule": crontab(minute=30, hour="*/4"),  # Every 4 hours
    },
    "cleanup-expired-refresh-tokens": {
        "task": "cleanup_expired_refresh_tokens",
        "schedule": crontab(hour=2, minute=30),  # Daily at 2:30 AM
    },
}
```

### 7.2.1 Worker RLS Context Decorator

```python
# worker/rls.py
import functools
import uuid
from celery import Task
from sqlalchemy import text
from worker.db import db_session

def with_rls_context(func):
    """Decorator that sets PostgreSQL RLS context for the task's user_id.
    
    Handles both bound tasks (@app.task(bind=True)) where `self` is the
    first argument, and unbound tasks where `user_id` is the first argument.
    
    Uses f-string formatting, safe because UUID is validated via round-trip.
    PostgreSQL SET does not accept bind parameters ($1).
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Handle bind=True tasks where first arg is the Task instance
        if args and isinstance(args[0], Task):
            task_self = args[0]
            user_id = args[1] if len(args) > 1 else kwargs.pop("user_id", None)
            remaining_args = args[2:]
        else:
            task_self = None
            user_id = args[0] if args else kwargs.pop("user_id", None)
            remaining_args = args[1:]
        
        if not user_id:
            raise ValueError("with_rls_context: user_id is required")
        
        validated_id = str(uuid.UUID(user_id))
        with db_session() as session:
            session.execute(text(
                f"SET LOCAL ttwatch.current_user_id = '{validated_id}'"
            ))
            if task_self is not None:
                return func(task_self, user_id, *remaining_args, session=session, **kwargs)
            else:
                return func(user_id, *remaining_args, session=session, **kwargs)
    return wrapper
```

**Example task usage:**

```python
# worker/tasks/utils.py
"""Shared utilities for worker tasks that need article content."""
import os
import logging
from minio import Minio

logger = logging.getLogger(__name__)

_minio = Minio(
    os.environ.get("MINIO_URL", "http://minio:9000").replace("http://", "").replace("https://", ""),
    access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
    secure=os.environ.get("MINIO_URL", "").startswith("https"),
)
_bucket = os.environ.get("MINIO_BUCKET", "ttwatch-content")


def fetch_article_text(raw_storage_key: str) -> str:
    """Fetch raw article text from MinIO by its storage key.
    
    Returns the full text string. Raises if key doesn't exist.
    Used by summarize, embed, extract_entities, classify_sentiment tasks.
    """
    response = _minio.get_object(_bucket, raw_storage_key)
    try:
        return response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()
```

```python
# worker/tasks/summarize.py
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from worker.tasks.utils import fetch_article_text
from app.models import Article

_llm = SyncLLMClient()

@app.task(name="summarize_article", max_retries=3, default_retry_delay=30)
@with_rls_context
def summarize_article(user_id: str, article_id: str, session=None):
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()
    
    # Fetch raw text from MinIO (Article model has no raw_text column;
    # raw content is stored in MinIO via raw_storage_key)
    raw_text = fetch_article_text(article.raw_storage_key)
    
    summary = _llm.generate([
        {"role": "system", "content": "Summarize this article in 2 sentences."},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ])
    article.summary = summary
```

```python
# worker/tasks/embed.py
"""Embed article text and store vector in Qdrant with user isolation payload."""
import os
import logging
from sqlalchemy import select
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncEmbeddingClient
from worker.tasks.utils import fetch_article_text
from app.models import Article

logger = logging.getLogger(__name__)

_embedder = SyncEmbeddingClient()
_qdrant = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))


@app.task(name="embed_article", max_retries=3, default_retry_delay=30)
@with_rls_context
def embed_article(user_id: str, article_id: str, session=None):
    """Generate embedding for an article and upsert into Qdrant.
    
    CRITICAL CONTRACT: Uses str(article.id) as the Qdrant point ID.
    recluster_topic (§7.5) depends on this: qdrant_point.id == article.id.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    # Fetch raw text from MinIO
    raw_text = fetch_article_text(article.raw_storage_key)

    # Create embedding text: title + first 1500 chars of body.
    # BGE-M3 supports up to 8192 tokens (~32K chars). Using 1500 chars
    # balances embedding quality with batch throughput. 512 chars was too
    # aggressive and missed important context in longer articles.
    embed_text = f"{article.title}\n\n{raw_text[:1500]}"
    embeddings = _embedder.embed([embed_text])

    if not embeddings:
        logger.error(f"Empty embedding for article {article_id}")
        return

    # Upsert to Qdrant — point ID MUST be the article UUID (clustering depends on this)
    point = PointStruct(
        id=str(article.id),  # Critical: must match PostgreSQL article.id
        vector=embeddings[0],
        payload={
            "user_id": user_id,
            "topic_id": str(article.topic_id),
            "title": article.title,
            "source": article.source_name or "",
            "ingested_at": article.ingested_at.isoformat() if article.ingested_at else "",
        },
    )
    _qdrant.upsert(collection_name="articles", points=[point])

    # --- Layer 3 semantic dedup: check for near-duplicate articles ---
    # After upserting, search for existing articles with cosine similarity > 0.92.
    # This catches paraphrased or syndicated content that passes URL and hash dedup.
    # Only checks within the same user + topic scope.
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    try:
        similar = _qdrant.search(
            collection_name="articles",
            query_vector=embeddings[0],
            query_filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="topic_id", match=MatchValue(value=str(article.topic_id))),
            ]),
            score_threshold=0.92,
            limit=3,
        )
        # Check if any high-similarity match is from a DIFFERENT, older article
        for hit in similar:
            if str(hit.id) != str(article.id):
                # Found a near-duplicate — mark this article
                article.is_duplicate = True
                article.duplicate_of = hit.id
                logger.info(f"Semantic dedup: article {article_id} is near-duplicate of {hit.id} (score={hit.score:.3f})")
                break
    except Exception as e:
        logger.warning(f"Semantic dedup check failed for {article_id}: {e}")

    # Store embedding reference on article
    article.embedding_id = str(article.id)
    logger.info(f"Embedded article {article_id}: {article.title[:60]}")
```

```python
# worker/tasks/entities.py
"""Extract named entities from article text using LLM."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from worker.tasks.utils import fetch_article_text
from app.models import Article, Entity, EntityArticleMap

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="extract_entities", max_retries=3, default_retry_delay=30)
@with_rls_context
def extract_entities(user_id: str, article_id: str, session=None):
    """Extract named entities from an article and persist to database.
    
    Creates Entity records if they don't exist, and creates
    EntityArticleMap join records linking entities to the article.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    result = _llm.generate_json([
        {"role": "system", "content": (
            "Extract named entities from the article. Return JSON: "
            '{"entities": [{"name": "...", "type": "person|org|product|location|event|technology"}]}. '
            "Only include clearly identified entities. Max 15."
        )},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ])

    entities = result.get("entities", [])
    for ent in entities:
        name = ent.get("name", "").strip()[:500]
        etype = ent.get("type", "unknown").strip().lower()[:50]
        if not name:
            continue

        # Upsert entity (unique per user + name + type + topic)
        existing = session.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.name == name,
                Entity.type == etype,
                Entity.topic_id == str(article.topic_id),
            )
        ).scalar_one_or_none()

        if existing:
            entity_id = existing.id
        else:
            entity = Entity(
                user_id=user_id,
                topic_id=str(article.topic_id),
                name=name,
                type=etype,
            )
            session.add(entity)
            session.flush()
            entity_id = entity.id

        # Link entity to article (skip if already linked)
        exists_link = session.execute(
            select(EntityArticleMap).where(
                EntityArticleMap.entity_id == entity_id,
                EntityArticleMap.article_id == article_id,
            )
        ).scalar_one_or_none()
        if not exists_link:
            session.add(EntityArticleMap(
                entity_id=entity_id,
                article_id=article_id,
                user_id=user_id,
            ))

        # Fan-out: resolve entity to ticker symbol for investment pipeline.
        # Only dispatches for newly created entities (not previously existing ones)
        # to avoid redundant LLM calls on every article.
        if not existing:
            from worker.tasks.resolve_ticker import resolve_entity_ticker
            resolve_entity_ticker.delay(user_id, str(entity_id), str(article.topic_id))

    logger.info(f"Extracted {len(entities)} entities from article {article_id}")
```

```python
# worker/tasks/sentiment.py
"""Classify article sentiment using LLM."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from worker.tasks.utils import fetch_article_text
from app.models import Article

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="classify_sentiment", max_retries=3, default_retry_delay=30)
@with_rls_context
def classify_sentiment(user_id: str, article_id: str, session=None):
    """Classify sentiment of an article on a -1.0 to 1.0 scale.
    
    -1.0 = strongly negative, 0.0 = neutral, 1.0 = strongly positive.
    Stores the result on the article's sentiment_score column.
    """
    article = session.execute(
        select(Article).where(Article.id == article_id)
    ).scalar_one()

    raw_text = fetch_article_text(article.raw_storage_key)

    result = _llm.generate_json([
        {"role": "system", "content": (
            "Classify the sentiment of this article on a scale from -1.0 to 1.0. "
            "-1.0 = strongly negative, 0.0 = neutral, 1.0 = strongly positive. "
            'Return JSON: {"score": 0.0, "rationale": "brief explanation"}'
        )},
        {"role": "user", "content": f"Title: {article.title}\nText: {raw_text[:2000]}"},
    ])

    score = result.get("score", 0.0)
    # Clamp to valid range
    score = max(-1.0, min(1.0, float(score)))
    article.sentiment_score = score

    logger.info(f"Sentiment for article {article_id}: {score:.2f}")
```

**Periodic dispatch tasks:**

```python
# worker/tasks/periodic.py
from sqlalchemy import select
from worker.celeryconfig import app
from worker.db import db_session
from app.models import User, Topic

@app.task(name="schedule_reclustering")
def schedule_reclustering():
    """Beat task: enumerate active users and dispatch per-user recluster jobs."""
    with db_session() as session:
        users = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    
    from worker.tasks.cluster import recluster_topic
    for user_id, topic_id in users:
        recluster_topic.delay(str(user_id), str(topic_id))


@app.task(name="schedule_trend_updates")
def schedule_trend_updates():
    """Beat task: dispatch trend update for each active user/topic pair."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    for user_id, topic_id in pairs:
        app.send_task("update_trends", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_briefings")
def schedule_briefings():
    """Beat task: dispatch briefing generation for each active user/topic pair."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    for user_id, topic_id in pairs:
        app.send_task("generate_briefing", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_coverage_gaps")
def schedule_coverage_gaps():
    """Beat task: dispatch coverage gap detection for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    for user_id, topic_id in pairs:
        app.send_task("detect_coverage_gaps", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_sentiment_history")
def schedule_sentiment_history():
    """Beat task: dispatch sentiment history computation for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    for user_id, topic_id in pairs:
        app.send_task("compute_sentiment_history", args=[str(user_id), str(topic_id)])


@app.task(name="refresh_market_data")
def refresh_market_data():
    """Beat task: refresh market data for all watched symbols across all users.
    
    Discovers symbols from BOTH watchlist_items (user-explicit) and
    asset_mappings (auto-resolved from entities). This ensures market data
    is available for generate_investment_analyses and detect_correlation_signals,
    not just for user-managed watchlists.
    """
    with db_session() as session:
        from app.models import WatchlistItem, AssetMapping
        watchlist_symbols = set(session.execute(
            select(WatchlistItem.symbol).distinct()
        ).scalars().all())
        mapping_symbols = set(session.execute(
            select(AssetMapping.resolved_symbol).where(
                AssetMapping.resolved_symbol.isnot(None)
            ).distinct()
        ).scalars().all())
        all_symbols = watchlist_symbols | mapping_symbols
    for symbol in all_symbols:
        app.send_task("fetch_market_data", args=[symbol])


@app.task(name="schedule_investment_analyses")
def schedule_investment_analyses():
    """Beat task (daily): dispatch investment analysis for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    for user_id, topic_id in pairs:
        app.send_task("generate_investment_analyses", args=[str(user_id), str(topic_id)])


@app.task(name="schedule_correlation_signals")
def schedule_correlation_signals():
    """Beat task (every 4h): dispatch correlation signal detection for each active user/topic."""
    with db_session() as session:
        pairs = session.execute(
            select(User.id, Topic.id)
            .join(Topic, Topic.user_id == User.id)
            .where(User.is_active == True)
        ).all()
    for user_id, topic_id in pairs:
        app.send_task("detect_correlation_signals", args=[str(user_id), str(topic_id)])
```

### 7.2.2 Ingestion Task (Trafilatura)

```python
# worker/tasks/ingest.py
"""Article ingestion pipeline: search → extract → dedup → store → fan-out."""
import hashlib
import logging
import os
from io import BytesIO

import redis as redis_lib
import trafilatura
from minio import Minio
from sqlalchemy import select

from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article

logger = logging.getLogger(__name__)

_minio = Minio(
    os.environ.get("MINIO_URL", "http://minio:9000").replace("http://", "").replace("https://", ""),
    access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
    secure=os.environ.get("MINIO_URL", "").startswith("https"),
)
_bucket = os.environ.get("MINIO_BUCKET", "ttwatch-content")

# Module-level singleton — NOT created per-invocation (avoids connection exhaustion
# under gevent concurrency=32; creating per-call would exhaust Redis maxclients)
_dedup_redis = redis_lib.from_url(
    os.environ.get("REDIS_DEDUP_URL", "redis://redis:6379/2")
)


@app.task(name="ingest_article", bind=True, max_retries=2)
@with_rls_context
def ingest_article(self, user_id: str, topic_id: str, url: str,
                   title: str = "", source_name: str = "", source_url: str = "",
                   session=None):
    """Download, extract, dedup, and store a single article.
    
    On success, fans out to summarize, embed, extract_entities, classify_sentiment.
    
    NOTE: Uses bind=True for self.retry() support. The with_rls_context decorator
    detects the Task instance and shifts arguments correctly.
    """

    # --- Layer 1: URL dedup ---
    dedup_key = f"ttwatch:dedup:urls:{user_id}"
    if _dedup_redis.sismember(dedup_key, url):
        logger.debug(f"URL already ingested for user {user_id}: {url}")
        return {"status": "duplicate", "layer": "url"}

    # --- Fetch and extract ---
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning(f"Failed to fetch: {url}")
            return {"status": "fetch_failed"}

        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            output_format="txt",
        )
        if not extracted or len(extracted.strip()) < 100:
            logger.warning(f"Insufficient content extracted from: {url}")
            return {"status": "extraction_failed"}
    except Exception as e:
        logger.error(f"Extraction error for {url}: {e}")
        raise self.retry(exc=e, countdown=30)

    raw_text = extracted.strip()

    # Extract title and published_at from document metadata.
    # Uses extract_metadata() which returns a structured object with .title and .date,
    # NOT extract(output_format="xmltei") which returns raw XML requiring parsing.
    # Extracting published_at is critical for temporal analysis (trends, briefing
    # windows). Without it, only ingested_at is available, which reflects when
    # TTwatch processed the article, not when it was actually published.
    published_at = None
    try:
        metadata = trafilatura.extract_metadata(downloaded)
        if metadata:
            if not title and metadata.title:
                title = metadata.title[:500]
            if metadata.date:
                from datetime import datetime as _dt
                try:
                    published_at = _dt.fromisoformat(metadata.date)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    if not title:
        title = url.split("/")[-1][:200] or "Untitled"

    # --- Layer 2: Content hash dedup ---
    content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    existing = session.execute(
        select(Article.id).where(
            Article.user_id == user_id,
            Article.topic_id == topic_id,
            Article.content_hash == content_hash,
        )
    ).scalar_one_or_none()

    if existing:
        _dedup_redis.sadd(dedup_key, url)
        logger.debug(f"Content hash duplicate: {url}")
        return {"status": "duplicate", "layer": "content_hash"}

    # --- Store raw content in MinIO ---
    storage_key = f"{user_id}/{topic_id}/{content_hash}.txt"
    raw_bytes = raw_text.encode("utf-8")
    _minio.put_object(
        _bucket, storage_key,
        BytesIO(raw_bytes), len(raw_bytes),
        content_type="text/plain",
    )

    # --- Create article record ---
    article = Article(
        user_id=user_id,
        topic_id=topic_id,
        url=url,
        title=title,
        source_name=source_name,
        source_url=source_url or None,  # Source homepage URL, passed from caller
        published_at=published_at,  # Extracted from document metadata; None if unavailable
        content_hash=content_hash,
        raw_storage_key=storage_key,
    )
    session.add(article)
    session.flush()  # get article.id

    # Mark URL as ingested
    _dedup_redis.sadd(dedup_key, url)

    # --- Fan-out to ALL processing tasks ---
    from worker.tasks.summarize import summarize_article
    from worker.tasks.embed import embed_article
    from worker.tasks.entities import extract_entities
    from worker.tasks.sentiment import classify_sentiment

    article_id = str(article.id)
    summarize_article.delay(user_id, article_id)
    embed_article.delay(user_id, article_id)
    extract_entities.delay(user_id, article_id)
    classify_sentiment.delay(user_id, article_id)

    logger.info(f"Ingested article {article_id}: {title[:80]}")
    return {"status": "ingested", "article_id": article_id}
```

### 7.3 Fair Queue Scheduling & Discovery

```python
# worker/startup.py
from celery.signals import worker_ready

@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    discover_and_register_queues(sender.app)

def discover_and_register_queues(app):
    import redis as redis_lib
    r = redis_lib.from_url(app.conf.broker_url)
    active_queues = set()

    # Celery uses queue names as Redis list keys. User priority queues
    # follow the pattern ttwatch:priority:{user_id}. Also scan for any
    # custom per-user task queues. The ttwatch:scheduled and ttwatch:tasks
    # patterns were removed as no code creates keys matching those patterns.
    for pattern in ["ttwatch:priority:*"]:
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            active_queues.update(k.decode() for k in keys)
            if cursor == 0:
                break

    for queue_name in active_queues:
        app.control.add_consumer(queue_name, reply=True, timeout=5.0)
```

```python
# worker/tasks/queue_discovery.py
from worker.celeryconfig import app
from worker.startup import discover_and_register_queues

@app.task(name="discover_queues")
def discover_queues():
    discover_and_register_queues(app)
```

```python
# In the API — non-blocking queue registration
import asyncio
from concurrent.futures import ThreadPoolExecutor
from worker.celeryconfig import app as celery_app

_executor = ThreadPoolExecutor(max_workers=2)

async def ensure_queue_consumed(user_id: str):
    """Register user's priority queue with workers (non-blocking)."""
    queue_name = f"ttwatch:priority:{user_id}"
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        _executor,
        lambda: celery_app.control.add_consumer(queue_name, reply=False),
    )
```

### 7.4 Deduplication Strategy

Three-layer deduplication, all user-scoped:

1. **URL-level** — Redis set per user in DB 2: `ttwatch:dedup:urls:{user_id}` — instant
2. **Content hash** — SHA-256 in PostgreSQL, filtered by `user_id` — catches syndication
3. **Semantic similarity** — Qdrant cosine > 0.92, filtered by `user_id` payload — catches paraphrasing

### 7.5 Clustering with HDBSCAN (two-phase Qdrant scroll)

```python
# worker/tasks/cluster.py
import os
import logging
import numpy as np
from umap import UMAP
from hdbscan import HDBSCAN
from sqlalchemy import select, delete
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import Cluster, Article

logger = logging.getLogger(__name__)

qdrant_sync = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
_llm = SyncLLMClient()

CLUSTER_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1",
    "#14B8A6", "#E11D48", "#A855F7", "#0EA5E9", "#D946EF",
]

@app.task(name="recluster_topic", queue="ttwatch:compute")
@with_rls_context
def recluster_topic(user_id: str, topic_id: str, session=None):
    """Re-cluster articles for a topic using HDBSCAN."""
    MAX_CLUSTER_ARTICLES = 2000
    scroll_filter = Filter(must=[
        FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        FieldCondition(key="topic_id", match=MatchValue(value=topic_id)),
    ])

    # Phase 1: Scroll payloads only (no vectors) for sorting
    all_points = []
    offset = None
    while True:
        points, next_offset = qdrant_sync.scroll(
            collection_name="articles",
            scroll_filter=scroll_filter,
            offset=offset,
            limit=500,
            with_vectors=False,
            with_payload=True,
        )
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset

    all_points.sort(key=lambda p: p.payload.get("ingested_at", ""), reverse=True)
    selected_ids = [p.id for p in all_points[:MAX_CLUSTER_ARTICLES]]

    if len(selected_ids) < 10:
        logger.info(f"Topic {topic_id}: only {len(selected_ids)} articles, skipping")
        return

    # Phase 2: Fetch vectors only for selected points
    points_with_vectors = qdrant_sync.retrieve(
        collection_name="articles",
        ids=selected_ids,
        with_vectors=True,
        with_payload=True,
    )

    vectors = np.array([p.vector for p in points_with_vectors])
    reduced = UMAP(n_components=20, metric="cosine", random_state=42).fit_transform(vectors)
    labels = HDBSCAN(min_cluster_size=5, min_samples=3).fit_predict(reduced)

    # Clear old clusters for this topic.
    # IMPORTANT: Nullify FK references in sentiment_history and entity_cluster_map
    # BEFORE deleting clusters. Without this, ON DELETE CASCADE would permanently
    # destroy historical sentiment data and entity-cluster mappings every 2 hours.
    old_cluster_ids = [
        row[0] for row in session.execute(
            select(Cluster.id).where(Cluster.topic_id == topic_id)
        ).all()
    ]
    if old_cluster_ids:
        from app.models import SentimentHistory, EntityClusterMap
        session.execute(
            SentimentHistory.__table__.update()
            .where(SentimentHistory.cluster_id.in_(old_cluster_ids))
            .values(cluster_id=None)
        )
        session.execute(
            EntityClusterMap.__table__.delete()
            .where(EntityClusterMap.cluster_id.in_(old_cluster_ids))
        )
        session.execute(delete(Cluster).where(Cluster.topic_id == topic_id))
        session.flush()

    unique_labels = sorted(set(labels) - {-1})
    for i, cluster_label in enumerate(unique_labels):
        cluster_point_indices = [idx for idx, l in enumerate(labels) if l == cluster_label]
        cluster_articles = [points_with_vectors[idx] for idx in cluster_point_indices]

        titles = "\n".join(p.payload.get("title", "Untitled") for p in cluster_articles[:10])
        keyword = _llm.generate([
            {"role": "system", "content": "Given these article titles, generate a concise 2-4 word topic label. Respond with ONLY the label."},
            {"role": "user", "content": titles},
        ]).strip().strip('"').strip("'")

        article_count = len(cluster_articles)
        cluster = Cluster(
            user_id=user_id, topic_id=topic_id, keyword=keyword,
            color=CLUSTER_COLORS[i % len(CLUSTER_COLORS)],
            article_count=article_count, trend_score=article_count,
        )
        session.add(cluster)
        session.flush()

        article_ids = [str(p.id) for p in cluster_articles]
        # CRITICAL CONTRACT: article_ids here are Qdrant point IDs, which
        # MUST equal PostgreSQL article UUIDs. This is enforced by embed_article
        # (worker/tasks/embed.py) which uses str(article.id) as the point ID.
        #
        # NOTE: Some Qdrant points may be orphaned (article deleted from PG
        # but vector remains in Qdrant). The UPDATE below correctly updates
        # only existing articles. After the update, recalculate the actual
        # article count from the database to avoid inflation from orphans.
        result = session.execute(
            Article.__table__.update().where(Article.id.in_(article_ids)).values(cluster_id=cluster.id)
        )
        # Update article_count to reflect actual DB rows, not Qdrant point count
        actual_count = result.rowcount if hasattr(result, 'rowcount') else article_count
        if actual_count != article_count:
            cluster.article_count = actual_count
            cluster.trend_score = actual_count

    noise_ids = [str(points_with_vectors[idx].id) for idx, l in enumerate(labels) if l == -1]
    if noise_ids:
        session.execute(
            Article.__table__.update().where(Article.id.in_(noise_ids)).values(cluster_id=None)
        )

    logger.info(f"Topic {topic_id}: {len(unique_labels)} clusters from {len(selected_ids)} articles ({len(noise_ids)} noise)")
```

### 7.6 Intelligence Aggregate Tasks

These tasks are dispatched by the periodic dispatch tasks in `periodic.py` and perform the actual intelligence analysis work.

```python
# worker/tasks/briefing.py
"""Generate topic briefing from cluster summaries using hierarchical summarization."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import Article, Cluster, Briefing, Entity, EntityArticleMap

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="generate_briefing", max_retries=2, default_retry_delay=60)
@with_rls_context
def generate_briefing(user_id: str, topic_id: str, session=None):
    """Generate an intelligence briefing for a topic using hierarchical summarization.
    
    Tier 3 task (~16K context): Aggregates cluster summaries into a single briefing.
    Uses the hierarchy: articles → article summaries → cluster summaries → briefing.
    This avoids sending raw article text directly to the LLM.
    """
    clusters = session.execute(
        select(Cluster).where(
            Cluster.topic_id == topic_id
        ).order_by(Cluster.article_count.desc())
    ).scalars().all()

    if not clusters:
        logger.info(f"No clusters for topic {topic_id}, skipping briefing")
        return

    # Build cluster summaries from article summaries
    cluster_sections = []
    total_articles = 0
    for cluster in clusters[:12]:  # Cap at 12 clusters to stay in context window
        articles = session.execute(
            select(Article).where(
                Article.cluster_id == cluster.id,
                Article.summary.isnot(None),
                Article.is_duplicate == False,
            ).order_by(Article.ingested_at.desc()).limit(20)
        ).scalars().all()

        if not articles:
            continue

        summaries = "\n".join(
            f"- [{a.source_name or 'Unknown'}] {a.summary}" for a in articles
        )
        cluster_sections.append(
            f"### {cluster.keyword} ({cluster.article_count} articles)\n{summaries}"
        )
        total_articles += len(articles)

    if not cluster_sections:
        logger.info(f"No summarized articles for topic {topic_id}, skipping briefing")
        return

    cluster_text = "\n\n".join(cluster_sections)

    # Detect new entities (first seen in last 24h)
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    new_entities = session.execute(
        select(Entity.name, Entity.type).where(
            Entity.topic_id == topic_id,
            Entity.first_seen >= cutoff,
        ).limit(20)
    ).all()
    new_entity_list = [{"name": e[0], "type": e[1]} for e in new_entities]

    result = _llm.generate_json([
        {"role": "system", "content": (
            "You are an intelligence analyst. Generate a briefing from these cluster summaries. "
            "Return JSON: {\"summary\": \"2-3 paragraph executive summary\", "
            "\"highlights\": [\"key development 1\", ...], "
            "\"watch_items\": [\"thing to monitor\", ...]}"
        )},
        {"role": "user", "content": f"Topic clusters:\n\n{cluster_text}"},
    ], max_tokens=2048)

    briefing = Briefing(
        user_id=user_id,
        topic_id=topic_id,
        summary=result.get("summary", ""),
        highlights=result.get("highlights", []),
        new_entities=new_entity_list,
        watch_items=result.get("watch_items", []),
        model_used=_llm.model,
    )
    session.add(briefing)

    logger.info(f"Generated briefing for topic {topic_id}: {total_articles} articles across {len(cluster_sections)} clusters")
```

```python
# worker/tasks/trends.py
"""Update trend scores and velocity for clusters based on recent article activity."""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article, Cluster

logger = logging.getLogger(__name__)


@app.task(name="update_trends", queue="ttwatch:compute")
@with_rls_context
def update_trends(user_id: str, topic_id: str, session=None):
    """Compute trend scores and velocity labels for each cluster.
    
    trend_score = weighted sum of recent articles (newer = higher weight).
    velocity = "surging" | "rising" | "steady" | "declining" based on
               comparison between last-24h and previous-24h article counts.
    """
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    prev_24h = now - timedelta(hours=48)

    clusters = session.execute(
        select(Cluster).where(Cluster.topic_id == topic_id)
    ).scalars().all()

    for cluster in clusters:
        # Count articles in two 24-hour windows
        recent_count = session.execute(
            select(func.count(Article.id)).where(
                Article.cluster_id == cluster.id,
                Article.ingested_at >= last_24h,
                Article.is_duplicate == False,
            )
        ).scalar() or 0

        previous_count = session.execute(
            select(func.count(Article.id)).where(
                Article.cluster_id == cluster.id,
                Article.ingested_at >= prev_24h,
                Article.ingested_at < last_24h,
                Article.is_duplicate == False,
            )
        ).scalar() or 0

        # Compute velocity label
        if previous_count == 0:
            velocity = "surging" if recent_count > 3 else "rising" if recent_count > 0 else "steady"
        else:
            ratio = recent_count / previous_count
            if ratio >= 2.0:
                velocity = "surging"
            elif ratio >= 1.2:
                velocity = "rising"
            elif ratio >= 0.8:
                velocity = "steady"
            else:
                velocity = "declining"

        # Weighted trend score: 24h articles × 3 + 48h articles × 1
        cluster.trend_score = (recent_count * 3) + (previous_count * 1)
        cluster.velocity = velocity

    logger.info(f"Updated trends for {len(clusters)} clusters in topic {topic_id}")
```

```python
# worker/tasks/sentiment_agg.py
"""Aggregate article sentiment into periodic sentiment_history snapshots."""
import logging
from datetime import date
from sqlalchemy import select, func
from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import Article, Cluster, SentimentHistory

logger = logging.getLogger(__name__)


@app.task(name="compute_sentiment_history")
@with_rls_context
def compute_sentiment_history(user_id: str, topic_id: str, session=None):
    """Aggregate per-cluster sentiment into daily sentiment_history snapshots.
    
    For each cluster, computes the average sentiment_score of articles
    ingested today and upserts a sentiment_history record.
    """
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()

    clusters = session.execute(
        select(Cluster.id, Cluster.keyword).where(Cluster.topic_id == topic_id)
    ).all()

    created_count = 0
    for cluster_id, cluster_keyword in clusters:
        agg = session.execute(
            select(
                func.avg(Article.sentiment_score),
                func.count(Article.id),
            ).where(
                Article.cluster_id == cluster_id,
                Article.sentiment_score.isnot(None),
                func.date(Article.ingested_at) == today,
                Article.is_duplicate == False,
            )
        ).one()

        avg_sentiment, article_count = agg
        if article_count == 0:
            continue

        # Upsert: check if record exists for this cluster+date
        existing = session.execute(
            select(SentimentHistory).where(
                SentimentHistory.user_id == user_id,
                SentimentHistory.cluster_id == cluster_id,
                SentimentHistory.period_start == today,
            )
        ).scalar_one_or_none()

        if existing:
            existing.avg_sentiment = float(avg_sentiment)
            existing.article_count = article_count
            existing.cluster_keyword = cluster_keyword
        else:
            session.add(SentimentHistory(
                user_id=user_id,
                topic_id=topic_id,
                cluster_id=cluster_id,
                cluster_keyword=cluster_keyword,
                period_start=today,
                avg_sentiment=float(avg_sentiment),
                article_count=article_count,
            ))
            created_count += 1

    logger.info(f"Sentiment history: {created_count} new snapshots for topic {topic_id}")
```

```python
# worker/tasks/coverage_gaps.py
"""Detect coverage gaps by analyzing what the current clusters DON'T cover."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import Cluster, Briefing

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="detect_coverage_gaps", max_retries=2, default_retry_delay=60)
@with_rls_context
def detect_coverage_gaps(user_id: str, topic_id: str, session=None):
    """Use LLM to identify topics NOT covered by existing clusters.
    
    Compares cluster keywords against what a comprehensive analysis of the
    topic domain should include. Stores gaps in the latest briefing record.
    """
    clusters = session.execute(
        select(Cluster.keyword, Cluster.article_count).where(
            Cluster.topic_id == topic_id
        ).order_by(Cluster.article_count.desc())
    ).all()

    if not clusters:
        return

    covered_topics = "\n".join(f"- {kw} ({count} articles)" for kw, count in clusters)

    # Get the topic name for context
    from app.models import Topic
    topic = session.execute(
        select(Topic.name).where(Topic.id == topic_id)
    ).scalar_one_or_none()
    topic_name = topic or "this topic"

    result = _llm.generate_json([
        {"role": "system", "content": (
            "You are an intelligence analyst. Given the currently covered subtopics, "
            "identify 3-5 important areas that are NOT being covered but SHOULD be "
            "for comprehensive monitoring. Return JSON: "
            "{\"gaps\": [{\"area\": \"...\", \"reason\": \"why this matters\"}]}"
        )},
        {"role": "user", "content": (
            f"Topic: {topic_name}\n\n"
            f"Currently covered subtopics:\n{covered_topics}\n\n"
            "What important areas are missing from this coverage?"
        )},
    ])

    gaps = result.get("gaps", [])

    # Update the latest briefing with coverage gaps
    latest_briefing = session.execute(
        select(Briefing).where(
            Briefing.topic_id == topic_id
        ).order_by(Briefing.generated_at.desc()).limit(1)
    ).scalar_one_or_none()

    if latest_briefing:
        latest_briefing.coverage_gaps = gaps

    logger.info(f"Detected {len(gaps)} coverage gaps for topic {topic_id}")
```

```python
# worker/tasks/investment_analysis.py
"""Generate investment analyses for entities with resolved ticker symbols."""
import logging
from sqlalchemy import select, func
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import (
    AssetMapping, Article, Entity, EntityArticleMap,
    MarketDataCache, InvestmentAnalysis,
)

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="generate_investment_analyses", max_retries=2, default_retry_delay=60)
@with_rls_context
def generate_investment_analyses(user_id: str, topic_id: str, session=None):
    """Generate investment analyses for entities with resolved tickers.
    
    For each asset mapping in the topic, gathers recent article summaries
    mentioning that entity, fetches latest market data, and generates
    an LLM-powered analysis with sentiment and key signals.
    """
    mappings = session.execute(
        select(AssetMapping).where(
            AssetMapping.topic_id == topic_id,
            AssetMapping.resolved_symbol.isnot(None),
        )
    ).scalars().all()

    for mapping in mappings:
        # Get recent articles mentioning this entity
        article_summaries = session.execute(
            select(Article.title, Article.summary, Article.sentiment_score).join(
                EntityArticleMap, EntityArticleMap.article_id == Article.id
            ).where(
                EntityArticleMap.entity_id == mapping.entity_id,
                Article.summary.isnot(None),
                Article.is_duplicate == False,
            ).order_by(Article.ingested_at.desc()).limit(15)
        ).all()

        if not article_summaries:
            continue

        # Get latest market data
        market_data = session.execute(
            select(MarketDataCache).where(
                MarketDataCache.symbol == mapping.resolved_symbol
            ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
        ).scalar_one_or_none()

        news_context = "\n".join(
            f"- {title}: {summary} (sentiment: {score:.2f})"
            for title, summary, score in article_summaries
            if summary and score is not None
        )

        market_context = ""
        if market_data:
            market_context = (
                f"Price: ${market_data.price}, "
                f"Change: {market_data.price_change_pct}%, "
                f"Market Cap: {market_data.market_cap}"
            )

        result = _llm.generate_json([
            {"role": "system", "content": (
                "You are a financial analyst. Analyze the news sentiment and market data "
                "for this asset. Return JSON: {\"analysis\": \"2-3 paragraph analysis\", "
                "\"recommendation\": \"bullish|bearish|neutral\", \"confidence\": 0.0-1.0, "
                "\"key_signals\": [\"signal 1\", ...], \"risk_factors\": [\"risk 1\", ...]}"
            )},
            {"role": "user", "content": (
                f"Asset: {mapping.entity_name} ({mapping.resolved_symbol})\n"
                f"Market Data: {market_context}\n\n"
                f"Recent News:\n{news_context}"
            )},
        ])

        # Calculate aggregate sentiment from articles
        sentiments = [s for _, _, s in article_summaries if s is not None]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        analysis = InvestmentAnalysis(
            user_id=user_id,
            topic_id=topic_id,
            analysis_scope="asset",
            scope_ref_id=mapping.id,
            symbol=mapping.resolved_symbol,
            analysis_text=result.get("analysis", ""),
            recommendation=result.get("recommendation", "neutral"),
            confidence=max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
            key_signals=result.get("key_signals", []),
            risk_factors=result.get("risk_factors", []),
            articles_considered=len(article_summaries),
            market_data_cache_id=market_data.id if market_data else None,
            sentiment_score=avg_sentiment,
            model_used=_llm.model,
        )
        session.add(analysis)

    logger.info(f"Generated investment analyses for {len(mappings)} assets in topic {topic_id}")
```

```python
# worker/tasks/resolve_ticker.py
"""Resolve named entities to ticker symbols using LLM + ticker_reference lookup."""
import logging
from sqlalchemy import select
from worker.celeryconfig import app
from worker.rls import with_rls_context
from worker.llm_sync import SyncLLMClient
from app.models import Entity, TickerReference, AssetMapping

logger = logging.getLogger(__name__)

_llm = SyncLLMClient()


@app.task(name="resolve_entity_ticker", max_retries=2, default_retry_delay=30)
@with_rls_context
def resolve_entity_ticker(user_id: str, entity_id: str, topic_id: str, session=None):
    """Resolve a named entity (e.g., 'Tesla', 'Bitcoin') to a ticker symbol.
    
    Uses a two-step approach:
    1. Check ticker_reference for direct name match (fast, no LLM).
    2. If no match, use LLM to infer the most likely ticker symbol.
    
    Creates an AssetMapping record linking the entity to the resolved symbol.
    """
    entity = session.execute(
        select(Entity).where(Entity.id == entity_id)
    ).scalar_one_or_none()
    if not entity:
        return

    # Skip non-resolvable entity types
    if entity.type not in ("org", "product", "technology"):
        return

    # Check if already resolved
    existing = session.execute(
        select(AssetMapping).where(
            AssetMapping.user_id == user_id,
            AssetMapping.entity_id == entity_id,
        )
    ).scalar_one_or_none()
    if existing:
        return

    # Step 1: Direct lookup in ticker_reference
    ref = session.execute(
        select(TickerReference).where(
            TickerReference.name.ilike(f"%{entity.name}%"),
            TickerReference.is_active == True,
        ).limit(1)
    ).scalar_one_or_none()

    if ref:
        session.add(AssetMapping(
            user_id=user_id,
            topic_id=topic_id,
            entity_id=entity_id,
            ticker_ref_id=ref.id,
            entity_name=entity.name,
            resolved_symbol=ref.symbol,
            resolution_method="reference_lookup",
            confidence=0.9,
        ))
        logger.info(f"Resolved '{entity.name}' → {ref.symbol} via reference lookup")
        return

    # Step 2: LLM resolution
    result = _llm.generate_json([
        {"role": "system", "content": (
            "Given the entity name, determine if it corresponds to a publicly "
            "traded stock, ETF, or cryptocurrency. Return JSON: "
            '{"symbol": "TICKER", "asset_type": "equity|etf|crypto", "confidence": 0.0-1.0}. '
            "If you cannot determine a ticker, return {\"symbol\": null, \"confidence\": 0.0}."
        )},
        {"role": "user", "content": f"Entity: {entity.name} (type: {entity.type})"},
    ])

    symbol = result.get("symbol")
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))

    if symbol and confidence >= 0.6:
        session.add(AssetMapping(
            user_id=user_id,
            topic_id=topic_id,
            entity_id=entity_id,
            entity_name=entity.name,
            resolved_symbol=symbol.upper(),
            resolution_method="llm_inference",
            confidence=confidence,
        ))
        logger.info(f"Resolved '{entity.name}' → {symbol} via LLM (confidence={confidence:.2f})")
```

```python
# worker/tasks/correlation_signals.py
"""Detect correlation signals between news sentiment and price movements."""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from worker.celeryconfig import app
from worker.rls import with_rls_context
from app.models import (
    Article, AssetMapping, MarketDataCache,
    CorrelationSignal, EntityArticleMap,
)

logger = logging.getLogger(__name__)


@app.task(name="detect_correlation_signals", max_retries=2, default_retry_delay=60)
@with_rls_context
def detect_correlation_signals(user_id: str, topic_id: str, session=None):
    """Detect correlations between news sentiment shifts and price movements.
    
    For each resolved asset mapping, compares recent sentiment trends with
    recent price changes to identify potential leading/lagging indicators.
    """
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(hours=48)

    mappings = session.execute(
        select(AssetMapping).where(
            AssetMapping.topic_id == topic_id,
            AssetMapping.resolved_symbol.isnot(None),
        )
    ).scalars().all()

    for mapping in mappings:
        # Calculate sentiment trend over last 48h
        sentiment_agg = session.execute(
            select(func.avg(Article.sentiment_score)).join(
                EntityArticleMap, EntityArticleMap.article_id == Article.id
            ).where(
                EntityArticleMap.entity_id == mapping.entity_id,
                Article.sentiment_score.isnot(None),
                Article.ingested_at >= lookback,
                Article.is_duplicate == False,
            )
        ).scalar()

        if sentiment_agg is None:
            continue

        # Get latest market data
        market = session.execute(
            select(MarketDataCache).where(
                MarketDataCache.symbol == mapping.resolved_symbol,
            ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
        ).scalar_one_or_none()

        if not market or market.price_change_pct is None:
            continue

        price_change = float(market.price_change_pct)
        avg_sentiment = float(sentiment_agg)

        # Detect divergence signals
        signal_type = None
        signal_strength = 0.0

        if avg_sentiment > 0.3 and price_change < -2.0:
            signal_type = "sentiment_price_divergence_bullish"
            signal_strength = min(1.0, abs(avg_sentiment - price_change / 100) / 0.5)
        elif avg_sentiment < -0.3 and price_change > 2.0:
            signal_type = "sentiment_price_divergence_bearish"
            signal_strength = min(1.0, abs(avg_sentiment - price_change / 100) / 0.5)
        elif avg_sentiment > 0.5 and price_change > 3.0:
            signal_type = "momentum_confirmation_bullish"
            signal_strength = min(1.0, (avg_sentiment + price_change / 100) / 1.0)
        elif avg_sentiment < -0.5 and price_change < -3.0:
            signal_type = "momentum_confirmation_bearish"
            signal_strength = min(1.0, abs(avg_sentiment + price_change / 100) / 1.0)

        if signal_type and signal_strength >= 0.3:
            session.add(CorrelationSignal(
                user_id=user_id,
                topic_id=topic_id,
                symbol=mapping.resolved_symbol,
                signal_type=signal_type,
                signal_strength=signal_strength,
                description=(
                    f"Sentiment={avg_sentiment:.2f}, "
                    f"Price change={price_change:.1f}%"
                ),
            ))

    logger.info(f"Correlation signal scan complete for topic {topic_id}")
```

```python
# worker/tasks/price_alerts.py
"""Check price alerts against latest market data and trigger notifications."""
import logging
import json
import redis as redis_lib
import os
from sqlalchemy import select
from worker.celeryconfig import app
from worker.db import db_session
from app.models import PriceAlert, MarketDataCache, User
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Redis pub/sub for real-time WebSocket notifications.
# Workers are synchronous and cannot access the API's in-process ws_manager.
# Instead, triggered alerts are published to a Redis channel that the API's
# WebSocket background listener subscribes to (see main.py ws_alert_listener).
_alert_redis = redis_lib.from_url(
    os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/3")
)


@app.task(name="check_price_alerts")
def check_price_alerts():
    """Check all active price alerts against latest market data.
    
    For each active alert, compares the latest cached price against the
    alert threshold. Triggered alerts are marked with triggered_at and
    deactivated. Publishes triggered alerts to Redis pub/sub channel
    'ttwatch:alerts:triggered' for real-time WebSocket delivery to users.
    """
    with db_session() as session:
        active_alerts = session.execute(
            select(PriceAlert).where(PriceAlert.is_active == True)
        ).scalars().all()

        triggered_count = 0
        for alert in active_alerts:
            # Get latest price for this symbol
            market = session.execute(
                select(MarketDataCache).where(
                    MarketDataCache.symbol == alert.symbol,
                ).order_by(MarketDataCache.fetched_at.desc()).limit(1)
            ).scalar_one_or_none()

            if not market or market.price is None:
                continue

            price = float(market.price)
            threshold = float(alert.threshold)
            triggered = False

            if alert.condition == "above" and price >= threshold:
                triggered = True
            elif alert.condition == "below" and price <= threshold:
                triggered = True
            elif alert.condition == "crosses_above":
                # Only triggers if the last known price was BELOW threshold
                # and current price is at or above. On first check after alert
                # creation (last_known_price is NULL), initialize it from
                # current price without triggering — this establishes the
                # baseline for subsequent crossing detection.
                if alert.last_known_price is not None:
                    was_below = float(alert.last_known_price) < threshold
                    if was_below and price >= threshold:
                        triggered = True
                # else: first check — will be initialized below
            elif alert.condition == "crosses_below":
                # Only triggers if the last known price was ABOVE threshold
                # and current price is at or below. Same first-check logic.
                if alert.last_known_price is not None:
                    was_above = float(alert.last_known_price) > threshold
                    if was_above and price <= threshold:
                        triggered = True

            # Always update last_known_price for crosses conditions.
            # This handles both normal updates AND first-check initialization
            # (when last_known_price was NULL from alert creation).
            if alert.condition in ("crosses_above", "crosses_below"):
                alert.last_known_price = price

            if triggered:
                alert.is_active = False
                alert.triggered_at = datetime.now(timezone.utc)
                triggered_count += 1
                logger.info(
                    f"Price alert triggered: {alert.symbol} {alert.condition} "
                    f"${threshold} (current: ${price})"
                )
                # Publish to Redis for real-time WebSocket delivery.
                # The API's ws_alert_listener coroutine subscribes to this
                # channel and forwards to the user's WebSocket connections.
                try:
                    _alert_redis.publish("ttwatch:alerts:triggered", json.dumps({
                        "user_id": str(alert.user_id),
                        "type": "price_alert_triggered",
                        "symbol": alert.symbol,
                        "condition": alert.condition,
                        "threshold": float(threshold),
                        "price": price,
                        "alert_id": str(alert.id),
                    }))
                except Exception as e:
                    logger.warning(f"Failed to publish alert notification: {e}")

        logger.info(f"Price alert check: {triggered_count}/{len(active_alerts)} triggered")
```
---

## 8. Database Schema

### PostgreSQL — User & Auth

```sql
-- ============================================================
-- USER & AUTH
-- ============================================================

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,          -- argon2id
    is_active BOOLEAN DEFAULT true,
    is_admin BOOLEAN DEFAULT false,

    max_topics INT DEFAULT 10,
    max_articles_per_topic INT DEFAULT 5000,
    max_api_keys INT DEFAULT 5,

    created_at TIMESTAMPTZ DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_prefix TEXT NOT NULL,            -- "tw_live_u7x2_" (first 14 chars for lookup)
    key_hash TEXT NOT NULL,              -- SHA-256 of full key
    label TEXT DEFAULT 'default',
    scopes JSONB DEFAULT '["read", "write", "search"]',
    rate_limit_per_minute INT DEFAULT 60,
    is_active BOOLEAN DEFAULT true,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ
);

CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    device_info TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix) WHERE is_active = true;
CREATE INDEX idx_api_keys_user ON api_keys(user_id);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash);
```

### PostgreSQL — Intelligence Tables (all user-scoped)

```sql
-- ============================================================
-- INTELLIGENCE TABLES (all user-scoped)
-- ============================================================

CREATE TABLE topics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    icon TEXT,
    config JSONB DEFAULT '{}',
    refresh_interval_minutes INT DEFAULT 120,
    last_refreshed_at TIMESTAMPTZ,
    next_refresh_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, name)
);

CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    source_type TEXT DEFAULT 'rss',
    enabled BOOLEAN DEFAULT true,
    is_builtin BOOLEAN DEFAULT false,
    config JSONB DEFAULT '{}',
    UNIQUE(user_id, topic_id, url)
);

CREATE TABLE clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    color TEXT,
    article_count INT DEFAULT 0,
    trend_score FLOAT DEFAULT 0,
    velocity TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    source_name TEXT,
    source_url TEXT,
    published_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    content_hash TEXT,
    raw_storage_key TEXT,
    summary TEXT,
    sentiment_score FLOAT,
    relevance_score FLOAT,
    key_quotes JSONB DEFAULT '[]',
    cluster_id UUID REFERENCES clusters(id) ON DELETE SET NULL,
    embedding_id TEXT,
    is_duplicate BOOLEAN DEFAULT false,
    duplicate_of UUID REFERENCES articles(id),
    UNIQUE(user_id, topic_id, url)
);

CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    first_seen TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, name, type, topic_id)
);

-- Join tables include user_id for RLS enforcement.
-- This prevents cross-user linking (e.g., User A's entity mapped to User B's article).
CREATE TABLE entity_article_map (
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    article_id UUID REFERENCES articles(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (entity_id, article_id)
);

CREATE TABLE entity_cluster_map (
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    cluster_id UUID REFERENCES clusters(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (entity_id, cluster_id)
);

CREATE TABLE sentiment_history (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    cluster_id UUID REFERENCES clusters(id) ON DELETE SET NULL,
    cluster_keyword TEXT,              -- snapshot of cluster keyword at time of aggregation;
                                       -- preserved when cluster_id is nullified during recluster
    period_start DATE NOT NULL,
    avg_sentiment FLOAT,
    article_count INT,
    UNIQUE(user_id, cluster_id, period_start)
);

CREATE TABLE saved_queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    schedule TEXT DEFAULT 'on_refresh',
    last_run TIMESTAMPTZ,
    last_result_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE briefings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    generated_at TIMESTAMPTZ DEFAULT now(),
    summary TEXT,
    highlights JSONB DEFAULT '[]',
    new_entities JSONB DEFAULT '[]',
    watch_items JSONB DEFAULT '[]',
    coverage_gaps JSONB DEFAULT '[]',
    input_tokens INT,
    output_tokens INT,
    model_used TEXT
);
```

### PostgreSQL — Investment Tables

```sql
-- ============================================================
-- SHARED REFERENCE TABLES (no user_id, no RLS)
-- ============================================================

CREATE TABLE ticker_reference (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    exchange TEXT,
    asset_type TEXT NOT NULL,
    sector TEXT,
    industry TEXT,
    market_cap_tier TEXT,
    is_active BOOLEAN DEFAULT true,
    metadata JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(symbol, exchange)
);

CREATE TABLE theme_etf_map (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    theme TEXT NOT NULL,
    etf_symbol TEXT NOT NULL,
    relevance_score FLOAT DEFAULT 1.0,
    UNIQUE(theme, etf_symbol)
);

-- Shared market data cache (one row per symbol per fetch, NOT per user)
CREATE TABLE market_data_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    price NUMERIC,
    price_change_pct NUMERIC,
    volume BIGINT,
    market_cap NUMERIC,
    pe_ratio NUMERIC,
    eps NUMERIC,
    dividend_yield NUMERIC,
    beta NUMERIC,
    fifty_two_week_high NUMERIC,
    fifty_two_week_low NUMERIC,
    data_source TEXT,
    is_stale BOOLEAN DEFAULT false,
    fetched_at TIMESTAMPTZ DEFAULT now()
);

-- Deduplicate market data fetches: one snapshot per symbol per hour
CREATE UNIQUE INDEX idx_market_data_cache_dedup
    ON market_data_cache (symbol, date_trunc('hour', fetched_at));

-- Historical OHLCV (shared cache)
CREATE TABLE price_history (
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    adj_close NUMERIC,
    volume BIGINT,
    source TEXT DEFAULT 'yfinance',
    PRIMARY KEY (symbol, trade_date)
);

-- ============================================================
-- USER-SCOPED INVESTMENT TABLES
-- ============================================================

CREATE TABLE asset_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    ticker_ref_id UUID REFERENCES ticker_reference(id),
    entity_name TEXT NOT NULL,
    resolved_symbol TEXT,
    resolution_method TEXT,
    confidence FLOAT DEFAULT 0,
    is_verified BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, entity_id, resolved_symbol)
);

CREATE TABLE investment_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,

    -- Polymorphic scope: references asset_mappings.id, clusters.id, or topics.id
    -- NOT enforced by FK constraint — validated in application code.
    analysis_scope TEXT NOT NULL CHECK (analysis_scope IN ('asset', 'cluster', 'topic')),
    scope_ref_id UUID,
    symbol TEXT,

    analysis_text TEXT NOT NULL,
    recommendation TEXT,
    confidence FLOAT,
    key_signals JSONB DEFAULT '[]',
    risk_factors JSONB DEFAULT '[]',
    articles_considered INT DEFAULT 0,
    market_data_cache_id UUID REFERENCES market_data_cache(id) ON DELETE SET NULL,
    sentiment_score FLOAT,
    technical_signals JSONB DEFAULT '{}',
    input_tokens INT,
    output_tokens INT,
    model_used TEXT,
    generated_at TIMESTAMPTZ DEFAULT now(),
    analysis_frequency TEXT DEFAULT 'daily',
    next_analysis_at TIMESTAMPTZ
);

CREATE TABLE watchlist_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    added_reason TEXT,
    topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,
    notes TEXT,
    target_price NUMERIC,
    stop_loss NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, symbol)
);

CREATE TABLE price_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    condition TEXT NOT NULL CHECK (condition IN ('above', 'below', 'crosses_above', 'crosses_below')),
    threshold NUMERIC NOT NULL,
    last_known_price NUMERIC,           -- tracks previous price for "crosses" conditions
    is_active BOOLEAN DEFAULT true,
    triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE correlation_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    cluster_id UUID REFERENCES clusters(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_strength FLOAT,
    description TEXT,
    detected_at TIMESTAMPTZ DEFAULT now()
);
```

### PostgreSQL — Indexes

```sql
-- ============================================================
-- INTELLIGENCE INDEXES
-- ============================================================
CREATE INDEX idx_topics_user ON topics(user_id);
CREATE INDEX idx_sources_user_topic ON sources(user_id, topic_id);
CREATE INDEX idx_articles_user_topic ON articles(user_id, topic_id, ingested_at DESC);
CREATE INDEX idx_articles_user_cluster ON articles(user_id, cluster_id);
CREATE INDEX idx_articles_user_hash ON articles(user_id, content_hash);
CREATE INDEX idx_clusters_user_topic ON clusters(user_id, topic_id);
CREATE INDEX idx_entities_user_topic ON entities(user_id, topic_id, type);
CREATE INDEX idx_entity_article_map_user ON entity_article_map(user_id);
CREATE INDEX idx_entity_cluster_map_user ON entity_cluster_map(user_id);
CREATE INDEX idx_sentiment_user_cluster ON sentiment_history(user_id, cluster_id, period_start);
CREATE INDEX idx_sentiment_user_topic ON sentiment_history(user_id, topic_id, period_start);
CREATE INDEX idx_queries_user_topic ON saved_queries(user_id, topic_id);
CREATE INDEX idx_briefings_user_topic ON briefings(user_id, topic_id, generated_at DESC);

-- ============================================================
-- SHARED REFERENCE INDEXES
-- ============================================================
CREATE INDEX idx_ticker_ref_symbol ON ticker_reference(symbol);
CREATE INDEX idx_ticker_ref_type ON ticker_reference(asset_type);
CREATE INDEX idx_ticker_ref_sector ON ticker_reference(sector) WHERE sector IS NOT NULL;
CREATE INDEX idx_price_history_recent ON price_history(symbol, trade_date DESC);
CREATE INDEX idx_market_data_cache_symbol ON market_data_cache(symbol, fetched_at DESC);

-- ============================================================
-- INVESTMENT INDEXES
-- ============================================================
CREATE INDEX idx_asset_mappings_user_topic ON asset_mappings(user_id, topic_id);
CREATE INDEX idx_asset_mappings_symbol ON asset_mappings(user_id, resolved_symbol)
    WHERE resolved_symbol IS NOT NULL;
CREATE INDEX idx_investment_analyses_user_topic ON investment_analyses(user_id, topic_id, generated_at DESC);
CREATE INDEX idx_investment_analyses_scope ON investment_analyses(user_id, analysis_scope, scope_ref_id);
CREATE INDEX idx_watchlist_user ON watchlist_items(user_id);
CREATE INDEX idx_price_alerts_active ON price_alerts(user_id, symbol) WHERE is_active = true;
CREATE INDEX idx_correlation_signals_user ON correlation_signals(user_id, topic_id, detected_at DESC);
```

### SQLAlchemy ORM Models

Every table above has a corresponding SQLAlchemy model. All user-scoped models carry a `user_id` column. The `Base` class provides Alembic metadata for autogenerate.

```python
# services/api/app/models/__init__.py
from app.models.base import Base
from app.models.user import User, ApiKey, RefreshToken
from app.models.intelligence import (
    Topic, Source, Cluster, Article, Entity,
    EntityArticleMap, EntityClusterMap,
    SentimentHistory, SavedQuery, Briefing,
)
from app.models.investment import (
    TickerReference, ThemeEtfMap, MarketDataCache, PriceHistory,
    AssetMapping, InvestmentAnalysis, WatchlistItem,
    PriceAlert, CorrelationSignal,
)

__all__ = [
    "Base",
    "User", "ApiKey", "RefreshToken",
    "Topic", "Source", "Cluster", "Article", "Entity",
    "EntityArticleMap", "EntityClusterMap",
    "SentimentHistory", "SavedQuery", "Briefing",
    "TickerReference", "ThemeEtfMap", "MarketDataCache", "PriceHistory",
    "AssetMapping", "InvestmentAnalysis", "WatchlistItem",
    "PriceAlert", "CorrelationSignal",
]
```

```python
# services/api/app/models/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models.
    
    Used by Alembic for autogenerate (target_metadata = Base.metadata).
    """
    pass
```

```python
# services/api/app/models/user.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, Integer, Text, DateTime, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, unique=True, nullable=False)
    display_name = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    max_topics = Column(Integer, default=10)
    max_articles_per_topic = Column(Integer, default=5000)
    max_api_keys = Column(Integer, default=5)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login_at = Column(DateTime(timezone=True))

    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    topics = relationship("Topic", back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_prefix = Column(Text, nullable=False)
    key_hash = Column(Text, nullable=False)
    label = Column(Text, default="default")
    scopes = Column(JSONB, default=lambda: ["read", "write", "search"])
    rate_limit_per_minute = Column(Integer, default=60)
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="api_keys")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, nullable=False)
    device_info = Column(Text)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

```python
# services/api/app/models/intelligence.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, Integer, Float, Text, Date,
    DateTime, ForeignKey, BigInteger, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base


class Topic(Base):
    __tablename__ = "topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    icon = Column(Text)
    config = Column(JSONB, default=dict)
    refresh_interval_minutes = Column(Integer, default=120)
    last_refreshed_at = Column(DateTime(timezone=True))
    next_refresh_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "name"),)

    user = relationship("User", back_populates="topics")
    clusters = relationship("Cluster", back_populates="topic", cascade="all, delete-orphan")
    articles = relationship("Article", back_populates="topic", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="topic", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    source_type = Column(Text, default="rss")
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    config = Column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("user_id", "topic_id", "url"),)

    topic = relationship("Topic", back_populates="sources")


class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(Text, nullable=False)
    color = Column(Text)
    article_count = Column(Integer, default=0)
    trend_score = Column(Float, default=0)
    velocity = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    topic = relationship("Topic", back_populates="clusters")
    articles = relationship("Article", back_populates="cluster")


class Article(Base):
    __tablename__ = "articles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    source_name = Column(Text)
    source_url = Column(Text)
    published_at = Column(DateTime(timezone=True))
    ingested_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    content_hash = Column(Text)
    raw_storage_key = Column(Text)
    summary = Column(Text)
    sentiment_score = Column(Float)
    relevance_score = Column(Float)
    key_quotes = Column(JSONB, default=list)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="SET NULL"))
    embedding_id = Column(Text)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of = Column(UUID(as_uuid=True), ForeignKey("articles.id"))

    __table_args__ = (UniqueConstraint("user_id", "topic_id", "url"),)

    topic = relationship("Topic", back_populates="articles")
    cluster = relationship("Cluster", back_populates="articles")


class Entity(Base):
    __tablename__ = "entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    first_seen = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "name", "type", "topic_id"),)


class EntityArticleMap(Base):
    __tablename__ = "entity_article_map"

    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    article_id = Column(UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class EntityClusterMap(Base):
    __tablename__ = "entity_cluster_map"

    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class SentimentHistory(Base):
    __tablename__ = "sentiment_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="SET NULL"))
    cluster_keyword = Column(Text)
    period_start = Column(Date, nullable=False)
    avg_sentiment = Column(Float)
    article_count = Column(Integer)

    __table_args__ = (UniqueConstraint("user_id", "cluster_id", "period_start"),)


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    query_text = Column(Text, nullable=False)
    schedule = Column(Text, default="on_refresh")
    last_run = Column(DateTime(timezone=True))
    last_result_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Briefing(Base):
    __tablename__ = "briefings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    summary = Column(Text)
    highlights = Column(JSONB, default=list)
    new_entities = Column(JSONB, default=list)
    watch_items = Column(JSONB, default=list)
    coverage_gaps = Column(JSONB, default=list)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    model_used = Column(Text)
```

```python
# services/api/app/models/investment.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, Integer, Float, Text, Date,
    DateTime, ForeignKey, BigInteger, Numeric, CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base


class TickerReference(Base):
    """Shared reference table — no user_id, no RLS."""
    __tablename__ = "ticker_reference"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    exchange = Column(Text)
    asset_type = Column(Text, nullable=False)
    sector = Column(Text)
    industry = Column(Text)
    market_cap_tier = Column(Text)
    is_active = Column(Boolean, default=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("symbol", "exchange"),)


class ThemeEtfMap(Base):
    """Shared reference table — no user_id, no RLS."""
    __tablename__ = "theme_etf_map"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    theme = Column(Text, nullable=False)
    etf_symbol = Column(Text, nullable=False)
    relevance_score = Column(Float, default=1.0)

    __table_args__ = (UniqueConstraint("theme", "etf_symbol"),)


class MarketDataCache(Base):
    """Shared cache — no user_id, no RLS. Writes restricted to worker role."""
    __tablename__ = "market_data_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(Text, nullable=False)
    asset_type = Column(Text, nullable=False)
    price = Column(Numeric)
    price_change_pct = Column(Numeric)
    volume = Column(BigInteger)
    market_cap = Column(Numeric)
    pe_ratio = Column(Numeric)
    eps = Column(Numeric)
    dividend_yield = Column(Numeric)
    beta = Column(Numeric)
    fifty_two_week_high = Column(Numeric)
    fifty_two_week_low = Column(Numeric)
    data_source = Column(Text)
    is_stale = Column(Boolean, default=False)
    fetched_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PriceHistory(Base):
    """Shared OHLCV cache — no user_id, no RLS."""
    __tablename__ = "price_history"

    symbol = Column(Text, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open = Column(Numeric)
    high = Column(Numeric)
    low = Column(Numeric)
    close = Column(Numeric)
    adj_close = Column(Numeric)
    volume = Column(BigInteger)
    source = Column(Text, default="yfinance")


class AssetMapping(Base):
    __tablename__ = "asset_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    ticker_ref_id = Column(UUID(as_uuid=True), ForeignKey("ticker_reference.id"))
    entity_name = Column(Text, nullable=False)
    resolved_symbol = Column(Text)
    resolution_method = Column(Text)
    confidence = Column(Float, default=0)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "entity_id", "resolved_symbol"),)


class InvestmentAnalysis(Base):
    __tablename__ = "investment_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    analysis_scope = Column(Text, nullable=False)
    scope_ref_id = Column(UUID(as_uuid=True))
    symbol = Column(Text)
    analysis_text = Column(Text, nullable=False)
    recommendation = Column(Text)
    confidence = Column(Float)
    key_signals = Column(JSONB, default=list)
    risk_factors = Column(JSONB, default=list)
    articles_considered = Column(Integer, default=0)
    market_data_cache_id = Column(UUID(as_uuid=True), ForeignKey("market_data_cache.id", ondelete="SET NULL"))
    sentiment_score = Column(Float)
    technical_signals = Column(JSONB, default=dict)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    model_used = Column(Text)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    analysis_frequency = Column(Text, default="daily")
    next_analysis_at = Column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("analysis_scope IN ('asset', 'cluster', 'topic')"),)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(Text, nullable=False)
    asset_type = Column(Text, nullable=False)
    added_reason = Column(Text)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL"))
    notes = Column(Text)
    target_price = Column(Numeric)
    stop_loss = Column(Numeric)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "symbol"),)


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(Text, nullable=False)
    condition = Column(Text, nullable=False)
    threshold = Column(Numeric, nullable=False)
    last_known_price = Column(Numeric)
    is_active = Column(Boolean, default=True)
    triggered_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (CheckConstraint("condition IN ('above', 'below', 'crosses_above', 'crosses_below')"),)


class CorrelationSignal(Base):
    __tablename__ = "correlation_signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="SET NULL"))
    symbol = Column(Text, nullable=False)
    signal_type = Column(Text, nullable=False)
    signal_strength = Column(Float)
    description = Column(Text)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

### PostgreSQL — Row-Level Security & Grants

```sql
-- ============================================================
-- ROW-LEVEL SECURITY (all user-scoped tables)
-- ============================================================

-- Intelligence tables
ALTER TABLE topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE clusters ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_article_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_cluster_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentiment_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE briefings ENABLE ROW LEVEL SECURITY;

-- Investment tables
ALTER TABLE asset_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE correlation_signals ENABLE ROW LEVEL SECURITY;

-- FORCE ensures RLS applies even if the session role matches the table owner.
-- Without FORCE, the table owner (postgres, used by Alembic) bypasses RLS.
-- While app/worker roles are not owners, FORCE is defense-in-depth: it prevents
-- accidental privilege escalation if a migration or debug session runs as owner.
ALTER TABLE topics FORCE ROW LEVEL SECURITY;
ALTER TABLE sources FORCE ROW LEVEL SECURITY;
ALTER TABLE articles FORCE ROW LEVEL SECURITY;
ALTER TABLE clusters FORCE ROW LEVEL SECURITY;
ALTER TABLE entities FORCE ROW LEVEL SECURITY;
ALTER TABLE entity_article_map FORCE ROW LEVEL SECURITY;
ALTER TABLE entity_cluster_map FORCE ROW LEVEL SECURITY;
ALTER TABLE sentiment_history FORCE ROW LEVEL SECURITY;
ALTER TABLE saved_queries FORCE ROW LEVEL SECURITY;
ALTER TABLE briefings FORCE ROW LEVEL SECURITY;
ALTER TABLE asset_mappings FORCE ROW LEVEL SECURITY;
ALTER TABLE investment_analyses FORCE ROW LEVEL SECURITY;
ALTER TABLE watchlist_items FORCE ROW LEVEL SECURITY;
ALTER TABLE price_alerts FORCE ROW LEVEL SECURITY;
ALTER TABLE correlation_signals FORCE ROW LEVEL SECURITY;

-- Note: users, api_keys, refresh_tokens do NOT have RLS — auth
-- queries must work before user context is established. Access control
-- is handled in application code for these tables.

-- RLS context: SET LOCAL ttwatch.current_user_id = '<uuid>';
-- FOR ALL covers SELECT, INSERT, UPDATE, DELETE.
-- USING filters existing rows (SELECT/UPDATE/DELETE).
-- WITH CHECK validates new/modified rows (INSERT/UPDATE).
-- IMPORTANT: Policies are restricted TO ttwatch_app. Without this, the
-- user_isolation policy would also apply to ttwatch_worker, where
-- current_setting('ttwatch.current_user_id') returns '' when the GUC
-- is unset (periodic dispatch tasks). Casting '' to UUID raises
-- "invalid input syntax for type uuid" — even though worker_bypass
-- ORs to true, PostgreSQL may evaluate both policies before short-
-- circuiting. Restricting to ttwatch_app eliminates the risk entirely.
CREATE POLICY user_isolation ON topics FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON sources FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON articles FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON clusters FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON entities FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON entity_article_map FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON entity_cluster_map FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON sentiment_history FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON saved_queries FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON briefings FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON asset_mappings FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON investment_analyses FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON watchlist_items FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON price_alerts FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);
CREATE POLICY user_isolation ON correlation_signals FOR ALL TO ttwatch_app
    USING (user_id = current_setting('ttwatch.current_user_id')::UUID)
    WITH CHECK (user_id = current_setting('ttwatch.current_user_id')::UUID);

-- ============================================================
-- WORKER BYPASS POLICIES
-- ============================================================
-- The worker role needs to query across ALL users for periodic dispatch
-- tasks (schedule_reclustering, schedule_briefings, etc.) which enumerate
-- active users+topics. Without these policies, those tasks would crash
-- because current_setting('ttwatch.current_user_id') is unset when no
-- per-user RLS context has been established.
--
-- These are separate, additive policies (PostgreSQL ORs multiple
-- policies for the same role). When the worker DOES set RLS context
-- via with_rls_context, both policies match; when it doesn't (periodic
-- dispatchers), only the worker bypass policy applies.

CREATE POLICY worker_bypass ON topics FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON sources FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON articles FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON clusters FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON entities FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON entity_article_map FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON entity_cluster_map FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON sentiment_history FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON saved_queries FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON briefings FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON asset_mappings FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON investment_analyses FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON watchlist_items FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON price_alerts FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);
CREATE POLICY worker_bypass ON correlation_signals FOR ALL TO ttwatch_worker
    USING (true) WITH CHECK (true);

-- Note: ticker_reference, theme_etf_map, market_data_cache, price_history
-- are shared reference data and do NOT have RLS policies.

-- ============================================================
-- GRANTS — separated app vs worker roles
-- ============================================================

-- App role (API service): full CRUD on user-scoped tables, READ-ONLY on shared
GRANT SELECT, INSERT, UPDATE, DELETE ON
    users, api_keys, refresh_tokens,
    topics, sources, articles, clusters, entities,
    entity_article_map, entity_cluster_map,
    sentiment_history, saved_queries, briefings,
    asset_mappings, investment_analyses,
    watchlist_items, price_alerts, correlation_signals
TO ttwatch_app;

GRANT SELECT ON
    ticker_reference, theme_etf_map, market_data_cache, price_history
TO ttwatch_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ttwatch_app;

-- Worker role: full access (writes to shared reference tables for data imports)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ttwatch_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ttwatch_worker;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ttwatch_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ttwatch_worker;
```

### Alembic Configuration

```python
# migrations/env.py
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

# Override sqlalchemy.url from environment
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get("DATABASE_URL", "postgresql://postgres:changeme@localhost:5432/ttwatch"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import your models' Base metadata for autogenerate support.
try:
    from app.models import Base
    target_metadata = Base.metadata
except ImportError:
    target_metadata = None

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline():
    """Run migrations in 'offline' mode for SQL script generation.
    
    Usage: alembic upgrade head --sql
    Generates SQL without requiring a live database connection.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

```ini
# config/alembic.ini
[alembic]
script_location = migrations
sqlalchemy.url = postgresql://postgres:changeme@localhost:5432/ttwatch

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

### Data Retention

```python
# worker/tasks/maintenance.py
import logging
from sqlalchemy import text, select
from worker.celeryconfig import app
from worker.db import db_session

logger = logging.getLogger(__name__)

@app.task(name="cleanup_stale_market_data")
def cleanup_stale_market_data():
    """Delete market data older than 30 days. Keep one snapshot per day per symbol.
    
    Uses a CTE to identify rows to keep, then deletes the rest. This is more
    efficient than NOT IN with DISTINCT ON, which forces a full subquery scan.
    """
    with db_session() as session:
        session.execute(text("""
            WITH keep AS (
                SELECT DISTINCT ON (symbol, date_trunc('day', fetched_at))
                    id
                FROM market_data_cache
                WHERE fetched_at < now() - interval '30 days'
                ORDER BY symbol, date_trunc('day', fetched_at), fetched_at DESC
            )
            DELETE FROM market_data_cache
            WHERE fetched_at < now() - interval '30 days'
            AND id NOT IN (SELECT id FROM keep)
        """))


@app.task(name="cleanup_stale_snapshots")
def cleanup_stale_snapshots():
    """Delete old briefings and investment analyses beyond retention window.
    
    Keeps the 10 most recent briefings per user per topic, and
    analyses from the last 90 days.
    """
    with db_session() as session:
        # Clean old briefings: keep latest 10 per user/topic.
        # Uses CTE to identify rows to keep, then DELETE excludes them.
        # More efficient than NOT IN with window function subquery.
        session.execute(text("""
            WITH keep AS (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY user_id, topic_id
                        ORDER BY generated_at DESC
                    ) AS rn
                    FROM briefings
                ) sub
                WHERE rn <= 10
            )
            DELETE FROM briefings
            WHERE id NOT IN (SELECT id FROM keep)
            AND generated_at < now() - interval '7 days'
        """))
        
        # Clean old investment analyses: keep last 90 days
        session.execute(text("""
            DELETE FROM investment_analyses
            WHERE generated_at < now() - interval '90 days'
        """))


@app.task(name="fetch_market_data")
def fetch_market_data(symbol: str):
    """Fetch current market data for a single symbol and cache it.
    
    Dispatched by refresh_market_data periodic task for each watched symbol.
    Uses yfinance for equities and CoinGecko for crypto.
    
    Handles the market_data_cache dedup UNIQUE index by using ON CONFLICT
    (same symbol + same hour → update existing row instead of crash).
    """
    import httpx
    from sqlalchemy import text as sa_text

    with db_session() as session:
        from app.models import MarketDataCache, TickerReference

        # Determine asset type from ticker_reference
        ref = session.execute(
            select(TickerReference).where(TickerReference.symbol == symbol)
        ).scalar_one_or_none()
        asset_type = ref.asset_type if ref else "equity"

        try:
            cache_data = {
                "symbol": symbol,
                "asset_type": asset_type,
                "data_source": "yfinance",
                "price": None,
                "price_change_pct": None,
                "volume": None,
                "market_cap": None,
                "pe_ratio": None,
                "eps": None,
                "dividend_yield": None,
                "beta": None,
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
            }

            if asset_type == "crypto":
                # CoinGecko uses its own IDs (e.g., "bitcoin" not "BTC").
                # Check ticker_reference metadata for coingecko_id, else
                # fall back to symbol lowercase (works for many: "ethereum", "solana").
                cg_id = None
                if ref and ref.metadata_:
                    cg_id = ref.metadata_.get("coingecko_id")
                if not cg_id:
                    cg_id = symbol.lower()

                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(
                        f"https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": cg_id, "vs_currencies": "usd",
                                "include_24hr_change": "true", "include_market_cap": "true",
                                "include_24hr_vol": "true"},
                    )
                    resp.raise_for_status()
                    data = resp.json().get(cg_id, {})
                    cache_data.update({
                        "price": data.get("usd"),
                        "price_change_pct": data.get("usd_24h_change"),
                        "market_cap": data.get("usd_market_cap"),
                        "volume": int(data["usd_24h_vol"]) if data.get("usd_24h_vol") else None,
                        "data_source": "coingecko",
                    })
            else:
                # Use yfinance for equities/ETFs — populate all available fields
                import yfinance as yf
                ticker = yf.Ticker(symbol)
                info = ticker.info
                cache_data.update({
                    "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "price_change_pct": info.get("regularMarketChangePercent"),
                    "market_cap": info.get("marketCap"),
                    "volume": info.get("regularMarketVolume"),
                    "pe_ratio": info.get("trailingPE"),
                    "eps": info.get("trailingEps"),
                    "dividend_yield": info.get("dividendYield"),
                    "beta": info.get("beta"),
                    "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                    "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                })

            if cache_data.get("price") is not None:
                # Use raw SQL upsert to handle the dedup UNIQUE index.
                # ON CONFLICT on (symbol, date_trunc('hour', fetched_at))
                # updates the existing row instead of crashing.
                session.execute(sa_text("""
                    INSERT INTO market_data_cache
                        (id, symbol, asset_type, price, price_change_pct, volume,
                         market_cap, pe_ratio, eps, dividend_yield, beta,
                         fifty_two_week_high, fifty_two_week_low, data_source, fetched_at)
                    VALUES
                        (gen_random_uuid(), :symbol, :asset_type, :price, :price_change_pct,
                         :volume, :market_cap, :pe_ratio, :eps, :dividend_yield, :beta,
                         :fifty_two_week_high, :fifty_two_week_low, :data_source, now())
                    ON CONFLICT (symbol, date_trunc('hour', fetched_at))
                    DO UPDATE SET
                        price = EXCLUDED.price,
                        price_change_pct = EXCLUDED.price_change_pct,
                        volume = EXCLUDED.volume,
                        market_cap = EXCLUDED.market_cap,
                        pe_ratio = EXCLUDED.pe_ratio,
                        eps = EXCLUDED.eps,
                        dividend_yield = EXCLUDED.dividend_yield,
                        beta = EXCLUDED.beta,
                        fifty_two_week_high = EXCLUDED.fifty_two_week_high,
                        fifty_two_week_low = EXCLUDED.fifty_two_week_low,
                        data_source = EXCLUDED.data_source,
                        is_stale = false
                """), cache_data)
                logger.info(f"Market data cached for {symbol}: ${cache_data['price']}")
        except Exception as e:
            logger.warning(f"Failed to fetch market data for {symbol}: {e}")


@app.task(name="cleanup_expired_refresh_tokens")
def cleanup_expired_refresh_tokens():
    """Delete expired refresh tokens from the database.
    
    Refresh tokens have a 30-day expiry (REFRESH_TOKEN_EXPIRE). The login handler
    caps active tokens at 10 per user, but expired tokens are never removed —
    they just fail the expires_at check on refresh. Over time, the refresh_tokens
    table grows unbounded. This task deletes all tokens past their expiry date.
    
    Runs daily. Safe to run concurrently — DELETE with WHERE is idempotent.
    """
    with db_session() as session:
        result = session.execute(text("""
            DELETE FROM refresh_tokens
            WHERE expires_at < now()
        """))
        logger.info(f"Cleaned up {result.rowcount} expired refresh tokens")


@app.task(name="cleanup_orphaned_qdrant_points")
def cleanup_orphaned_qdrant_points():
    """Remove Qdrant points whose corresponding PostgreSQL articles no longer exist.
    
    Over time, article/topic deletions leave orphaned vectors in Qdrant because
    PostgreSQL CASCADE deletes don't propagate to Qdrant. These orphaned points
    inflate cluster article_count, waste storage, and degrade search quality.
    
    Runs daily. Scrolls all Qdrant points and batch-checks existence in PostgreSQL.
    """
    import os
    from qdrant_client import QdrantClient
    from sqlalchemy import select
    from app.models import Article

    qdrant = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))

    # Scroll all points in batches
    orphaned_ids = []
    offset = None
    while True:
        points, next_offset = qdrant.scroll(
            collection_name="articles",
            offset=offset,
            limit=500,
            with_vectors=False,
            with_payload=False,
        )
        if not points:
            break

        point_ids = [str(p.id) for p in points]

        with db_session() as session:
            existing = set(
                str(row[0]) for row in session.execute(
                    select(Article.id).where(Article.id.in_(point_ids))
                ).all()
            )

        orphans = [pid for pid in point_ids if pid not in existing]
        orphaned_ids.extend(orphans)

        if next_offset is None:
            break
        offset = next_offset

    if orphaned_ids:
        # Delete in batches of 500
        for i in range(0, len(orphaned_ids), 500):
            batch = orphaned_ids[i:i + 500]
            qdrant.delete(
                collection_name="articles",
                points_selector=batch,
            )
        logger.info(f"Removed {len(orphaned_ids)} orphaned Qdrant points")
    else:
        logger.info("No orphaned Qdrant points found")
```

### Qdrant Collection

```python
from app.config import settings

qdrant.create_collection("articles", vectors_config={
    "size": settings.EMBEDDING_DIMENSION,  # 1024 for BGE-M3, 3072 for OpenAI
    "distance": "Cosine"
})

qdrant.create_payload_index(
    collection_name="articles",
    field_name="user_id",
    field_schema="keyword"
)
qdrant.create_payload_index(
    collection_name="articles",
    field_name="topic_id",
    field_schema="keyword"
)

# Every point stored with user isolation payload:
qdrant.upsert(collection="articles", points=[{
    "id": article_uuid,
    "vector": embedding,
    "payload": {
        "user_id": "user-uuid-here",
        "topic_id": "topic-uuid-here",
        "title": "...",
        "source": "...",
        "ingested_at": "2026-02-25T12:00:00Z"
    }
}])

# Every search MUST include user_id filter:
qdrant.search(
    collection="articles",
    query_vector=query_embedding,
    query_filter=Filter(must=[
        FieldCondition(key="user_id", match=MatchValue(value=current_user_id)),
        FieldCondition(key="topic_id", match=MatchValue(value=topic_id)),
    ]),
    limit=20
)
```

### Redis Namespace Convention

```
ttwatch:session:{session_id}                    → user_id (DB 2)
ttwatch:rate:{user_id}:{endpoint}               → counter (DB 3)
ttwatch:dedup:urls:{user_id}                    → SET of ingested URLs (DB 2)
ttwatch:tasks:{user_id}                         → LIST background queue (DB 0)
ttwatch:priority:{user_id}                      → LIST priority queue (DB 0)
ttwatch:active_users                            → SET of user_ids (DB 0)
ttwatch:cache:briefing:{user_id}:{topic_id}     → cached briefing JSON (DB 3)
ttwatch:cache:market:{symbol}                    → cached market data (DB 3)
```

### MinIO Bucket Structure

```
ttwatch-content/
├── {user_id_1}/
│   ├── {topic_id_a}/
│   │   ├── {content_hash_1}.html
│   │   ├── {content_hash_1}.txt
│   │   └── ...
│   └── {topic_id_b}/
│       └── ...
├── {user_id_2}/
│   └── ...
```

---

**Sections 9–19 remain identical to v3** with the following exceptions noted in the v4 changelog. The full text of Sections 9–19 (FastAPI Backend, OpenClaw Agent Integration, LLM Prompt Templates, Dashboard Design System, Frontend Structure, Build Phases, Token Budget, File/Folder Structure, Security Checklist, Key Technical Decisions, Data Persistence & Safe Update Strategy) carry forward from v3 with the specific fixes described below applied inline.

---

## 9. FastAPI Backend

### Project Structure

```
services/api/
├── app/
│   ├── main.py                 # FastAPI app with lifespan
│   ├── config.py               # Settings class (pydantic-settings)
│   ├── deps.py                 # get_current_user, get_db, get_qdrant, redis connections
│   ├── auth/
│   │   ├── router.py           # /auth/register, /auth/login, /auth/refresh
│   │   ├── jwt.py
│   │   ├── api_keys.py
│   │   └── passwords.py
│   ├── middleware/
│   │   ├── auth.py
│   │   ├── rls.py
│   │   └── rate_limit.py
│   ├── routers/
│   │   ├── health.py           # /health and /health/services (LAN connectivity)
│   │   ├── users.py
│   │   ├── topics.py
│   │   ├── clusters.py
│   │   ├── articles.py
│   │   ├── search.py
│   │   ├── briefings.py
│   │   ├── entities.py
│   │   ├── sentiment.py
│   │   ├── sources.py
│   │   ├── queries.py
│   │   ├── investment.py       # Watchlist, analyses, alerts
│   │   ├── market_data.py      # Market data endpoints
│   │   └── webhooks.py
│   ├── services/
│   │   ├── llm.py              # ABC
│   │   ├── llm_local.py        # vLLM provider (async)
│   │   ├── llm_cloud.py        # Cloud provider (async)
│   │   ├── llm_factory.py      # get_llm_provider()
│   │   ├── llm_utils.py        # parse_json_response
│   │   ├── http_utils.py       # Retry config (standard + LAN startup)
│   │   ├── embedder.py         # Local + Cloud + factory (async)
│   │   ├── search_engine.py
│   │   ├── clustering.py
│   │   ├── briefing_gen.py
│   │   ├── dedup.py
│   │   └── market_data.py
│   ├── models/                 # SQLAlchemy models (all have user_id)
│   ├── schemas/                # Pydantic schemas
│   └── mcp/
│       ├── server.py
│       └── tools.py
├── Dockerfile
└── requirements.txt
```

### Frontend API Client (SSR-safe)

```typescript
// lib/api-client.ts
const API_BASE = typeof window === 'undefined'
  ? process.env.INTERNAL_API_URL       // Server-side: Docker internal URL
  : process.env.NEXT_PUBLIC_API_URL;   // Client-side: browser-accessible URL (may be LAN IP)
```

### Core Pydantic Schemas

```python
# services/api/app/schemas/topics.py
import uuid
from datetime import datetime
from pydantic import BaseModel


class TopicCreate(BaseModel):
    name: str
    icon: str | None = None
    config: dict = {}
    refresh_interval_minutes: int = 120


class TopicUpdate(BaseModel):
    """All fields optional for partial updates."""
    name: str | None = None
    icon: str | None = None
    config: dict | None = None
    refresh_interval_minutes: int | None = None


class TopicResponse(BaseModel):
    id: uuid.UUID
    name: str
    icon: str | None
    config: dict
    refresh_interval_minutes: int
    last_refreshed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClusterResponse(BaseModel):
    id: uuid.UUID
    keyword: str
    color: str | None
    article_count: int
    trend_score: float
    velocity: str | None

    model_config = {"from_attributes": True}


class ArticleResponse(BaseModel):
    id: uuid.UUID
    url: str
    title: str
    source_name: str | None
    published_at: datetime | None
    ingested_at: datetime
    summary: str | None
    sentiment_score: float | None
    relevance_score: float | None
    cluster_id: uuid.UUID | None
    is_duplicate: bool

    model_config = {"from_attributes": True}


class BriefingResponse(BaseModel):
    id: uuid.UUID
    generated_at: datetime
    summary: str | None
    highlights: list
    new_entities: list
    watch_items: list
    coverage_gaps: list

    model_config = {"from_attributes": True}


class SearchRequest(BaseModel):
    query: str
    topic_id: uuid.UUID
    limit: int = 20


class SearchResult(BaseModel):
    article: ArticleResponse
    score: float
```

### Search Router (Semantic Search)

```python
# services/api/app/routers/search.py
"""Semantic search across articles using Qdrant vector similarity."""
import logging
from fastapi import APIRouter, Depends, Request
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from app.deps import get_current_user, get_db
from app.config import settings
from app.models import User, Article
from app.schemas.topics import SearchRequest, SearchResult, ArticleResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level async client — shared across all requests.
# NOT created per-request (avoids connection exhaustion under load).
_qdrant: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Lazy-initialize the async Qdrant client."""
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=settings.QDRANT_URL, timeout=30)
    return _qdrant


@router.post("/search", response_model=list[SearchResult])
async def semantic_search(
    req: SearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search articles by semantic similarity using the query embedding.
    
    1. Embeds the query text using the configured embedding provider.
    2. Searches Qdrant with user_id + topic_id filters (async, non-blocking).
    3. Fetches full article records from PostgreSQL for matching IDs.
    """
    embedder = request.app.state.embedder
    query_embedding = (await embedder.embed([req.query]))[0]

    qdrant = get_qdrant_client()
    results = await qdrant.search(
        collection_name="articles",
        query_vector=query_embedding,
        query_filter=Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=str(user.id))),
            FieldCondition(key="topic_id", match=MatchValue(value=str(req.topic_id))),
        ]),
        limit=req.limit,
    )

    if not results:
        return []

    # Fetch full article records from PostgreSQL
    article_ids = [hit.id for hit in results]
    score_map = {str(hit.id): hit.score for hit in results}

    articles = await db.execute(
        select(Article).where(Article.id.in_(article_ids))
    )
    article_map = {str(a.id): a for a in articles.scalars().all()}

    search_results = []
    for aid in article_ids:
        article = article_map.get(str(aid))
        if article:
            search_results.append(SearchResult(
                article=ArticleResponse.model_validate(article),
                score=score_map.get(str(aid), 0.0),
            ))

    return search_results
```

### Topics Router (CRUD)

```python
# services/api/app/routers/topics.py
"""CRUD operations for intelligence topics."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models import User, Topic, Article, Cluster
from app.schemas.topics import TopicCreate, TopicUpdate, TopicResponse, ClusterResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/topics", response_model=list[TopicResponse])
async def list_topics(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all topics for the current user."""
    result = await db.execute(
        select(Topic).where(Topic.user_id == user.id).order_by(Topic.created_at.desc())
    )
    return result.scalars().all()


@router.post("/topics", response_model=TopicResponse, status_code=201)
async def create_topic(
    req: TopicCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new topic for the current user."""
    # Enforce topic limit
    count = await db.execute(
        select(func.count(Topic.id)).where(Topic.user_id == user.id)
    )
    if count.scalar() >= user.max_topics:
        raise HTTPException(403, f"Topic limit reached ({user.max_topics})")

    topic = Topic(
        user_id=user.id,
        name=req.name,
        icon=req.icon,
        config=req.config,
        refresh_interval_minutes=req.refresh_interval_minutes,
    )
    db.add(topic)
    await db.flush()
    return topic


@router.get("/topics/{topic_id}", response_model=TopicResponse)
async def get_topic(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single topic by ID."""
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")
    return topic


@router.put("/topics/{topic_id}", response_model=TopicResponse)
async def update_topic(
    topic_id: uuid.UUID,
    req: TopicUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing topic. Supports partial updates — only provided fields are changed.
    
    Uses Pydantic's model_fields_set to distinguish between "field not provided"
    (not in JSON body) and "field explicitly set to null" ({"icon": null}).
    This enables clearing optional fields like icon.
    """
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")

    # Only update fields that were explicitly included in the request body.
    # model_fields_set contains field names the client actually sent.
    for field_name in req.model_fields_set:
        setattr(topic, field_name, getattr(req, field_name))
    return topic


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a topic and all associated data.
    
    NOTE: This only deletes PostgreSQL data. Qdrant vectors for this topic's
    articles will be cleaned up by the daily cleanup_orphaned_qdrant_points task.
    """
    topic = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = topic.scalar_one_or_none()
    if not topic:
        raise HTTPException(404, "Topic not found")
    await db.delete(topic)


@router.get("/topics/{topic_id}/clusters", response_model=list[ClusterResponse])
async def list_clusters(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List clusters for a topic, ordered by trend score."""
    result = await db.execute(
        select(Cluster).where(
            Cluster.topic_id == topic_id,
            Cluster.user_id == user.id,
        ).order_by(Cluster.trend_score.desc())
    )
    return result.scalars().all()
```

---

## 16. File/Folder Structure (updated)

```
ttwatch/
├── docker-compose.yml
├── docker-compose.gpu.yml           # Colocated GPU (same machine)
├── docker-compose.gpu-node.yml      # Remote GPU node (runs on GPU machine)
├── docker-compose.search-node.yml   # Remote SearXNG node (runs on search machine)
├── docker-compose.lan.yml           # LAN override for main server
├── docker-compose.cloud.yml
├── docker-compose.dev.yml
├── .env.example
├── .gitignore
├── .dockerignore
├── Makefile
│
├── config/
│   ├── searxng/settings.yml
│   └── alembic.ini
│
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 001_create_users_and_auth.py
│       ├── 002_create_intelligence_tables.py
│       ├── 003_create_investment_tables.py
│       ├── 004_add_rls_policies.py
│       ├── 005_grants_app_role.py
│       └── 006_grants_worker_role.py
│
├── models/                     # gitignored — downloaded separately
│   └── .gitkeep
│
├── backups/                    # gitignored — created by `make backup`
│   └── .gitkeep
│
├── scripts/
│   ├── init-db.sh
│   ├── update.sh
│   ├── backup.sh
│   ├── restore.sh
│   ├── download-models.sh
│   ├── seed-topics.py
│   ├── create-admin-user.py
│   └── benchmark-gpu.py
│
├── services/
│   ├── api/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py
│   │       ├── config.py
│   │       ├── deps.py
│   │       ├── auth/
│   │       │   ├── router.py             # /auth/register, /auth/login, /auth/refresh, /auth/logout
│   │       │   ├── jwt.py
│   │       │   ├── api_keys.py
│   │       │   └── passwords.py
│   │       ├── middleware/
│   │       │   └── rate_limit.py
│   │       ├── routers/
│   │       │   ├── health.py
│   │       │   └── ...
│   │       ├── services/
│   │       │   ├── llm.py              # ABC (async)
│   │       │   ├── llm_local.py
│   │       │   ├── llm_cloud.py
│   │       │   ├── llm_factory.py
│   │       │   ├── llm_utils.py
│   │       │   ├── http_utils.py
│   │       │   ├── embedder.py
│   │       │   ├── init_services.py    # Qdrant + MinIO init
│   │       │   └── ...
│   │       ├── models/                 # SQLAlchemy ORM models
│   │       │   ├── __init__.py         # Re-exports all models + Base
│   │       │   ├── base.py             # DeclarativeBase
│   │       │   ├── user.py             # User, ApiKey, RefreshToken
│   │       │   ├── intelligence.py     # Topic, Source, Cluster, Article, Entity, etc.
│   │       │   └── investment.py       # TickerReference, AssetMapping, WatchlistItem, etc.
│   │       ├── schemas/                # Pydantic request/response models
│   │       │   ├── __init__.py
│   │       │   └── topics.py           # TopicCreate/Update/Response, ClusterResponse, ArticleResponse, SearchRequest/Result
│   │       └── mcp/
│   │
│   ├── worker/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── worker/
│   │       ├── celeryconfig.py         # Includes task_routes + beat_schedule
│   │       ├── db.py
│   │       ├── rls.py                 # RLS decorator (handles bind=True + unbound tasks)
│   │       ├── llm_sync.py            # SyncLLMClient + SyncEmbeddingClient
│   │       ├── startup.py
│   │       └── tasks/
│   │           ├── __init__.py        # Required for autodiscover_tasks
│   │           ├── utils.py           # Shared MinIO fetch helper (fetch_article_text)
│   │           ├── ingest.py          # Trafilatura extraction + dedup + fan-out (all 4 tasks)
│   │           ├── summarize.py       # Uses MinIO fetch via utils.py
│   │           ├── embed.py           # Embedding + Qdrant upsert + Layer 3 semantic dedup
│   │           ├── entities.py        # Named entity extraction via LLM
│   │           ├── sentiment.py       # Sentiment classification via LLM
│   │           ├── cluster.py         # Full HDBSCAN + PostgreSQL persistence
│   │           ├── briefing.py        # Topic briefing generation (hierarchical summarization)
│   │           ├── trends.py          # Trend score + velocity computation
│   │           ├── sentiment_agg.py   # Sentiment history aggregation
│   │           ├── coverage_gaps.py   # Coverage gap detection via LLM
│   │           ├── dedup.py
│   │           ├── market_data.py     # fetch_market_data task (yfinance + CoinGecko)
│   │           ├── resolve_ticker.py  # resolve_entity_ticker (two-step lookup + LLM)
│   │           ├── investment_analysis.py  # generate_investment_analyses
│   │           ├── correlation_signals.py  # detect_correlation_signals
│   │           ├── price_alerts.py   # check_price_alerts
│   │           ├── periodic.py        # All dispatch task stubs (recluster, briefings, etc.)
│   │           ├── maintenance.py     # cleanup tasks + cleanup_orphaned_qdrant_points
│   │           └── queue_discovery.py
│   │
│   ├── embedder/                       # Complete implementation
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── server.py
│   │
│   └── frontend/
│       ├── Dockerfile
│       ├── package.json
│       └── src/
│           ├── app/
│           ├── components/
│           ├── hooks/
│           └── lib/
│               ├── api-client.ts
│               ├── auth-storage.ts
│               ├── design-tokens.ts
│               └── force-simulation.ts
│
└── docs/
    ├── api-reference.md
    ├── agent-integration.md
    ├── deployment.md
    ├── multi-tenancy.md
    └── lan-deployment.md
```

---

## 17. Security Checklist (updated)

```
[ ] Every PostgreSQL query filters by user_id (code review all queries)
[ ] RLS policies active on all user-data tables (15 tables, including join tables)
[ ] RLS policies include both USING and WITH CHECK clauses (FOR ALL)
[ ] Join tables (entity_article_map, entity_cluster_map) have user_id + RLS
[ ] Shared reference tables are READ-ONLY for ttwatch_app role
[ ] Every Qdrant search includes user_id in the filter
[ ] MinIO paths always include user_id prefix
[ ] Redis keys always include user_id namespace
[ ] MCP tools always call resolve_user_from_context() first
[ ] API key hashes stored, never plaintext
[ ] JWT tokens are short-lived (15 min) with refresh rotation
[ ] Rate limiting per user on all endpoints (atomic Lua script)
[ ] No user_id in error messages (prevents enumeration)
[ ] Admin endpoints require is_admin=true
[ ] CORS restricted to configured origins (CORS_ORIGINS env var, supports multiple)
[ ] Worker tasks propagate user_id from queue, never from request body
[ ] Worker tasks set RLS context via @with_rls_context decorator
[ ] Worker tasks are synchronous (def, not async def) — Celery requirement
[ ] Worker tasks use SyncLLMClient/SyncEmbeddingClient (not async providers)
[ ] Password hashing uses argon2id (not bcrypt, not sha256)
[ ] API key prefix reveals user_short_id only (not full user_id)
[ ] init-db.sh uses safe password escaping (no shell interpolation into SQL)
[ ] SET LOCAL uses f-string with validated UUID (not bind params) — PG SET doesn't accept $1
[ ] investment_analyses.scope_ref_id validated in application code (polymorphic FK)
[ ] market_data_cache has no RLS (shared) — write access restricted to worker role
[ ] vLLM image version supports RTX 5090 (Blackwell architecture)
[ ] GPU startup order enforced: embedder loads before vLLM (colocated or node compose)
[ ] .env is in .gitignore (secrets never committed to git)
[ ] .dockerignore prevents secrets and models from entering build context
[ ] Docker named volumes (pgdata, qdrant_data, redis_data, minio_data) all declared
[ ] `docker compose down -v` NEVER used in production (destroys all data)
[ ] All schema changes after initial deploy go through Alembic migrations
[ ] Alembic migrations include GRANT + RLS statements for new tables
[ ] Backup script tested before any production update
[ ] Qdrant embedding dimension matches EMBEDDING_DIMENSION env var
[ ] LAN: remote vLLM/embedder/SearXNG ports not exposed to public internet
[ ] LAN: CORS_ORIGINS includes all LAN hostnames/IPs that access the frontend
[ ] LAN: Frontend NEXT_PUBLIC_API_URL uses LAN-accessible IP (not localhost)
[ ] JWT decode wrapped in try/except (ExpiredSignatureError, InvalidTokenError → 401)
[ ] Qdrant collection created at startup via init_services.py (idempotent)
[ ] MinIO bucket created at startup via init_services.py (idempotent)
[ ] All storage services have restart: unless-stopped
[ ] All GPU compose services have restart: unless-stopped
[ ] Celery task_routes configured — CPU tasks to ttwatch:compute queue
[ ] beat_schedule defined in celeryconfig.py (not in task module)
[ ] RateLimiter instantiated and wired into API dependencies
[ ] All FastAPI routers included in main.py
[ ] SearXNG settings.yml configures JSON output format
[ ] .gitignore excludes .env and models/
[ ] .dockerignore excludes secrets and large directories
[ ] SQLAlchemy ORM models defined for all tables (Base, User, Article, etc.)
[ ] Worker Dockerfile copies app/models for ORM access
[ ] Worker uses psycogreen to patch psycopg2 for gevent compatibility
[ ] Auth router implements register, login, refresh with argon2id
[ ] Cloud compose overrides scheduler service (not just api + workers)
[ ] Dev compose override does not use YAML anchor from base compose
[ ] Ingestion task implements 3-layer dedup (URL, content hash, semantic)
[ ] parse_json_response handles fences, preamble, and bare JSON
[ ] with_rls_context handles both bind=True and unbound tasks correctly
[ ] Worker tasks access article content via MinIO fetch helper (no raw_text column)
[ ] embed_article uses article UUID as Qdrant point ID (clustering contract)
[ ] Redis dedup client in ingest.py is module-level singleton (not per-invocation)
[ ] All beat_schedule tasks have corresponding task implementations
[ ] worker/tasks/__init__.py exists (required for Celery autodiscover_tasks)
[ ] users router registered in main.py (not just listed in project structure)
[ ] Worker role has bypass RLS policies on all user-scoped tables (periodic tasks)
[ ] with_rls_context uses kwargs.pop() not kwargs.get() for user_id (prevents double-passing)
[ ] get_current_user explicitly converts JWT sub claim to UUID before db.get()
[ ] sentiment_history.cluster_id is nullable with ON DELETE SET NULL (not CASCADE)
[ ] recluster_topic nullifies sentiment_history FKs before deleting clusters
[ ] embed_article and summarize_article have max_retries + default_retry_delay
[ ] Alembic env.py supports both online and offline migration modes
[ ] init-db.sh sets ALTER DEFAULT PRIVILEGES for both app and worker roles
[ ] embed_article uses 1500-char body window (not 512) for quality embeddings
[ ] cleanup_stale_market_data uses CTE pattern (not slow NOT IN subquery)
[ ] Unused imports removed from ingest.py (SyncEmbeddingClient, Topic)
[ ] extract_entities task implemented and called from ingest fan-out
[ ] classify_sentiment task implemented and called from ingest fan-out
[ ] ingest_article fans out to all 4 tasks (summarize, embed, entities, sentiment)
[ ] published_at extracted from article metadata via trafilatura.extract_metadata()
[ ] source_url parameter accepted and stored in ingest_article
[ ] embed_article performs Layer 3 semantic dedup (cosine > 0.92) after upsert
[ ] fetch_market_data task implemented with yfinance + CoinGecko support
[ ] cleanup_orphaned_qdrant_points task runs daily to remove stale vectors
[ ] recluster_topic uses DB rowcount for article_count (not Qdrant point count)
[ ] Queue discovery scans only ttwatch:priority:* (removed nonexistent patterns)
[ ] WebSocket /ws endpoint implemented with JWT auth for real-time updates
[ ] cleanup_stale_snapshots uses CTE pattern for performance
[ ] yfinance added to worker requirements.txt
[ ] maintenance.py has module-level logging import and logger definition
[ ] maintenance.py imports select from sqlalchemy at module level
[ ] fetch_market_data uses module-level select import (not missing)
[ ] cleanup_orphaned_qdrant_points uses module-level logger (not missing)
[ ] All 5 core intelligence tasks have implementations (briefing, trends, sentiment_agg, coverage_gaps, investment_analysis)
[ ] asyncio.get_running_loop() used instead of deprecated get_event_loop()
[ ] ConnectionManager.connect() used by websocket_endpoint (no direct dict manipulation)
[ ] Unused CorrelationSignal import removed from recluster_topic
[ ] Auth router includes /auth/logout endpoint for refresh token invalidation
[ ] RegisterRequest validates password strength (min 10 chars, mixed case, digit)
[ ] Pydantic schemas defined for topics, clusters, articles, search
[ ] Search router uses AsyncQdrantClient (not sync, avoids blocking event loop)
[ ] Search router uses module-level shared Qdrant client (not per-request instantiation)
[ ] Password validator uses @field_validator("password") decorator (not bare @classmethod)
[ ] WebSocket implements heartbeat ping/pong with 30s interval and 90s timeout
[ ] WebSocket auth message has 10s timeout to prevent dangling connections
[ ] Qdrant init_qdrant validates dimension matches EMBEDDING_DIMENSION (prevents silent mismatches)
[ ] resolve_entity_ticker validates entity type before attempting resolution
[ ] check_price_alerts deactivates triggered alerts (prevents repeated triggers)
[ ] Topics router enforces max_topics limit on create
[ ] Topics router delete notes Qdrant cleanup is deferred to daily GC task
[ ] detect_correlation_signals requires minimum signal_strength of 0.3
[ ] All 3 investment pipeline tasks (resolve_ticker, correlations, price_alerts) have implementations
[ ] AsyncQdrantClient dependency (aiohttp) in API requirements.txt
[ ] RLS user_isolation policies restricted TO ttwatch_app (not PUBLIC) — prevents UUID cast error for worker
[ ] register endpoint handles IntegrityError for concurrent duplicate email (returns 409, not 500)
[ ] check_price_alerts uses last_known_price for crosses_above/crosses_below conditions
[ ] compute_sentiment_history uses datetime.now(timezone.utc).date() not date.today()
[ ] fetch_market_data uses ON CONFLICT upsert for market_data_cache dedup index
[ ] fetch_market_data populates all available yfinance fields (volume, PE, EPS, beta, 52-week range)
[ ] fetch_market_data crypto path uses coingecko_id from ticker_reference metadata
[ ] price_alerts table includes last_known_price column for crosses condition tracking
[ ] TopicUpdate uses model_fields_set for proper partial update semantics (not `is not None` check)
[ ] fetch_market_data crypto path initializes all SQL bind parameters (pe_ratio, eps, etc. defaulted to None)
[ ] WebSocket heartbeat uses asyncio.get_running_loop().time() (not deprecated get_event_loop())
[ ] Login endpoint caps refresh tokens at 10 per user (prevents unbounded accumulation)
[ ] Price alert crosses conditions initialize last_known_price on first check (not left NULL)
[ ] FORCE ROW LEVEL SECURITY applied to all 15 user-scoped tables (defense-in-depth)
[ ] Login handler stores token_count.scalar() in variable before reuse (Result consumed on first call)
[ ] Price alert notifications published to Redis pub/sub for WebSocket delivery
[ ] ws_alert_listener background task started in API lifespan for pub/sub subscription
[ ] price_alerts.condition has CHECK constraint limiting to valid values
[ ] Expired refresh tokens cleaned up daily by cleanup_expired_refresh_tokens task
[ ] refresh_tokens.token_hash indexed for fast lookup on refresh/logout
[ ] sentiment_history.cluster_keyword preserves cluster context after recluster nullifies cluster_id
[ ] sentiment_history.topic_id preserves topic context for direct topic-level queries after recluster
[ ] Login token cap counts only unexpired tokens (excludes expired tokens from count and delete)
[ ] ws_alert_listener uses settings.REDIS_CACHE_URL directly (no dead os.environ fallback)
[ ] Appendix cross-references accurately point to inline technical decisions (not §18)
```

---

## 18. Key Technical Decisions & Tradeoffs (updated)

*All previous decisions carry forward. v8 additions:*

**Why per-table `worker_bypass` RLS policies instead of `BYPASSRLS` on the worker role?** `ALTER ROLE ttwatch_worker BYPASSRLS` grants blanket bypass of ALL RLS policies, including any future tables that might have different security requirements. Per-table `worker_bypass` policies (using `USING (true) WITH CHECK (true)`) are granular and auditable — they explicitly list which tables the worker can access without restriction. If a new high-security table is added, it won't automatically be bypassed.

**Why `kwargs.pop("user_id")` instead of `kwargs.get("user_id")` in `with_rls_context`?** When `user_id` is passed as a keyword argument, `get()` leaves it in kwargs. The wrapper then passes `user_id` as a positional argument AND in `**kwargs`, causing `TypeError: got multiple values for argument 'user_id'`. Using `pop()` removes it from kwargs, preventing the collision. While Celery normally passes arguments positionally, the decorator must handle all invocation patterns safely.

**Why explicit `uuid.UUID(user_id)` in `get_current_user`?** The JWT `sub` claim is always a string. SQLAlchemy's asyncpg dialect may or may not auto-coerce strings to UUIDs depending on the operation type. `db.get()` uses the primary key directly, and passing a string where a UUID is expected can raise `DataError: invalid input syntax for type uuid` under certain asyncpg versions. Explicit conversion guarantees type safety.

**Why `ON DELETE SET NULL` for `sentiment_history.cluster_id` instead of `CASCADE`?** Sentiment history tracks data over time and must survive cluster regeneration. Clusters are rebuilt every 2 hours by `recluster_topic`, which deletes and recreates them. With `CASCADE`, all historical sentiment data would be destroyed every 2 hours. `SET NULL` preserves the data — orphaned sentiment records (where `cluster_id IS NULL`) represent historical snapshots from previous clustering cycles and remain available for trend analysis.

**Why nullify FK references before cluster deletion in `recluster_topic`?** Even with `ON DELETE SET NULL` on sentiment_history, `entity_cluster_map` uses `ON DELETE CASCADE` (since entity-cluster mappings are ephemeral and fully regenerated). Explicitly handling the cleanup in application code makes the behavior predictable and prevents accidental data loss if FK constraints are modified in future migrations.

**Why retry configuration on `embed_article` and `summarize_article`?** These tasks are fanned out from `ingest_article` and depend on external services (BGE-M3 embedder, vLLM). If either service is temporarily unavailable (pod restart, memory spike, network blip), the task should retry rather than fail permanently. `max_retries=3` with `default_retry_delay=30` gives services up to ~90 seconds to recover. Without retries, a brief embedder hiccup would leave articles permanently unembedded and unsummarized.

**Why 1500 chars instead of 512 for embedding text?** BGE-M3 supports 8192 tokens (~32K chars). The original 512-char limit captured only the opening paragraph of most articles, missing critical context. 1500 chars captures approximately the first 3-4 paragraphs, providing substantially better semantic representation while keeping batch embedding throughput high. The title prefix ensures topic relevance even if the body is truncated.

**Why `ALTER DEFAULT PRIVILEGES` in init-db.sh instead of per-table GRANTs?** Per-table GRANT statements (as in §8 SQL) must be re-run after every Alembic migration that adds new tables. `ALTER DEFAULT PRIVILEGES` automatically grants the specified permissions on any future tables created by the postgres superuser in the public schema. This makes `alembic upgrade head` self-sufficient — new tables are immediately accessible without manual GRANT scripts.

**Why configurable `EMBEDDING_DIMENSION`?** BGE-M3 produces 1024-dim vectors. OpenAI's `text-embedding-3-large` produces 3072-dim vectors by default. The Qdrant collection's vector size must match. Making this configurable avoids silent failures when switching between local and cloud embeddings.

**Why `CORS_ORIGINS` as comma-separated instead of a single origin?** LAN deployments need multiple origins: `http://localhost:3000` for the operator's local browser and `http://192.168.1.100:3000` for other LAN users. A single origin would force operators to choose.

**Why `profiles: ["disabled"]` for SearXNG in LAN mode?** Docker Compose's `profiles` feature cleanly prevents a service from starting without removing it from the file. This is more maintainable than `scale: 0` and doesn't require editing the base compose file.

*v9 additions:*

**Why implement `extract_entities` and `classify_sentiment` as separate tasks?** Each task makes an independent LLM call and can fail/retry independently. Running them in parallel (fanned out from `ingest_article`) maximizes throughput — all 4 processing tasks (summarize, embed, entities, sentiment) run concurrently. If entity extraction fails, the article still gets its summary and embedding. This aligns with the existing fan-out pattern.

**Why extract `published_at` from trafilatura metadata?** Without the actual publication date, all temporal analysis (trend detection, briefing windows, article recency) must use `ingested_at` — the time TTwatch processed the article. This can be hours or days after publication, especially for RSS backfill. `published_at` enables accurate temporal ordering and time-windowed queries. Falls back gracefully to `None` if the metadata doesn't contain a parseable date.

**Why add Layer 3 semantic dedup to `embed_article` instead of `ingest_article`?** Semantic dedup requires an embedding vector. The embedding is only computed in `embed_article`, not during ingestion. Computing a separate embedding in `ingest_article` would double the embedder load. Instead, `embed_article` performs the dedup check immediately after upserting, using the freshly computed vector to search for near-duplicates in Qdrant (cosine > 0.92). This is cheap (single vector search) and happens at the right point in the pipeline.

**Why a dedicated `cleanup_orphaned_qdrant_points` task?** PostgreSQL `ON DELETE CASCADE` doesn't propagate to Qdrant. When topics or articles are deleted, their Qdrant vectors remain orphaned. These orphans inflate cluster `article_count` (since `recluster_topic` counts Qdrant points), waste vector storage, and potentially degrade search quality. The daily cleanup task scrolls all points and batch-verifies existence in PostgreSQL. The v9 fix to `recluster_topic` (using DB rowcount) mitigates the count inflation, but removing orphans entirely keeps the vector store clean.

**Why use `result.rowcount` to correct cluster `article_count`?** Qdrant may contain orphaned points from deleted articles. When `recluster_topic` assigns articles to clusters, it counts Qdrant points (which may include orphans). The PostgreSQL UPDATE's `rowcount` reflects how many actual articles were updated, giving an accurate count. This prevents dashboard displays from showing inflated numbers while the Qdrant GC task handles full cleanup.

**Why remove `ttwatch:scheduled:*` and `ttwatch:tasks:*` from queue discovery?** No code in the system creates Redis keys matching these patterns. Celery uses queue names as direct Redis list keys (e.g., `ttwatch:default`, `ttwatch:compute`). The only custom queue pattern actually used is `ttwatch:priority:{user_id}`. Scanning nonexistent patterns wastes Redis SCAN cycles.

**Why add a WebSocket endpoint?** The architecture (§2) and frontend configuration (`NEXT_PUBLIC_WS_URL`) both reference WebSocket support, but no implementation existed. The WebSocket enables real-time dashboard updates (new articles, cluster changes, briefing completions) without polling. JWT authentication on the WebSocket prevents unauthorized connections.

**Why `yfinance` in worker requirements?** The `fetch_market_data` task was dispatched by the periodic scheduler but never implemented. yfinance provides free equity/ETF market data without API keys. CoinGecko's free tier handles crypto data. Both are used conditionally based on asset_type from the ticker_reference table.

*v10 additions:*

**Why add `logging` and `select` imports at the module level of `maintenance.py`?** Both `fetch_market_data` and `cleanup_orphaned_qdrant_points` use `logger.info()` and `select()` but neither was imported. These would crash with `NameError` on every invocation. Adding them at module level follows the pattern used by all other task files (e.g., `summarize.py`, `embed.py`, `entities.py`).

**Why implement all 5 core intelligence tasks instead of leaving them as stubs?** The periodic dispatch tasks (`schedule_briefings`, `schedule_trend_updates`, etc.) are fully implemented and execute on the beat schedule. They dispatch work tasks by name (e.g., `generate_briefing`). Without implementations, Celery workers log `Received unregistered task` errors every 2 hours for every user/topic pair. This is the same class of bug fixed in v7 (#103) and v9 (#131). The implementations follow the established patterns for LLM tasks (hierarchical summarization for briefings) and pure-compute tasks (SQL aggregation for sentiment history).

**Why `asyncio.get_running_loop()` instead of `get_event_loop()`?** `asyncio.get_event_loop()` is deprecated in Python 3.10+ and emits `DeprecationWarning` in Python 3.12 (which TTwatch uses). `get_running_loop()` is the correct replacement when called from within an async context (which `ensure_queue_consumed` always is, since it's an `async def` called from FastAPI handlers).

**Why add a `/auth/logout` endpoint?** Without explicit logout, users cannot invalidate their refresh tokens. A stolen refresh token would remain valid for up to 30 days. The logout endpoint deletes the token from the database, preventing further access token generation. The endpoint always returns 200 regardless of whether the token existed, preventing token existence enumeration.

**Why add password strength validation?** The original register endpoint accepted any string as a password, including empty strings and single characters. Weak passwords undermine the entire auth system. The validation requires minimum 10 characters with mixed case and at least one digit, which balances security with usability. Validation is done in the Pydantic model to fail fast before hitting the database.

**Why implement the search router with a two-step Qdrant→PostgreSQL pattern?** Qdrant returns similarity scores and point IDs but stores only minimal payload (title, source, ingested_at). Full article data (summary, sentiment, cluster assignment, source_url) lives in PostgreSQL. The search router first queries Qdrant for top-K vector matches with user isolation, then batch-fetches the corresponding Article records from PostgreSQL. This maintains the separation of concerns: Qdrant for fast similarity search, PostgreSQL for authoritative data.

**Why use `model_config = {"from_attributes": True}` on Pydantic schemas?** This replaces the Pydantic v1 `orm_mode = True` pattern. It allows Pydantic to read data from SQLAlchemy ORM instances directly (e.g., `ArticleResponse.model_validate(article)`) without manual dict conversion. All response schemas need this for the FastAPI router to serialize ORM objects.

*v11 additions:*

**Why `@field_validator("password")` instead of bare `@classmethod`?** Pydantic v2 requires explicit `@field_validator("field_name")` decorators to register validators. A bare `@classmethod` without the decorator is just a regular class method that Pydantic never calls during model validation. This was silently allowing empty/weak passwords through the registration endpoint, completely bypassing the security validation. The `@classmethod` decorator must appear AFTER `@field_validator` due to Python's decorator evaluation order.

**Why `AsyncQdrantClient` instead of synchronous `QdrantClient` in the search router?** The search router is an `async def` FastAPI handler. Using the synchronous `QdrantClient.search()` blocks the event loop for the duration of the Qdrant network call (typically 10-50ms, up to 30s on timeout). Under concurrent load, this serializes all search requests. `AsyncQdrantClient.search()` yields control back to the event loop, allowing other requests to be processed during the network wait.

**Why a module-level shared Qdrant client instead of per-request instantiation?** Creating a new `QdrantClient` per request establishes a new TCP connection each time. Under high search traffic, this exhausts the connection pool and file descriptor limits. A shared module-level client maintains a persistent connection pool. The `QdrantClient` is thread-safe and designed for reuse across requests.

**Why WebSocket heartbeat with ping/pong instead of relying on TCP keepalive?** TCP keepalive operates at the OS level with long intervals (typically 2+ hours). Load balancers, reverse proxies (nginx, AWS ALB), and firewalls commonly close idle WebSocket connections after 30-60 seconds. Application-level ping/pong with 30s intervals and 90s dead-connection timeout ensures the connection stays alive through intermediary infrastructure and detects dead clients promptly.

**Why validate Qdrant collection dimension at startup?** Switching between local embeddings (BGE-M3: 1024-dim) and cloud embeddings (OpenAI text-embedding-3-large: 3072-dim) requires a matching Qdrant collection dimension. If the collection exists with the wrong dimension, all vector operations will silently produce meaningless results — searches return random articles, semantic dedup stops working, clustering becomes noise. The startup check fails fast with a clear error message instead of allowing subtle data corruption.

**Why implement `resolve_entity_ticker` with a two-step lookup-then-LLM approach?** Direct lookup against `ticker_reference` is instant and reliable for well-known companies (Apple → AAPL, Tesla → TSLA). LLM inference is slower and less reliable but handles edge cases (new entities, informal names, subsidiaries). The two-step approach minimizes LLM calls for common entities while still supporting the long tail. Confidence scoring allows downstream tasks to weight results appropriately.

**Why implement `check_price_alerts` as a periodic beat task instead of event-driven?** Price data is fetched hourly by `refresh_market_data`. Real-time price feeds would require a streaming market data subscription (expensive). Given hourly data refreshes, checking alerts every 15 minutes is sufficient — it catches threshold crossings within the hour. The periodic approach is simpler, doesn't require additional infrastructure, and aligns with the existing batch-processing architecture.

**Why add a full topics CRUD router?** The document claims to be "the single source of truth" (§1) and "self-contained" yet lacked the most fundamental API endpoint — creating and managing topics. Without it, users cannot interact with the platform at all. The implementation enforces the per-user `max_topics` limit from the users table and notes that Qdrant cleanup after topic deletion is handled by the daily GC task.

---

## Appendix A — v2 Change Log

*Unchanged from v3. See original document.*

---

## Appendix B — v3 Change Log

*Unchanged from v3. See original document.*

---

## Appendix C — v4 Change Log

This section summarizes the 17 issues identified during the v4 deep review. The primary focus of v4 is **distributed LAN deployment** and **missing implementation details** that would prevent the platform from starting.

### Critical Bugs Fixed (would break at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 41 | **`os` not imported in `main.py`** — The CORS middleware configuration used `os.environ.get("CORS_ORIGIN"...)` but `os` was never imported. App would crash on startup with `NameError`. | §4 Lifespan | Added `import os` at top of `main.py`. |
| 42 | **No synchronous LLM/embedder client for workers** — The `LLMProvider` ABC defined only `async` methods, but Celery tasks MUST be synchronous (`def`). Workers had no way to call vLLM or the embedder. The comment in §7.2.1 said "use httpx synchronous client" but none was provided. | §4 (new) | Added `worker/llm_sync.py` with `SyncLLMClient` and `SyncEmbeddingClient` using `httpx.Client` (synchronous). Supports both local vLLM and all cloud providers. |
| 43 | **`config.py` / Settings class never defined** — Multiple modules imported `from app.config import settings` but no Settings class existed. Every module referencing `settings.VLLM_URL`, `settings.LLM_PROVIDER`, etc. would crash with `ImportError` or `AttributeError`. | §4 (new) | Added complete `config.py` with `pydantic-settings` `BaseSettings` class covering all service URLs, LLM config, MinIO, JWT, CORS, and embedding dimension. |
| 44 | **Missing imports in auth dependency code** — `get_current_user` used `jwt`, `hashlib`, `datetime`, `select`, `text`, `User`, and `ApiKey` but the code snippet only imported `uuid` and FastAPI dependencies. Would crash on first authenticated request. | §6 Auth Deps | Consolidated all imports into `deps.py`: added `jwt`, `hashlib`, `datetime`, `select`, `text`, and model imports. |
| 45 | **`MINIO_URL` missing from service environments** — Workers and the API need to connect to MinIO for raw content storage, but no `MINIO_URL`, `MINIO_ACCESS_KEY`, or `MINIO_SECRET_KEY` appeared in any service environment or common anchor. Content storage would fail silently. | §5 Common Env | Added `MINIO_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, and `MINIO_BUCKET` to `x-common-env` and `.env.example`. |
| 46 | **Frontend `NEXT_PUBLIC_API_URL` hardcoded to `localhost`** — Users accessing the dashboard from another LAN machine would get connection refused. The browser would try to reach `http://localhost:8080` which doesn't exist on their machine. | §5 Frontend Service | Changed to `${NEXT_PUBLIC_API_URL:-http://localhost:8080}` so it can be overridden to the server's LAN IP. Same for `NEXT_PUBLIC_WS_URL`. |
| 47 | **Qdrant embedding dimension hardcoded to 1024** — Qdrant collection was created with `"size": 1024` (BGE-M3). Switching to cloud embeddings (OpenAI = 3072 dims) would cause silent vector rejection or dimension mismatch errors. | §8 Qdrant + §4 Settings | Added `EMBEDDING_DIMENSION` env var (default 1024). Qdrant collection creation reads `settings.EMBEDDING_DIMENSION`. Cloud compose override sets it to 3072. |

### Security & Design Gaps Fixed

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 48 | **`sentiment_history` UNIQUE constraint excluded `user_id`** — `UNIQUE(cluster_id, period_start)` allowed theoretical cross-user collisions if two users happened to have clusters with the same UUID (impossible in practice but inconsistent with the RLS model). | §8 Intelligence Tables | Changed to `UNIQUE(user_id, cluster_id, period_start)` for consistency. |
| 49 | **`CORS_ORIGIN` supported only a single origin** — LAN deployments need multiple CORS origins (localhost + LAN IP). The v3 implementation used a single string. | §4 Lifespan + §5 .env | Renamed to `CORS_ORIGINS` (plural). Supports comma-separated list. `main.py` splits and passes as `allow_origins` list. |
| 50 | **No `SEARXNG_URL` in common-env** — Only `worker-io` had `SEARXNG_URL`. The API service needed it for the sidebar status indicator and the `/health/services` endpoint. Other workers that might need search access couldn't reach SearXNG. | §5 Common Env | Moved `SEARXNG_URL` to `x-common-env` with configurable default. |
| 51 | **`db.commit()` inside `get_current_user` broke transaction boundary** — The auth function called `await db.commit()` to update API key `last_used_at`, but `get_db()` wraps the session in `session.begin()`. Calling `commit()` inside a context-managed transaction raises `InvalidRequestError`. | §6 Auth Deps | Removed explicit `await db.commit()`. The `last_used_at` mutation is committed by the `session.begin()` context manager when the request completes. |

### LAN Distribution (new feature)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 52 | **No support for distributed LAN deployment** — All service URLs were hardcoded to Docker service names (`http://vllm:8000`), making it impossible to run GPU services on a separate machine. | §2, §4, §5 | Added complete LAN architecture: env-var-driven URLs in common-env, `docker-compose.gpu-node.yml` for GPU machine, `docker-compose.search-node.yml` for search machine, `docker-compose.lan.yml` override for main server. All URLs default to Docker service names but accept LAN IPs. |
| 53 | **No extended retry for LAN startup** — Remote services may take longer to become available than colocated Docker services. Workers would fail immediately on first LLM call if the GPU machine was still loading models. | §4 HTTP Retry | Added `lan_startup_retry` configuration with 30 attempts and exponential backoff up to 60 seconds. Used by workers during initial connectivity. |
| 54 | **No service connectivity health check** — Operators had no way to verify that the API could reach vLLM, embedder, and SearXNG, especially critical in LAN deployments where network issues are common. | §6 Health | Added `/health/services` endpoint that probes all downstream services and reports connectivity status per-service. |
| 55 | **No LAN deployment documentation** — No guide for operators setting up multi-machine deployments. | §16, §18, docs/ | Added `lan-deployment.md` to docs, LAN architecture diagram in §2, deployment mode comments in `.env.example`, LAN-specific items in security checklist. |
| 56 | **GPU node compose exposed ports only on localhost** — The `docker-compose.gpu.yml` mapped ports as `["8100:8000"]` which by default binds to `0.0.0.0`, but the vLLM command didn't include `--host 0.0.0.0`. vLLM defaults to `127.0.0.1` which would reject connections from other machines. | §5 GPU Node | Added `--host 0.0.0.0` to vLLM command in `docker-compose.gpu-node.yml`. (The colocated `docker-compose.gpu.yml` doesn't need this since Docker networking handles it.) |
| 57 | **`worker-io` depended on local `searxng` even in LAN mode** — The base compose had `depends_on: searxng: { condition: service_healthy }` for `worker-io`. In LAN mode where SearXNG runs remotely, this dependency would prevent the worker from starting because the local service doesn't exist. | §5 LAN Override | The `docker-compose.lan.yml` override removes the `searxng` dependency and uses `profiles: ["disabled"]` to prevent local SearXNG from starting. Workers connect to remote SearXNG via `SEARXNG_URL` env var. |

### Makefile

```makefile
# Makefile — common TTwatch operations

.PHONY: dev prod gpu lan cloud stop logs backup restore migrate

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
```

### .gitignore

```gitignore
# Environment
.env
.env.local

# Models (downloaded separately)
models/
!models/.gitkeep

# Backups
backups/
!backups/.gitkeep

# Python
__pycache__/
*.pyc
*.egg-info/
.venv/
venv/

# Node
node_modules/
.next/
.turbo/

# Docker volumes
pgdata/
qdrant_data/
redis_data/
minio_data/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

### .dockerignore

```dockerignore
.git
.env
.env.*
models/
backups/
node_modules/
.next/
__pycache__
*.pyc
.vscode
.idea
docs/
*.md
!requirements.txt
```

---

*All v4 technical decisions carry forward. Additional v5 decisions:*

**Why f-string for SET LOCAL instead of bind parameters?** PostgreSQL's `SET` command does not accept parameterized inputs (`$1`). SQLAlchemy compiles `:uid` placeholders into `$1` for asyncpg, which crashes with a syntax error. The UUID is validated via `str(uuid.UUID(...))` round-trip before interpolation, guaranteeing only `[0-9a-f-]` characters — safe for f-string usage in this specific case.

**Why init_services.py instead of migration scripts for Qdrant/MinIO?** Qdrant collections and MinIO buckets are external service state, not database schema. They don't belong in Alembic migrations. An idempotent initialization function called during API startup handles creation cleanly and works for both fresh deployments and restarts.

**Why `run_in_executor` for `add_consumer`?** Celery's control commands use synchronous Redis connections internally. Calling them from an async FastAPI handler blocks the event loop. Offloading to a thread pool keeps the API responsive while still registering queues.

---

## Appendix D — v5 Change Log

This section summarizes the 22 issues identified during the v5 deep review.

### Critical Bugs Fixed (would crash/fail at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 58 | `SET LOCAL` with bind params crashes PostgreSQL | §4, §7.2.1 | Use f-string with UUID-validated value |
| 59 | JWT decode has no error handling → HTTP 500 | §6 | Added try/except for ExpiredSignatureError, InvalidTokenError |
| 60 | No Qdrant collection initialization | §4, §8 | Added `init_services.py` called during lifespan startup |
| 61 | `recluster_topic` missing decorators + no persistence | §7.5 | Added @app.task, @with_rls_context, full PostgreSQL write logic |
| 62 | Storage services missing restart policy | §5 | Added `restart: unless-stopped` to all 5 services |
| 63 | No embedder server.py implementation | §3 | Added complete FastAPI embedder with Dockerfile |
| 64 | Routers never included in FastAPI app | §4 | Added all router registrations to main.py |
| 65 | No Celery task routing configuration | §7.2 | Added `task_routes` and `task_default_queue` to celeryconfig |

### Security & Reliability Fixes

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 66 | `datetime.utcnow()` deprecated in Python 3.12+ | §6 | Changed to `datetime.now(timezone.utc)` |
| 67 | Pydantic v2 `class Config` deprecated | §4 | Changed to `model_config = SettingsConfigDict(...)` |
| 68 | `worker-cpu` missing minio dependency | §5 | Added minio to depends_on |
| 69 | `/health/services` incomplete | §6 | Added PostgreSQL, Redis, Qdrant checks |
| 70 | RateLimiter never instantiated | §6 | Added instantiation + FastAPI dependency |
| 71 | `lan_startup_retry` defined but unused | §4 | Applied in SyncLLMClient startup verification |

### Missing Implementations

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 72 | No SearXNG settings.yml | §5 | Added complete configuration file |
| 73 | Missing model imports in task files | §7 | Standardized imports in all task modules |
| 74 | Duplicate `parse_json_response` | §4 | Documented duplication with cross-reference comments |
| 75 | No Makefile provided | §16 | Added complete Makefile with all deployment targets |
| 76 | beat_schedule in wrong module | §7.3 | Moved to celeryconfig.py |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 77 | `add_consumer` blocks async event loop | §7.3 | Wrapped in `run_in_executor` |
| 78 | No .gitignore or .dockerignore | §16 | Added both files |

---

*All v5 technical decisions carry forward. Additional v6 decisions. v7 decisions in [Appendix F](#appendix-f--v7-change-log). v8 decisions in [Appendix G](#appendix-g--v8-change-log):*

**Why provide complete SQLAlchemy ORM models?** Every module in the system imports from `app.models` — deps.py, worker tasks, Alembic migrations, all routers. Without concrete model definitions, none of these imports resolve. The models mirror the SQL DDL exactly, with relationships enabling eager/lazy loading patterns used by the API.

**Why does the Worker Dockerfile copy `app/models`?** Worker tasks import from `app.models` (e.g., `from app.models import Article, Cluster`). Workers are a separate Docker service with their own Dockerfile. The simplest approach is copying the model package into the worker image. An alternative (shared pip package) adds complexity without benefit for a single-project monorepo.

**Why psycogreen for gevent workers?** Celery's gevent pool monkey-patches the socket module, but psycopg2's C extension uses libpq directly, bypassing Python sockets. `psycogreen` patches psycopg2 to use gevent-compatible waits, preventing the worker from blocking on database calls. Without this, `worker-io` with `--pool=gevent --concurrency=32` would serialize all DB operations.

**Why not use YAML anchors in override compose files?** YAML anchors (`&common-env` / `*common-env`) are a YAML spec feature resolved within a single file. Docker Compose's multi-file merge (`-f a.yml -f b.yml`) happens at the Compose level, after YAML parsing. Override files cannot reference anchors defined in the base file. Instead, overrides rely on Compose's deep merge behavior — the base file's `environment` block is inherited and only specified keys are overridden.

**Why a brace-finding fallback in `parse_json_response`?** LLMs occasionally emit preamble text ("Here is the JSON:") or trailing commentary after the JSON block. The v5 regex only handled markdown fences at string boundaries. Finding the first `{` and last `}` in the string handles all these edge cases robustly. This is safe because the function always expects a single JSON object (not arrays).

**Why include the scheduler in the cloud compose override?** The scheduler service dispatches periodic tasks that eventually call LLM providers. If the scheduler inherits `LLM_PROVIDER=local` from common-env while workers use `cloud`, tasks dispatched by the scheduler may carry incorrect provider assumptions. All services that participate in the LLM pipeline must agree on the provider configuration.

---

## Appendix E — v6 Change Log

This section summarizes the 17 issues identified during the v6 deep review.

### Critical Missing Implementations (would prevent building)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 79 | **No SQLAlchemy ORM models defined** — `from app.models import User, ApiKey, Cluster, Article, Base` referenced in deps.py, worker tasks, Alembic, all routers, but no model definitions existed. Every import would fail with `ImportError`. | §8 (new) | Added complete `models/` package with `base.py`, `user.py`, `intelligence.py`, `investment.py` covering all 22 tables. Includes relationships, constraints, and proper UUID/JSONB types. |
| 80 | **No API or Worker Dockerfiles** — `services/api/Dockerfile` and `services/worker/Dockerfile` referenced by all compose files but never provided. `docker compose build` would fail immediately. | §6 (new) | Added complete multi-stage Dockerfile for API (with `dev` target for hot-reload), worker Dockerfile (copies `app/models` for ORM access), and frontend Dockerfile (multi-stage Next.js build). |
| 81 | **No `requirements.txt` for API or Worker** — Only the embedder had dependency files. Services couldn't install their Python dependencies. | §6 (new) | Added `requirements.txt` for API (FastAPI, asyncpg, qdrant-client, etc.) and Worker (celery, gevent, hdbscan, trafilatura, psycogreen, etc.). |
| 82 | **No auth router implementation** — `from app.auth.router import router` imported in main.py but no code existed for register, login, or refresh endpoints. | §6 (new) | Added complete `auth/router.py` with `/auth/register`, `/auth/login`, `/auth/refresh` endpoints using argon2id hashing, JWT generation, and refresh token rotation. |
| 83 | **`cleanup_stale_market_data` missing imports** — Task used `app`, `db_session`, `text` without importing any of them. Would crash on first invocation. | §8 | Added `from sqlalchemy import text`, `from worker.celeryconfig import app`, `from worker.db import db_session`. |
| 84 | **No ingestion task implementation** — The plan referenced Trafilatura extraction and a detailed pipeline diagram but no `ingest.py` task existed. The core data pipeline was unimplemented. | §7.2.2 (new) | Added complete `ingest_article` task with Trafilatura extraction, 3-layer dedup (URL via Redis, content hash via PostgreSQL, semantic via Qdrant), MinIO content storage, and fan-out to downstream tasks. |

### Runtime Bugs

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 85 | **GPU compose services missing `restart: unless-stopped`** — Embedder and vLLM in `docker-compose.gpu.yml` would not restart after crashes or host reboots. | §5 | Added `restart: unless-stopped` to both services in colocated GPU compose. |
| 86 | **Cloud compose missing scheduler override** — Scheduler service inherited `LLM_PROVIDER=local` from common-env, causing task dispatch to assume wrong provider. | §5 | Added scheduler service override in `docker-compose.cloud.yml` with all cloud env vars. |
| 87 | **YAML anchor `*common-env` in dev compose override** — YAML anchors are per-file. `docker-compose.dev.yml` referenced `<<: *common-env` which is defined only in `docker-compose.yml`. Compose merge would fail with "anchor not found". | §5 | Removed YAML anchor from dev override. Only overrides dev-specific vars (DEBUG); base compose environment inherited via Compose deep merge. |
| 88 | **`parse_json_response` fragile regex** — Only handled markdown fences at string boundaries. Failed on LLM responses with preamble text or trailing commentary. | §4 | Rewrote parser: first tries markdown fence extraction via regex, then falls back to finding first `{` and last `}` brace positions. Handles bare JSON, fenced JSON, and JSON embedded in surrounding text. |

### Security & Reliability

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 89 | **gevent pool + psycopg2 incompatibility** — `worker-io` uses gevent pool (concurrency=32) but psycopg2's C extension bypasses gevent's monkey-patched sockets, serializing all DB calls. | §6 | Added `psycogreen` to worker requirements. Worker `db.py` calls `patch_psycopg()` before creating engine. Safe no-op in prefork workers. |
| 90 | **Queue discovery beat task too aggressive** — Ran every 15 seconds, executing Redis SCAN on every iteration. Unnecessary load for a slowly-changing queue set. | §7.2 | Changed schedule from 15 seconds to 120 seconds (2 minutes). |
| 91 | **Worker file structure missing auth router** — `app/auth/` listed in §9 project structure but no file content provided. | §6, §9 | Full implementation provided in §6. Updated §9 structure to reflect auth module contents. |
| 92 | **File/folder structure stale** — Did not reflect models package, auth router, or Dockerfile locations accurately. | §16 | Updated tree to include `models/` subpackage, `auth/router.py`, and descriptive comments. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 93 | **Technical decisions missing for v6 changes** — No rationale documented for ORM models, Dockerfiles, psycogreen, YAML anchor behavior, or JSON parser robustness. | §18 | Added 6 technical decision entries explaining each design choice. |
| 94 | **Security checklist incomplete for v6** — Missing items for ORM models, worker Dockerfile, psycogreen, auth router, cloud scheduler, and dev compose. | §17 | Added 10 new checklist items covering all v6 additions. |
| 95 | **No frontend Dockerfile** — Frontend compose service referenced `services/frontend/Dockerfile` but it was never provided. | §6 (new) | Added multi-stage Next.js Dockerfile (deps → build → production runner with standalone output). |

---

## Appendix F — v7 Change Log

This section summarizes the 13 issues identified during the v7 deep review.

### Critical Bugs (would crash or prevent building)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 96 | **`ingest_article` uses `bind=True` but `with_rls_context` doesn't handle `self`** — `@app.task(bind=True)` injects the task instance as the first positional argument (`self`). The `with_rls_context` wrapper captures the first arg as `user_id` and calls `uuid.UUID(user_id)`. Since `self` is a Celery Task object (not a UUID string), this crashes with `ValueError` on every invocation. This is critical because the task body uses `self.retry(exc=e, countdown=30)`, which requires `bind=True`. | §7.2.1 | Rewrote `with_rls_context` to detect bound tasks: if first positional arg is a Celery Task instance, shift it out before extracting `user_id`. Added explicit `bind` parameter to decorator for clarity. |
| 97 | **`summarize_article` accesses `article.raw_text` but no such column exists** — The Article ORM model stores raw content in MinIO via `raw_storage_key`. There is no `raw_text` column. The `summarize_article` task accesses `article.raw_text[:2000]` which raises `AttributeError` on every call. All downstream tasks (entity extraction, sentiment) would have the same problem if they follow this pattern. | §7.2 | Added `worker/tasks/utils.py` with `fetch_article_text()` helper that reads raw text from MinIO using the article's `raw_storage_key`. Updated `summarize_article` to use this helper. |
| 98 | **`embed_article` task imported but never defined** — `ingest_article` fans out to `embed_article.delay(user_id, article_id)` importing from `worker.tasks.embed`, but no `embed.py` module is provided anywhere in the plan. The import would fail with `ImportError`, breaking the entire ingestion pipeline. | §7.2 (new) | Added complete `embed_article` task with MinIO text fetch, embedding via `SyncEmbeddingClient`, and Qdrant upsert with user isolation payload. Uses `str(article.id)` as Qdrant point ID (required by clustering). |
| 99 | **`celery_app` referenced but never imported in `ensure_queue_consumed`** — The function calls `celery_app.control.add_consumer(...)` but the Celery app is imported as `app` (from `worker.celeryconfig`), not `celery_app`. Crashes with `NameError` on every search initiation. | §7.3 | Changed to proper import: `from worker.celeryconfig import app as celery_app`. |
| 100 | **Missing `__init__.py` for `worker/tasks/` package** — `celeryconfig.py` calls `app.autodiscover_tasks(["worker.tasks"])` which requires `worker/tasks/` to be a Python package. Without `__init__.py`, autodiscovery silently fails and no tasks are registered. The scheduler dispatches tasks that no worker recognizes. | §16 | Added `worker/tasks/__init__.py` to project structure. |

### Runtime Bugs

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 101 | **Redis connection created per-invocation in `ingest_article`** — The task body created a new `redis_lib.from_url()` connection on every call. Under load with gevent concurrency=32, this creates hundreds of connections, exhausting Redis' `maxclients` limit and causing cascading failures. | §7.2.2 | Moved Redis dedup client to module-level singleton `_dedup_redis`, matching the pattern used for `_embedder` and `_minio`. |
| 102 | **`meta` variable computed but never used for title extraction in `ingest_article`** — The code called `trafilatura.extract(downloaded, output_format="xmltei")` and assigned to `meta`, but then ignored it: `title = title or url.split("/")[-1][:200]`. The xmltei extraction was wasted computation and the title was never extracted from the document metadata. | §7.2.2 | Replaced with `trafilatura.extract_metadata(downloaded)` which returns a metadata object with `.title`. Falls back to URL slug only if metadata extraction also fails. |
| 103 | **`cleanup_stale_snapshots` in beat schedule but no implementation** — The beat schedule dispatches task `cleanup_stale_snapshots` daily at 03:30, but only `cleanup_stale_market_data` is implemented in `maintenance.py`. Workers receive the task but have no handler, logging `Received unregistered task` errors every day. | §7.2, §8 | Added `cleanup_stale_snapshots` task that removes old briefing and analysis snapshots beyond a configurable retention window. |
| 104 | **`users` router in project structure but never registered in `main.py`** — The project structure (§9) lists `users.py` as a router, but `main.py` never imports or includes it. User management endpoints (profile update, API key CRUD, quota checks) are unreachable. | §4, §9 | Added `users` router import and registration in `main.py`. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 105 | **No documented guarantee that `embed_article` uses article UUID as Qdrant point ID** — The `recluster_topic` task (§7.5) assumes `qdrant_point.id == article.id` when updating cluster assignments. This assumption was never validated and wasn't enforceable because `embed_article` didn't exist. | §7.2 (new), §7.5 | The new `embed_article` implementation explicitly uses `str(article.id)` as the Qdrant point UUID. Added a contract comment in `recluster_topic` documenting this dependency. |
| 106 | **No `fetch_article_text` helper for worker tasks that need article content** — Multiple downstream tasks (summarize, extract entities, sentiment) need to read the raw article text from MinIO. Each would need to duplicate MinIO fetch logic. No shared utility exists. | §7.2 (new) | Added `worker/tasks/utils.py` with `fetch_article_text(storage_key)` that reads from MinIO and returns the text string. All content-dependent tasks import from this shared module. |
| 107 | **Several periodic dispatch tasks referenced but not implemented** — Beat schedule references `schedule_trend_updates`, `schedule_briefings`, `schedule_coverage_gaps`, `schedule_sentiment_history`, `refresh_market_data`, `schedule_investment_analyses`. Only `schedule_reclustering` and `discover_queues` had implementations, causing "unregistered task" errors in Celery logs. | §7.2 | Added implementation stubs for all periodic dispatch tasks following the established `schedule_reclustering` pattern. |
| 108 | **Technical decisions missing for v7 changes** — No rationale documented for new fixes. | §18 | Added 5 technical decision entries explaining bind=True handling, shared fetch utility, module-level Redis, metadata extraction, and periodic task stubs. |

---

*All v6 technical decisions carry forward. v7 and v8 decisions are in §18.*

---

## Appendix G — v8 Change Log

This section summarizes the 13 issues identified during the v8 deep review.

### Critical Bugs (would crash in production)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 109 | **Worker role blocked by RLS on periodic dispatch tasks** — Periodic tasks like `schedule_reclustering` query the `topics` table (which has RLS enabled) without setting `ttwatch.current_user_id`. The worker role is not the table owner and has no `BYPASSRLS` privilege. PostgreSQL raises `ERROR: unrecognized configuration parameter "ttwatch.current_user_id"` or returns zero rows, preventing ALL periodic scheduling from functioning. | §8 RLS | Added `worker_bypass` RLS policies (`USING (true) WITH CHECK (true)`) on all 15 user-scoped tables for the `ttwatch_worker` role. This allows periodic dispatch tasks to enumerate users/topics while still enforcing RLS for the `ttwatch_app` role. |
| 110 | **`with_rls_context` uses `kwargs.get()` instead of `kwargs.pop()` for `user_id`** — If `user_id` is passed as a keyword argument, `get()` leaves it in kwargs. The wrapper then passes `user_id` as a positional argument AND in `**kwargs`, causing `TypeError: got multiple values for argument 'user_id'`. While Celery normally passes positionally, any non-standard invocation (e.g., `.apply(kwargs={"user_id": ...})`) would crash. | §7.2.1 | Changed `kwargs.get("user_id")` to `kwargs.pop("user_id", None)` in both code paths (bound and unbound). |
| 111 | **`get_current_user` passes string to `db.get(User, user_id)` without UUID conversion** — JWT `sub` claim is a string. `User.id` is `UUID(as_uuid=True)`. asyncpg may raise `DataError: invalid input syntax for type uuid` if the string isn't auto-coerced. | §6 Auth Deps | Added explicit `uuid.UUID(user_id)` conversion with try/except before `db.get()`. |

### Runtime Bugs (data loss or incorrect behavior)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 112 | **`recluster_topic` cascade-deletes `sentiment_history` every 2 hours** — Clusters are deleted and recreated every reclustering cycle. `sentiment_history.cluster_id` had `ON DELETE CASCADE`, destroying all historical sentiment data. `entity_cluster_map` also cascade-deleted. This made sentiment trend tracking impossible. | §7.5, §8 | Changed `sentiment_history.cluster_id` FK to `ON DELETE SET NULL` (nullable). Added explicit nullification of sentiment_history FKs and deletion of entity_cluster_map before cluster deletion in `recluster_topic`. |
| 113 | **No retry configuration on `embed_article` and `summarize_article`** — These tasks are fanned out from `ingest_article` and depend on external services. If the embedder or LLM is temporarily unavailable, tasks fail permanently with no retry. Articles remain unembedded and unsummarized. | §7.2 | Added `max_retries=3, default_retry_delay=30` to both task decorators. |
| 114 | **Alembic `env.py` missing offline mode support** — `run_migrations_online()` called unconditionally. `alembic upgrade head --sql` (offline mode for generating SQL scripts) didn't work, making it impossible to review migration SQL before applying. | §8 Alembic | Added `run_migrations_offline()` function and `context.is_offline_mode()` check. |
| 115 | **`init-db.sh` only grants `USAGE ON SCHEMA` but no table privileges** — The GRANT statements in §8 SQL are documentation-only — they're not in any init script or migration file. Both roles (`ttwatch_app`, `ttwatch_worker`) would get `permission denied` on ALL tables after Alembic creates them. | §5 init-db.sh | Added `ALTER DEFAULT PRIVILEGES` for both roles in init-db.sh, so all future tables created by Alembic are automatically accessible. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 116 | **Unused `SyncEmbeddingClient` import and initialization in `ingest.py`** — `_embedder = SyncEmbeddingClient()` initialized at module level but never used. Embedding is handled by the separate `embed_article` task. Wastes a cloud API connection or embedder HTTP client on import. `Topic` model also imported but unused. | §7.2.2 | Removed unused `SyncEmbeddingClient` import/init and `Topic` import from `ingest.py`. |
| 117 | **`embed_article` truncates body to 512 chars — too aggressive** — BGE-M3 supports up to 8192 tokens (~32K chars). 512 chars captures only the first paragraph, producing poor-quality embeddings for longer articles. Clustering quality degrades because semantically similar articles may have different openings. | §7.2 | Increased truncation to 1500 chars with documenting comment explaining the tradeoff. |
| 118 | **`cleanup_stale_market_data` SQL uses slow NOT IN subquery** — `NOT IN` with `DISTINCT ON` forces a full table scan of the subquery for each candidate row. On tables with millions of rows, this query could take minutes and lock the table. | §8 Maintenance | Rewrote using CTE pattern: CTE identifies rows to keep among old data, then DELETE excludes them. More efficient and limits subquery scope to old rows only. |
| 119 | **Technical decisions section duplicated** — §18 contained v7 decisions that were also in Appendix F, while missing rationale for v8 fixes. | §18 | Removed duplicated v7 decisions from §18. Added 8 new v8 technical decision entries. |
| 120 | **Security checklist missing v8 items** — No checklist coverage for worker bypass policies, kwargs safety, UUID coercion, sentiment FK change, retry configuration, or DEFAULT PRIVILEGES. | §17 | Added 12 new checklist items covering all v8 changes. |
| 121 | **`init-db.sh` missing DEFAULT PRIVILEGES for app role** — Only the worker role had `ALTER DEFAULT PRIVILEGES` (added in a previous version). The app role needed the same treatment for table access after migrations. | §5 init-db.sh | Added `ALTER DEFAULT PRIVILEGES` for `ttwatch_app` alongside the existing worker grants. |


---

## Appendix H — v9 Change Log

This section summarizes the 14 issues identified during the v9 deep review.

### Critical Bugs (missing implementations that break the intelligence pipeline)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 122 | **`extract_entities` task referenced in pipeline but never implemented** — The processing pipeline (§7.2) lists `extract_entities` as task #3, the `entities.py` module appears in the file structure, but no task code exists. The entity_article_map table stays permanently empty, making all entity-based features (entity tracking, entity-cluster mapping, entity search) non-functional. | §7.2 (new) | Added complete `extract_entities` task that uses LLM to extract named entities (person, org, product, location, event, technology), upserts Entity records, and creates EntityArticleMap join records. Includes retry configuration. |
| 123 | **`classify_sentiment` task referenced in pipeline but never implemented** — The processing pipeline lists `classify_sentiment` as task #4, but no implementation exists. The `article.sentiment_score` column is never populated, making all sentiment features (sentiment_history, sentiment trends, cluster sentiment) non-functional. Periodic `compute_sentiment_history` dispatches have no data to aggregate. | §7.2 (new) | Added complete `classify_sentiment` task that uses LLM to score article sentiment on a -1.0 to 1.0 scale. Clamps output to valid range. Includes retry configuration. |
| 124 | **`published_at` never extracted from article metadata** — The Article model has `published_at` (TIMESTAMPTZ), but `ingest_article` never sets it. The `trafilatura.extract_metadata()` call only extracts `.title`, ignoring `.date`. All temporal analysis (trend detection, briefing windows, article recency sorting) can only use `ingested_at`, which reflects processing time, not publication time. For RSS backfill, this can be hours or days off. | §7.2.2 | Extended metadata extraction to also parse `metadata.date` via `datetime.fromisoformat()`. Always attempts extraction regardless of whether title was provided. Falls back to None if date is unparseable. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 125 | **`ingest_article` only fans out to 2 of 4 processing tasks** — The fan-out code calls `summarize_article.delay()` and `embed_article.delay()` with a comment "extract_entities and classify_sentiment follow similar pattern" — but never actually calls them. 50% of the intelligence pipeline is silently skipped. | §7.2.2 | Added `extract_entities.delay(user_id, article_id)` and `classify_sentiment.delay(user_id, article_id)` to the fan-out, with corresponding imports. |
| 126 | **`recluster_topic` `article_count` inflated by orphaned Qdrant points** — Cluster `article_count` is set to `len(cluster_articles)` which counts Qdrant points. If PostgreSQL articles have been deleted but their Qdrant vectors remain (no Qdrant GC exists), the count includes non-existent articles. Dashboard displays show inflated numbers. | §7.5 | Changed to use `result.rowcount` from the PostgreSQL UPDATE to get the actual number of articles updated. Corrects `article_count` and `trend_score` if they differ from the Qdrant point count. |
| 127 | **Layer 3 semantic dedup (Qdrant cosine > 0.92) documented but never executed** — §7.4 describes three-layer dedup with Layer 3 being semantic similarity via Qdrant. But no code implements this check. Paraphrased or syndicated articles with different URLs and different content hashes pass through Layers 1 and 2 undetected, creating near-duplicate articles. | §7.2 (embed.py) | Added semantic dedup check to `embed_article`: after Qdrant upsert, searches for existing articles with cosine > 0.92 in the same user+topic scope. Marks the article as `is_duplicate=True` and sets `duplicate_of` if a near-duplicate is found. |
| 128 | **`cleanup_stale_snapshots` uses `NOT IN` with window function subquery** — Same performance anti-pattern that was fixed for `cleanup_stale_market_data` in v8 (issue #118). On tables with many briefings, the `NOT IN` forces a full scan. | §8 Maintenance | Rewrote using CTE pattern with an additional safety guard (`AND generated_at < now() - interval '7 days'`) to prevent accidentally deleting recent briefings during the CTE evaluation. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 129 | **`source_url` never populated on Article records** — The Article model has `source_url` (source homepage), but `ingest_article` doesn't accept or set it. The field is always NULL. Source attribution in the dashboard is incomplete. | §7.2.2 | Added `source_url` parameter to `ingest_article` signature. Stores it on the Article record. Callers can pass the source's homepage URL. |
| 130 | **WebSocket referenced in architecture but never implemented** — §2 architecture mentions "WebSocket" in the API Gateway box. `docker-compose.yml` configures `NEXT_PUBLIC_WS_URL`. But no WebSocket endpoint exists in `main.py` or any router. Real-time dashboard updates are impossible without it. | §4 main.py | Added WebSocket endpoint at `/ws` with JWT authentication, ConnectionManager for per-user connections, and send_json interface for pushing real-time events. |
| 131 | **`fetch_market_data` task dispatched but never implemented** — `refresh_market_data` periodic task dispatches `app.send_task("fetch_market_data", args=[symbol])` for each watched symbol. No handler exists. Workers log "Received unregistered task" errors hourly. Market data cache stays empty. | §8 Maintenance | Added complete `fetch_market_data` task with yfinance (equities/ETFs) and CoinGecko (crypto) support. Stores results in `market_data_cache`. Added `yfinance` to worker `requirements.txt`. |
| 132 | **No Qdrant garbage collection** — PostgreSQL CASCADE deletes don't propagate to Qdrant. Deleting topics or articles leaves orphaned vectors. Over time: wasted storage, inflated cluster counts (mitigated by fix #126 but not eliminated), and degraded search quality from stale vectors. | §8 Maintenance | Added `cleanup_orphaned_qdrant_points` daily task that scrolls all Qdrant points, batch-checks existence in PostgreSQL, and removes orphans. Added to beat schedule at 04:00. |
| 133 | **Queue discovery scans for nonexistent Redis key patterns** — `discover_and_register_queues` scans for `ttwatch:scheduled:*` and `ttwatch:tasks:*`, but no code creates keys matching these patterns. Only `ttwatch:priority:{user_id}` is a real pattern. Wasted SCAN cycles. | §7.3 | Removed phantom patterns. Now scans only `ttwatch:priority:*`. |
| 134 | **Technical decisions and security checklist missing v9 coverage** — No rationale documented for new task implementations, semantic dedup approach, Qdrant GC strategy, or WebSocket design. | §17, §18 | Added 9 technical decision entries and 16 security checklist items covering all v9 changes. |
| 135 | **Processing pipeline table missing `cleanup_orphaned_qdrant_points` entry** — Beat schedule references the task but the §7.2 periodic task table doesn't list it. | §7.2 | Added entry #20 for `cleanup_orphaned_qdrant_points` (daily, I/O queue). |

---

*All v8 technical decisions carry forward. v9 decisions are in §18.*

---

## Appendix I — v10 Change Log

This section summarizes the 15 issues identified during the v10 deep review.

### Critical Bugs (will crash at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 136 | **`maintenance.py` missing `logging` import and `logger` definition** — Both `fetch_market_data` and `cleanup_orphaned_qdrant_points` call `logger.info()` and `logger.warning()`, but the module only imports `from sqlalchemy import text`. No `import logging` or `logger = logging.getLogger(__name__)` exists. Both tasks crash with `NameError: name 'logger' is not defined` on every invocation. Market data caching and Qdrant garbage collection silently fail. | §8 Maintenance | Added `import logging` and `logger = logging.getLogger(__name__)` to module level of `maintenance.py`. |
| 137 | **`fetch_market_data` missing `select` import** — The task calls `session.execute(select(TickerReference).where(...))` but `select` is not imported at the module level (only `text` is imported from sqlalchemy). Crashes with `NameError: name 'select' is not defined` every time the hourly market refresh runs. | §8 Maintenance | Added `select` to the module-level `from sqlalchemy import text, select` import. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 138 | **5 core intelligence tasks dispatched but never implemented** — The periodic dispatch tasks (`schedule_briefings`, `schedule_trend_updates`, `schedule_coverage_gaps`, `schedule_sentiment_history`, `schedule_investment_analyses`) enumerate user/topic pairs and dispatch work tasks by name. But the actual worker tasks (`generate_briefing`, `update_trends`, `compute_sentiment_history`, `detect_coverage_gaps`, `generate_investment_analyses`) have no implementations. Workers log `Received unregistered task` errors every 2 hours for every user/topic pair. All briefing, trend, sentiment aggregation, coverage gap, and investment analysis features are non-functional. This is the same class of bug fixed in v7 (#103, #107) and v9 (#131). | §7.6 (new) | Added complete implementations for all 5 tasks: `generate_briefing` (hierarchical summarization from cluster summaries), `update_trends` (24h/48h article count comparison for velocity), `compute_sentiment_history` (daily per-cluster aggregation), `detect_coverage_gaps` (LLM analysis of uncovered areas), `generate_investment_analyses` (LLM + market data analysis per resolved ticker). |
| 139 | **`asyncio.get_event_loop()` deprecated in Python 3.12** — `ensure_queue_consumed` uses `asyncio.get_event_loop()` which is deprecated in Python 3.10+ and emits `DeprecationWarning` in Python 3.12. In Python 3.14 it will be removed entirely. | §7.3 | Changed to `asyncio.get_running_loop()`, which is the correct replacement for async contexts. |
| 140 | **`ConnectionManager.connect()` dead code — websocket_endpoint bypasses it** — The `ConnectionManager` class defines a `connect()` method that accepts the WebSocket and adds it to the connections dict. But `websocket_endpoint` calls `await websocket.accept()` directly and then manipulates `ws_manager.connections` dict directly via `setdefault()`, bypassing the `connect()` method entirely. The `connect()` method also calls `accept()`, so calling both would have caused a double-accept error. | §4 main.py | Removed `accept()` from `ConnectionManager.connect()` (accept is done by the endpoint before auth). Changed websocket_endpoint to call `await ws_manager.connect(user_id, websocket)` instead of direct dict manipulation. |
| 141 | **Unused `CorrelationSignal` import in `recluster_topic`** — `from app.models import SentimentHistory, EntityClusterMap, CorrelationSignal` imports `CorrelationSignal` but it's never used in the function body. Wastes import time and is misleading. `correlation_signals.cluster_id` has `ON DELETE SET NULL` which PostgreSQL handles automatically. | §7.5 | Removed `CorrelationSignal` from the import statement. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 142 | **No search router implementation** — `search.py` is registered in `main.py` and listed in the project structure, but no implementation is provided. The core semantic search feature — querying articles by natural language via Qdrant vector similarity — is unreachable. | §9 (new) | Added complete search router with `POST /api/search` endpoint. Embeds query text, searches Qdrant with user+topic filters, batch-fetches full Article records from PostgreSQL, returns results with similarity scores. |
| 143 | **No Pydantic response schemas defined** — The `schemas/` directory is referenced in the project structure but no schema files exist. API routers cannot properly serialize responses or validate requests. FastAPI's automatic OpenAPI documentation is incomplete. | §9 (new) | Added `schemas/topics.py` with `TopicCreate`, `TopicResponse`, `ClusterResponse`, `ArticleResponse`, `BriefingResponse`, `SearchRequest`, and `SearchResult` Pydantic models. All use `from_attributes=True` for ORM serialization. |
| 144 | **No logout endpoint** — The auth system provides `/auth/login` and `/auth/refresh` but no `/auth/logout`. Users cannot invalidate refresh tokens. A compromised refresh token remains valid for up to 30 days with no way to revoke it. | §6 Auth Router | Added `POST /auth/logout` endpoint that accepts a refresh token and deletes it from the database. Returns 200 regardless of token existence to prevent enumeration. |
| 145 | **No password strength validation on registration** — The `RegisterRequest` model accepts any string as a password, including empty strings. Users can register with `password: "a"` which undermines the entire auth security model despite using argon2id hashing. | §6 Auth Router | Added `validate_password` validator to `RegisterRequest` requiring minimum 10 characters, mixed case, and at least one digit. |
| 146 | **`update_trends` task routes to wrong queue** — The task is CPU-bound (SQL aggregation, no LLM calls) and should route to `ttwatch:compute`. But the `task_routes` config only maps `recluster_topic` and `update_trends` to compute. Wait — actually `update_trends` IS in task_routes. However, the task decorator should also specify `queue="ttwatch:compute"` for clarity. | §7.6, §7.2 | Added explicit `queue="ttwatch:compute"` to the `@app.task` decorator on `update_trends` for consistency with `recluster_topic`. |
| 147 | **Processing pipeline table missing new task files** — §7.2 file structure doesn't list `trends.py`, `sentiment_agg.py`, `coverage_gaps.py`, or `investment_analysis.py` even though these tasks are now implemented. | §16 | Updated file structure to include all new task files with descriptive comments. |
| 148 | **Security checklist missing v10 items** — No coverage for maintenance.py imports, task implementations, logout, password validation, search router, or deprecated asyncio usage. | §17 | Added 14 new security checklist items covering all v10 changes. |
| 149 | **Technical decisions missing v10 coverage** — No rationale documented for maintenance.py fixes, task implementation approach, asyncio deprecation, logout design, password validation policy, or search router architecture. | §18 | Added 7 new technical decision entries covering all v10 design choices. |
| 150 | **Auth router in `main.py` missing logout endpoint documentation** — The `auth_router` import in `main.py` doesn't reflect the new `/auth/logout` endpoint. While FastAPI auto-discovers routes from the router, the project structure comment should note the endpoint. | §4, §9 | Updated auth router comments to include logout alongside register, login, and refresh. |

---

*All v9 technical decisions carry forward. v10 decisions are in §18.*

---

## Appendix J — v11 Change Log

This section summarizes the 13 issues identified during the v11 deep review.

### Critical Bugs (will crash or silently fail at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 151 | **`validate_password` never executes — missing `@field_validator` decorator** — The `RegisterRequest` model defines `validate_password` as a `@classmethod` but lacks the Pydantic v2 `@field_validator("password")` decorator. Without it, Pydantic never calls the method during validation. Users can register with any password including empty strings, completely bypassing the security validation added in v10 (#145). The v10 changelog claims this was fixed, but the implementation is incorrect. | §6 Auth Router | Added `@field_validator("password")` decorator above `@classmethod`. Added `field_validator` to the pydantic import statement. |
| 152 | **Search router blocks the async event loop with synchronous `QdrantClient`** — `semantic_search` is an `async def` FastAPI handler but calls `qdrant.search()` via the synchronous `QdrantClient`. This blocks the entire event loop for the duration of the Qdrant network call. Under concurrent load, all other HTTP requests are stalled. Combined with per-request client instantiation (#153), this makes search both slow and resource-exhausting. | §9 Search Router | Replaced `QdrantClient` with `AsyncQdrantClient`. Changed `qdrant.search()` to `await qdrant.search()`. Added `aiohttp>=3.9.0` to API requirements (async transport dependency). |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 153 | **Search router creates a new QdrantClient on every request** — Each call to `semantic_search` instantiates `QdrantClient(url=settings.QDRANT_URL, timeout=30)`, establishing a new TCP connection. Under concurrent search traffic, this exhausts connection pools and file descriptors. Other Qdrant users in the codebase (workers, init_services) use module-level clients. | §9 Search Router | Added module-level `_qdrant: AsyncQdrantClient` with lazy initialization via `get_qdrant_client()`. Shared across all requests. |
| 154 | **3 investment pipeline tasks listed but never implemented** — The processing pipeline table (§7.2) lists `resolve_entity_ticker` (#7), `detect_correlation_signals` (#10), and `check_price_alerts` (#11) as tasks. The file structure lists `investment.py` as "stub". No implementations exist. `resolve_entity_ticker` is particularly critical — without it, no entities are mapped to tickers, so `generate_investment_analyses` (v10 #138) has no data to analyze. | §7.6 (new) | Added complete implementations: `resolve_entity_ticker` (two-step reference lookup + LLM inference), `detect_correlation_signals` (sentiment-price divergence detection), `check_price_alerts` (threshold comparison against cached market data). |
| 155 | **`check_price_alerts` not in beat schedule** — The task was listed in the pipeline table but not added to the Celery beat schedule in `celeryconfig.py`. Without a beat entry, the task never runs automatically. Price alerts would never trigger. | §7.2 Celery Config | Added `check-price-alerts` entry to beat schedule running every 15 minutes. |
| 156 | **WebSocket has no heartbeat — connections silently die behind proxies** — The WebSocket endpoint (`/ws`) has no ping/pong mechanism. Load balancers (nginx, AWS ALB) and firewalls commonly close idle WebSocket connections after 30-60 seconds. Users see their dashboard stop updating with no error indication. The server retains dead connections in `ConnectionManager`, wasting memory and causing failed `send_json` calls. | §4 main.py | Added heartbeat: server sends `{"type": "ping"}` every 30s. Dead connections (no pong for 90s) are terminated. Auth message now has 10s timeout. |
| 157 | **WebSocket auth `receive_json()` has no timeout — dangling connections** — A malicious or buggy client could open a WebSocket and never send the auth message, holding the connection indefinitely. With enough such connections, the server runs out of file descriptors. | §4 main.py | Wrapped auth `receive_json()` in `asyncio.wait_for(..., timeout=10.0)`. Connections that don't authenticate within 10s are closed. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 158 | **No topics CRUD router implementation** — The document registers `topics.router` in `main.py` and defines the `TopicCreate`/`TopicResponse` Pydantic schemas, but provides no router implementation. Users cannot create, list, update, or delete topics — the platform's most fundamental operation is unreachable. The document claims to be "self-contained" but relies on implicit "carry forward from v3" for critical routers. | §9 (new) | Added complete topics router with `GET /api/topics` (list), `POST /api/topics` (create with limit enforcement), `GET /api/topics/{id}`, `PUT /api/topics/{id}`, `DELETE /api/topics/{id}`, and `GET /api/topics/{id}/clusters`. |
| 159 | **Qdrant dimension mismatch not validated on startup** — `init_qdrant()` creates the collection if missing but never validates the vector dimension if it already exists. Switching from local BGE-M3 (1024-dim) to cloud OpenAI (3072-dim) without recreating the collection causes silent failures: searches return random results, semantic dedup stops working, clustering produces noise. No error or warning is logged. | §4 init_services.py | Added dimension validation: `init_qdrant()` now fetches the existing collection's dimension and raises `RuntimeError` with clear instructions if it doesn't match `EMBEDDING_DIMENSION`. |
| 160 | **Processing pipeline table missing `check_price_alerts` entry** — The task was listed as pipeline task #11 in the investment tasks table but missing from the periodic aggregate tasks table, making it unclear when it runs. | §7.2 | Added entry #21 for `check_price_alerts` (every 15 min, I/O queue). |
| 161 | **File structure lists `investment.py` as "stub"** — After v11 added the full `resolve_entity_ticker` implementation, the file structure comment still said "stub". Additionally, `correlation_signals.py` and `price_alerts.py` were not listed. | §16 | Updated file structure to include all three new task files with accurate descriptions. |
| 162 | **Security checklist missing v11 items** — No coverage for async Qdrant client, field_validator fix, WebSocket heartbeat, dimension validation, topics router, or investment pipeline tasks. | §17 | Added 13 new security checklist items covering all v11 changes. |
| 163 | **Technical decisions missing v11 coverage** — No rationale documented for field_validator fix, async Qdrant switch, heartbeat design, dimension validation, topics router, or investment task implementation approaches. | §18 | Added 8 new technical decision entries covering all v11 design choices. |

---

*All v10 technical decisions carry forward. v11 decisions are in §18.*

---

*End of v12 build plan.*
---

## Appendix K — v12 Change Log

This section summarizes the 12 issues identified during the v12 deep review.

### Critical Bugs (will crash or silently fail at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 164 | **`init_all()` silently swallows `RuntimeError` from Qdrant dimension check** — v11 fix #159 added dimension validation in `init_qdrant()` that raises `RuntimeError` on mismatch. But `init_all()` wraps it in `try/except Exception` and just logs. The application starts and serves requests with wrong-dimension vectors, producing meaningless search results. The entire purpose of the dimension check is defeated. | §4 init_services.py | `init_all()` now catches `RuntimeError` separately and re-raises it. Only non-critical initialization errors (e.g., Qdrant temporarily unreachable) are logged and swallowed. |
| 165 | **`resolve_entity_ticker` is implemented but never dispatched — investment pipeline is dead** — v11 fix #154 added the complete implementation, but no code ever calls `.delay()` on it. It's not in the `ingest_article` fan-out, not in the beat schedule, and has no dispatch task in `periodic.py`. Without execution, no entities ever map to tickers, so `generate_investment_analyses` has no `AssetMapping` records to analyze. The entire investment analysis pipeline produces nothing. | §7.2, §7.6 | Added fan-out from `extract_entities`: when a NEW entity is created (not previously existing), `resolve_entity_ticker.delay()` is dispatched immediately. This is more efficient than a periodic sweep since it runs only for genuinely new entities. |
| 166 | **Missing `email-validator` package in API requirements** — `RegisterRequest` uses `EmailStr` from pydantic which requires the `email-validator` package as an optional dependency. It's not listed in `services/api/requirements.txt`. Registration endpoint crashes with `ImportError: email-validator is not installed`. | §6 API Requirements | Added `email-validator>=2.0.0` to `services/api/requirements.txt`. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 167 | **`detect_correlation_signals` implemented but never dispatched** — v11 fix #154 added the implementation, but unlike `check_price_alerts` which got a beat schedule entry (#155), correlation signal detection has no trigger. No beat entry, no dispatch task. Signals are never detected. | §7.2, periodic.py | Added `schedule_correlation_signals` dispatch task to `periodic.py` and `schedule-correlation-signals` entry to beat schedule (every 4 hours). Also added pipeline table entry #22. |
| 168 | **`SyncEmbeddingClient` has no LAN startup retry** — `SyncLLMClient` has `_lan_startup_retry` with 30 attempts and exponential backoff for remote GPU startup. `SyncEmbeddingClient` has no retry logic at all. In LAN deployments, if the remote embedder takes 30-60 seconds to start, the first `embed()` call fails permanently with `ConnectionError`. | §4 llm_sync.py | Added `_verified` flag and `_verify_connectivity()` method with `_lan_startup_retry` to `SyncEmbeddingClient`, mirroring `SyncLLMClient`'s pattern. First call checks embedder health endpoint with retry backoff. |
| 169 | **`TopicCreate` schema reused for PUT endpoint — no partial updates** — `update_topic` accepts `TopicCreate` as its request body, which requires `name` (non-optional). Users cannot update just the refresh interval without resending the full topic name, icon, and config. Standard REST practice requires separate create and update schemas. | §9 Topics Router, Schemas | Added `TopicUpdate` schema with all-optional fields. `update_topic` now applies only provided fields, enabling partial updates. |
| 170 | **Router path parameters accept raw `str` instead of validated `uuid.UUID`** — `get_topic`, `update_topic`, `delete_topic`, and `list_clusters` all accept `topic_id: str`. FastAPI doesn't validate UUID format for string parameters, so malformed IDs like `topic_id=abc` pass validation and cause a database `DataError` instead of a clean 422 response. | §9 Topics Router | Changed all `topic_id: str` parameters to `topic_id: uuid.UUID`. Added `import uuid` to the router module. FastAPI now returns 422 with clear error for invalid UUIDs. |
| 171 | **`refresh_market_data` only discovers symbols from `watchlist_items`** — The periodic task queries `WatchlistItem.symbol` for market data refresh. But `AssetMapping.resolved_symbol` also contains symbols that need market data (for `generate_investment_analyses` and `detect_correlation_signals`). Symbols auto-resolved via entity-ticker mapping but not on any user's watchlist never get their market data refreshed. Investment analyses for these symbols have stale or missing market data. | §7.2 periodic.py | `refresh_market_data` now unions symbols from both `WatchlistItem` and `AssetMapping` tables before dispatching `fetch_market_data` tasks. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 172 | **`parse_json_response` still duplicated despite v10 acknowledging it** — The identical function exists in both `llm_sync.py` and `llm_utils.py` with a comment "change both files". The worker Dockerfile already copies `services/api/app` to `/app/app`, meaning the worker CAN import from `app.services.llm_utils`. The duplication is a maintenance trap — a logic change in one file that misses the other causes inconsistent JSON parsing between API and worker. | §4 llm_sync.py, llm_utils.py | `llm_sync.py` now imports `parse_json_response` from `app.services.llm_utils` at module level (with fallback to inline copy if import fails). `llm_utils.py` is the canonical implementation. Inline copy retained as documentation and fallback. |
| 173 | **File structure says `investment.py` but code says `resolve_ticker.py`** — §16 file structure lists `worker/tasks/investment.py` for `resolve_entity_ticker`, but the actual code block header (§7.6) says `worker/tasks/resolve_ticker.py`. Developers don't know which filename to use. | §16 | Updated file structure to `resolve_ticker.py` matching the code block header. |
| 174 | **Unused `httpx` import in `ingest.py`** — `import httpx` at module level but never used in the task body. Trafilatura handles all HTTP fetching. Wastes import time and is misleading. | §7.2.2 | Removed `import httpx` from `ingest.py`. |
| 175 | **Processing pipeline table missing `schedule_correlation_signals`** — `detect_correlation_signals` appears in the investment tasks table (#10) but the periodic aggregate tasks table had no corresponding dispatch entry. | §7.2 | Added entry #22 for `schedule_correlation_signals` (every 4h, I/O queue). |

---

### v12 Technical Decisions

**Why re-raise `RuntimeError` but not other exceptions in `init_all()`?** `RuntimeError` from dimension validation is a _configuration error_ — the system fundamentally cannot work correctly. Logging it and continuing lets the API serve corrupt results. Other exceptions (e.g., Qdrant temporarily unreachable during startup) are transient — services may become available after the API starts. The distinction between "misconfigured" and "temporarily unavailable" is critical for operational reliability.

**Why dispatch `resolve_entity_ticker` from `extract_entities` instead of a periodic beat task?** Entity creation happens in real-time during article ingestion. A periodic sweep would need to track which entities have already been resolved, adding complexity. The fan-out pattern (dispatch only for NEW entities, not pre-existing ones) ensures immediate resolution without redundant LLM calls. It follows the same fan-out pattern already used by `ingest_article` → `summarize/embed/entities/sentiment`.

**Why `uuid.UUID` for path parameters instead of `str`?** FastAPI validates `uuid.UUID` path parameters before the handler runs. Invalid UUIDs return a clean 422 response with a descriptive error. With `str`, invalid UUIDs pass validation and cause SQLAlchemy `DataError` or PostgreSQL cast errors, which surface as ugly 500 responses. Type-safe parameters are both more correct and produce better error messages.

**Why `TopicUpdate` with all-optional fields instead of reusing `TopicCreate`?** Standard REST semantics distinguish between POST (create, all required fields) and PUT/PATCH (update, only changed fields). Using `TopicCreate` for updates forces clients to re-send unchanged fields, risking accidental overwrites if the client has stale data. `TopicUpdate` with optional fields enables true partial updates. The handler applies only fields that are explicitly provided (not `None`).

**Why union `WatchlistItem` and `AssetMapping` symbols for market data refresh?** `generate_investment_analyses` reads market data for symbols in `AssetMapping`. If those symbols aren't also in someone's watchlist, their market data is never refreshed. The union ensures all symbols with downstream consumers get fresh data. The set deduplication prevents double-fetching symbols that appear in both tables.

**Why `SyncEmbeddingClient` needs the same startup retry as `SyncLLMClient`?** In LAN deployments, the embedder may take 30-60 seconds to load the BGE-M3 model into GPU VRAM. The first worker task to call `embed()` would fail without retry. This is the same startup race condition that `SyncLLMClient._verify_connectivity()` solves for vLLM. The embedder's `/health` endpoint returns 503 until the model is loaded, making it a clean retry target.

**Why import `parse_json_response` from the shared module with fallback?** The worker Dockerfile copies `services/api/app` to `/app/app`, making `app.services.llm_utils` available at runtime. Importing from the shared module ensures a single source of truth for JSON parsing logic. The inline fallback copy prevents build failures if the import path changes, and serves as documentation. The `try/except ImportError` pattern is standard Python practice for optional dependencies.

**Why every 4 hours for correlation signal detection?** Correlation signals compare 48-hour sentiment windows against price changes. Running more frequently than 4 hours adds LLM costs without meaningful signal improvement (sentiment averages change slowly). Running less frequently risks missing divergence signals before they resolve. 4 hours provides 6 checks per 48-hour window — sufficient to catch emerging patterns.

---

*All v11 technical decisions carry forward. v12 decisions are in §18.*

---

## Appendix L — v13 Change Log

This section summarizes the 10 issues identified during the v13 deep review.

### Critical Bugs (will crash or silently fail at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 176 | **`register` endpoint race condition on concurrent duplicate email** — Two concurrent registration requests with the same email both pass the SELECT check, but the second INSERT violates the UNIQUE constraint on `users.email`. The resulting `IntegrityError` (PostgreSQL error code 23505) is unhandled and surfaces as an HTTP 500 internal server error instead of a clean 409 conflict response. This is a classic check-then-act race condition. | §6 Auth Router | Added `try/except` around `db.flush()` that catches `IntegrityError` (detected via error string matching for "unique", "duplicate", or PostgreSQL code "23505") and raises `HTTPException(409)`. The initial SELECT check remains as an optimization for the non-concurrent case. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 177 | **`check_price_alerts` — `crosses_above` and `crosses_below` conditions identical to `above` and `below`** — The conditions `crosses_above` (`price >= threshold`) and `crosses_below` (`price <= threshold`) have the same logic as `above` and `below`. "Crosses" semantics require knowing whether the price was previously on the other side of the threshold. Without historical state tracking, these conditions trigger on every check where the price is beyond the threshold, not only when it crosses the boundary. Effectively, users who set "crosses" alerts get the same behavior as simple "above"/"below" alerts. | §7.6 price_alerts.py, §8 SQL schema, ORM | Added `last_known_price NUMERIC` column to `price_alerts` table and ORM model. `check_price_alerts` now compares `last_known_price` against the threshold to determine whether the price actually crossed. Updates `last_known_price` on every check for "crosses" conditions. Only triggers when the price transitions from one side to the other. |
| 178 | **`compute_sentiment_history` uses `date.today()` (local timezone)** — `today = date.today()` returns the server's local date, which may differ from UTC. All other timestamps use `datetime.now(timezone.utc)`. On a server in UTC-8, articles ingested between 16:00-23:59 UTC would have `ingested_at` on one UTC day but be aggregated under a different local `period_start` date. This creates inconsistent date boundaries in sentiment history charts. | §7.6 sentiment_agg.py | Changed to `datetime.now(timezone.utc).date()` for consistent UTC date boundaries matching `ingested_at` timestamps. |
| 179 | **`fetch_market_data` crashes on duplicate insert within same hour** — `market_data_cache` has a UNIQUE index on `(symbol, date_trunc('hour', fetched_at))` for deduplication. The task uses `session.add(MarketDataCache(...))` which performs a plain INSERT. When `refresh_market_data` dispatches the same symbol twice within one hour (e.g., symbol appears in both `watchlist_items` and `asset_mappings`), the second INSERT violates the unique constraint and crashes with `IntegrityError`. The hourly beat schedule ensures this happens regularly. | §8 Maintenance | Replaced ORM `session.add()` with raw SQL `INSERT ... ON CONFLICT DO UPDATE` that updates the existing row when the dedup index conflicts. Also populates all available yfinance fields (volume, PE ratio, EPS, dividend yield, beta, 52-week high/low) that were previously discarded. |
| 180 | **`fetch_market_data` crypto path uses ticker symbol as CoinGecko ID** — The CoinGecko API parameter `ids` expects CoinGecko's own identifiers (e.g., `"bitcoin"`, `"ethereum"`, `"solana"`), not exchange symbols (e.g., `"BTC"`, `"ETH"`, `"SOL"`). The code uses `symbol.lower()` which converts `"BTC"` to `"btc"` — CoinGecko does not recognize `"btc"` as a valid ID. The API returns an empty JSON object `{}` and `.get("btc", {})` returns empty dict, so `price` is `None` and no data is cached. All crypto market data is silently missing. | §8 Maintenance | Added CoinGecko ID lookup from `ticker_reference.metadata` (key: `coingecko_id`). Falls back to `symbol.lower()` which works for some coins (e.g., "ethereum", "solana") but not ticker-style symbols. The ticker_reference seeding process should populate `coingecko_id` in metadata for crypto assets. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 181 | **RLS `user_isolation` policies apply to all roles (PUBLIC) instead of `ttwatch_app` only** — The `CREATE POLICY user_isolation ON ... FOR ALL` statements don't specify a target role, so they apply to all roles including `ttwatch_worker`. When periodic dispatch tasks (e.g., `schedule_reclustering`) query tables without setting the `ttwatch.current_user_id` GUC, `current_setting('ttwatch.current_user_id')` returns `''` (empty string for unset custom GUC). Casting `''::UUID` raises `ERROR: invalid input syntax for type uuid`. While PostgreSQL's query planner typically constant-folds `(expr OR true)` to `true` (making the `worker_bypass` policy short-circuit the evaluation), this relies on optimizer behavior rather than guaranteed semantics. A future PostgreSQL version or complex query plan could evaluate both policies, causing a crash. | §8 RLS | Added `TO ttwatch_app` to all 15 `user_isolation` policies. Now only `ttwatch_app` has the user-scoping policy; `ttwatch_worker` only has the `worker_bypass` policy. The GUC cast is never attempted for the worker role. |
| 182 | **`TopicUpdate` cannot distinguish "not provided" from "explicitly null"** — The `update_topic` handler checks `if req.icon is not None:` to decide whether to update fields. When a client sends `{"icon": null}` intending to clear the icon, `req.icon` is `None` — same as when `icon` is omitted entirely. Users cannot clear optional fields to null via the API. | §9 Topics Router | Changed to use Pydantic's `model_fields_set` attribute which contains the set of field names the client actually included in the request body. The handler now iterates over `model_fields_set` and applies `setattr()` for only those fields, correctly handling both "not provided" (skip) and "explicitly null" (set to None). |
| 183 | **`fetch_market_data` discards available yfinance fields** — The task only extracts `currentPrice`, `regularMarketChangePercent`, and `marketCap` from yfinance's `ticker.info` dict. Fields like `regularMarketVolume`, `trailingPE`, `trailingEps`, `dividendYield`, `beta`, `fiftyTwoWeekHigh`, and `fiftyTwoWeekLow` are available but ignored. The `market_data_cache` table has columns for all of these. Investment analyses and correlation signals operate on incomplete market data. | §8 Maintenance | Extended the yfinance extraction to populate all available fields from `ticker.info`. The ON CONFLICT upsert (fix #179) handles all columns in a single statement. |
| 184 | **Security checklist missing v13 items** — No coverage for RLS role restriction, register race condition, price alert state tracking, UTC date boundaries, market data upsert, or TopicUpdate field semantics. | §17 | Added 10 new security checklist items covering all v13 changes. |
| 185 | **Technical decisions not updated for v13** — No rationale documented for RLS role restriction approach, register error handling strategy, crosses condition state machine, UTC date choice, ON CONFLICT vs ORM upsert, CoinGecko ID mapping, or model_fields_set pattern. | §18 | Added 7 new technical decision entries below. |

---

### v13 Technical Decisions

**Why restrict `user_isolation` policies to `TO ttwatch_app` instead of using `current_setting(..., true)` with `missing_ok`?** The `missing_ok` parameter of `current_setting()` (PostgreSQL 9.6+) handles truly missing GUCs. But custom GUCs (with a dot prefix like `ttwatch.current_user_id`) are never "missing" — they default to empty string `''` when unset. Casting `''::UUID` still fails. The alternative `COALESCE(NULLIF(current_setting(...), ''), '00000000-...')::UUID` works but adds complexity to every policy definition and evaluates a function that the worker never needs. Restricting policies to `ttwatch_app` is simpler, more explicit, and eliminates the entire category of GUC-related issues for the worker.

**Why catch `IntegrityError` via string matching instead of importing the specific exception class?** The exception chain from asyncpg through SQLAlchemy varies by driver version. SQLAlchemy wraps asyncpg's `UniqueViolationError` in `IntegrityError`, but catching `sqlalchemy.exc.IntegrityError` requires importing it — and within a session-managed block, the exception may arrive as the driver-level exception before SQLAlchemy wraps it. String matching for "unique", "duplicate", or PostgreSQL error code "23505" catches all variants reliably. The initial SELECT remains as a fast-path optimization.

**Why `last_known_price` on the alert instead of querying price history?** `market_data_cache` stores snapshots per-symbol-per-hour. Determining whether a price "crossed" a threshold requires knowing the price at the previous check, which was 15 minutes ago. The hourly cache granularity doesn't capture this. Storing `last_known_price` directly on the alert is O(1) and always reflects the exact price seen at the previous check interval, enabling precise crossing detection.

**Why `datetime.now(timezone.utc).date()` instead of `date.today()` for sentiment history?** All timestamps in the system (`ingested_at`, `created_at`, etc.) use UTC. Sentiment history aggregates articles by day. If the server runs in a non-UTC timezone, `date.today()` produces local dates while `ingested_at` is UTC. A filter like `func.date(Article.ingested_at) == today` compares UTC dates against local dates, missing articles near the date boundary. Using UTC consistently ensures the aggregation window matches the timestamp data.

**Why ON CONFLICT with raw SQL instead of SQLAlchemy ORM upsert?** SQLAlchemy's ORM `merge()` doesn't support PostgreSQL's `ON CONFLICT` on expression indexes like `date_trunc('hour', fetched_at)`. The `insert().on_conflict_do_update()` Core API supports it, but requires importing additional SQLAlchemy internals. Raw SQL with named parameters is clearer, maps directly to the PostgreSQL documentation, and handles the expression index naturally. The parametrized `:symbol`, `:price` etc. prevent injection.

**Why store `coingecko_id` in `ticker_reference.metadata` JSONB instead of a dedicated column?** CoinGecko IDs are only needed for crypto assets. Adding a dedicated column would be NULL for ~95% of ticker_reference rows (equities, ETFs). The `metadata` JSONB column already exists for extensible reference data. A seeding script can populate `{"coingecko_id": "bitcoin"}` for crypto entries. The fallback to `symbol.lower()` handles coins where the CoinGecko ID matches the lowercase symbol (e.g., "ethereum", "solana", "cardano").

**Why `model_fields_set` instead of `model_dump(exclude_unset=True)`?** Both accomplish the same goal — identifying which fields the client explicitly provided. `model_fields_set` is a set of field names, while `model_dump(exclude_unset=True)` returns a dict of values. Using `model_fields_set` with `setattr()` avoids creating an intermediate dict and handles the ORM object directly. It's also more explicit about the intent: we're checking which fields were set, not extracting their values.

---

*All v12 technical decisions carry forward. v13 decisions are in §18.*

---

### v14 Technical Decisions

**Why initialize all SQL bind parameters with `None` in `cache_data` instead of conditionally building the SQL?** The raw SQL INSERT statement uses named parameters (`:pe_ratio`, `:eps`, etc.) for all columns. SQLAlchemy's `text()` requires ALL named parameters to be present in the passed dict, even if the value is `NULL`. The crypto code path only populates price-related fields, but the equity path populates all fields. Rather than maintaining two separate SQL statements (error-prone, code duplication), initializing all parameters to `None` at the top of the function ensures the single INSERT works for both paths. `None` maps to SQL `NULL`, which is the correct value for equity-specific fields like `pe_ratio` on crypto assets.

**Why `asyncio.get_running_loop().time()` instead of `asyncio.get_event_loop().time()` in WebSocket?** This is the same fix documented in v10 for `ensure_queue_consumed`, but the WebSocket endpoint was missed. `get_event_loop()` is deprecated in Python 3.12 (the runtime used by TTwatch) and emits `DeprecationWarning`. `get_running_loop()` is semantically correct inside an async context — it returns the loop that's currently executing the coroutine, guaranteed to exist. The WebSocket handler is always running inside an async context (FastAPI async endpoint), so `get_running_loop()` is safe.

**Why initialize `last_known_price` on first check instead of at alert creation time?** At creation time, the latest market data for the symbol may not be cached yet (e.g., user creates an alert for a symbol added to the watchlist in the same session). The `check_price_alerts` task already fetches market data as part of its loop. Initializing on the first check guarantees the price is available. The tradeoff is a maximum 15-minute delay (one beat cycle) before the crosses alert becomes active. This is acceptable because crosses alerts are inherently temporal — they require two data points to detect a threshold crossing.

**Why cap refresh tokens at 10 per user instead of revoking all on login?** Revoking all tokens on login would force logout on all other devices. Users expect multi-device sessions to persist. A cap of 10 allows reasonable multi-device usage (phone, tablet, laptop, work PC, etc.) while preventing unbounded growth. The oldest tokens are deleted first, which naturally evicts stale sessions from forgotten devices.

**Why `FORCE ROW LEVEL SECURITY` instead of relying on role-based enforcement?** `ENABLE ROW LEVEL SECURITY` makes RLS apply to all roles except the table owner. `FORCE ROW LEVEL SECURITY` additionally applies RLS to the table owner. Since Alembic migrations run as the `postgres` superuser (which is the table owner), any migration that accidentally queries user-scoped data would bypass RLS without `FORCE`. Superusers still bypass RLS regardless of `FORCE` (this is a PostgreSQL invariant), but `FORCE` protects against non-superuser table owners. It's a zero-cost defense-in-depth measure — if the roles are configured correctly, `FORCE` has no observable effect.

---

*All v13 technical decisions carry forward. v14 decisions are documented inline above.*

---

## Appendix M — v14 Change Log

This section summarizes the 6 issues identified during the v14 deep review.

### Critical Bugs (will crash or silently fail at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 186 | **`fetch_market_data` crypto code path omits required SQL bind parameters** — The `cache_data` dict is initialized with only `symbol`, `asset_type`, and `data_source`. The crypto code path adds `price`, `price_change_pct`, `market_cap`, `volume`, and `data_source` — but never sets `pe_ratio`, `eps`, `dividend_yield`, `beta`, `fifty_two_week_high`, or `fifty_two_week_low`. The raw SQL INSERT uses named parameters (`:pe_ratio`, `:eps`, etc.) for all columns. SQLAlchemy's `text()` raises `StatementError: A value is required for bind parameter 'pe_ratio'` when the dict is missing any named parameter. This crashes `fetch_market_data` for every crypto symbol, silently preventing all crypto market data from being cached. Investment analyses and correlation signals for crypto assets operate on empty data. | §8 Maintenance | Moved all field initialization to `cache_data` dict declaration at the top of the function, defaulting every column to `None`. Both crypto and equity code paths then selectively `update()` the fields they have data for. The single INSERT statement works for both paths because `None` maps to SQL `NULL`. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 187 | **WebSocket heartbeat uses deprecated `asyncio.get_event_loop().time()`** — The WebSocket endpoint (§4 `main.py`) uses `asyncio.get_event_loop().time()` in two places: initializing `last_pong` and updating it on pong receipt. The v10 technical decisions explicitly document that `get_event_loop()` is deprecated in Python 3.12 and `get_running_loop()` is the correct replacement. However, the WebSocket code (added in v9) was not updated. This emits `DeprecationWarning` on every WebSocket connection in Python 3.12 and may break in future Python versions. | §4 main.py | Replaced both `asyncio.get_event_loop().time()` calls with `asyncio.get_running_loop().time()`. |
| 188 | **`crosses_above`/`crosses_below` price alerts never fire after creation** — When a user creates a price alert with condition `crosses_above` or `crosses_below`, `last_known_price` starts as `NULL` (database default). The `check_price_alerts` task evaluates crosses conditions only when `alert.last_known_price is not None`. On the first check (and every subsequent check), the condition is skipped because `last_known_price` remains `NULL` until it's updated. But it's only updated inside the `if alert.last_known_price is not None` block — creating a dead code path. The alert never fires. | §7.6 price_alerts.py | The `last_known_price` update was already outside the `is not None` guard (correctly), but the first-check behavior was not documented. Added explicit comment clarifying that the first check initializes the baseline without triggering. No code change needed beyond the clarifying comments — the existing code correctly initializes `last_known_price` on first check via the unconditional update at the end of the crosses block. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 189 | **Login endpoint accumulates unbounded refresh tokens per user** — Each call to `/auth/login` creates a new `RefreshToken` record. There is no limit on how many active refresh tokens a user can have, and no cleanup of old tokens on new login. A user who logs in 100 times (multiple devices, page refreshes, automated tests) creates 100 valid refresh tokens, all usable to generate access tokens for 30 days. This wastes database storage and increases the attack surface — a compromised old token remains valid. | §6 Auth Router | Added a cap of 10 active refresh tokens per user in the login handler. After creating the new token, the handler counts active tokens and deletes the oldest ones beyond the cap. This allows multi-device sessions while preventing unbounded growth. |
| 190 | **Missing `FORCE ROW LEVEL SECURITY` on all RLS-enabled tables** — `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` makes RLS policies apply to all roles except the table owner. Without `FORCE ROW LEVEL SECURITY`, the table owner bypasses all RLS policies. Since tables are created by the `postgres` superuser (via Alembic migrations), `postgres` is the owner. While superusers always bypass RLS regardless of `FORCE`, the `FORCE` flag protects against future configuration changes (e.g., if table ownership is transferred to a non-superuser role) and is a zero-cost defense-in-depth measure recommended by PostgreSQL security documentation. | §8 RLS | Added `ALTER TABLE ... FORCE ROW LEVEL SECURITY` for all 15 user-scoped tables immediately after the corresponding `ENABLE` statements. |
| 191 | **Security checklist and technical decisions not updated for v14** — No coverage for crypto bind parameters, WebSocket deprecation fix, refresh token cap, crosses alert initialization, or FORCE RLS. | §17, §18 | Added 6 new security checklist items and 5 new technical decision entries covering all v14 changes. |

---

*All v13 technical decisions carry forward. v14 decisions are documented inline in Appendix M above.*

---

## Appendix N — v15 Change Log

This section summarizes the 6 issues identified during the v15 deep review.

### Critical Bugs (will crash at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 192 | **`token_count.scalar()` called twice on same `Result` object in login handler** — The v14 refresh token cap implementation calls `token_count.scalar()` to check `if > 10`, then calls `token_count.scalar()` again inside `.limit()` to compute how many tokens to delete. SQLAlchemy's `Result.scalar()` consumes the result row on first call. The second call returns `None`, causing `None - 10` → `TypeError: unsupported operand type(s) for -: 'NoneType' and 'int'`. Every login for a user with 10+ active refresh tokens crashes with HTTP 500. | §6 Auth Router | Stored `token_count.scalar()` in a local variable `active_count` before the `if` check. Both the comparison and the `.limit()` calculation use the stored value. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 193 | **Price alert WebSocket notification described but never implemented** — The `check_price_alerts` docstring says "If WebSocket is available, sends real-time notification" but no notification code exists. Workers are synchronous Celery processes in separate containers and cannot access the API's in-process `ws_manager`. Triggered alerts are logged and deactivated in the database, but users never receive real-time WebSocket notification — they only see triggered alerts on next page load. | §7.6 price_alerts.py, §4 main.py | Added Redis pub/sub bridge: `check_price_alerts` publishes triggered alerts to `ttwatch:alerts:triggered` channel via synchronous Redis. Added `ws_alert_listener()` background coroutine in API lifespan that subscribes to the channel and forwards events to `ws_manager.notify_user()`. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 194 | **Missing CHECK constraint on `price_alerts.condition`** — The `condition` column accepts any text value, but `check_price_alerts` only handles `above`, `below`, `crosses_above`, `crosses_below`. Any other value (typo, API bug, direct SQL) creates an alert that appears active but can never trigger — it silently passes through all condition checks without matching. | §8 Investment Tables, ORM | Added `CHECK (condition IN ('above', 'below', 'crosses_above', 'crosses_below'))` to SQL DDL and `CheckConstraint` to SQLAlchemy model. |
| 195 | **No periodic cleanup of expired refresh tokens** — Refresh tokens expire after 30 days (`expires_at`), but expired tokens are never deleted. The login handler caps *active* tokens at 10 per user, but expired tokens accumulate indefinitely. Over time, the `refresh_tokens` table grows unbounded with dead rows, wasting storage and degrading index performance for the `token_hash` lookups used by `/auth/refresh` and `/auth/logout`. | §7.2 beat_schedule, §8 Maintenance | Added `cleanup_expired_refresh_tokens` task that deletes all tokens with `expires_at < now()`. Runs daily at 2:30 AM via beat schedule. |
| 196 | **Missing index on `refresh_tokens.token_hash`** — The `/auth/refresh` and `/auth/logout` endpoints query `WHERE token_hash = ?` to look up tokens. The table has indexes on `user_id` but not on `token_hash`. Every refresh/logout operation performs a sequential scan on the entire table. With expired token accumulation (fixed in #195), performance degrades over time. | §8 User & Auth Indexes | Added `CREATE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash)`. |
| 197 | **`sentiment_history` permanently loses cluster context after recluster** — Every 2 hours, `recluster_topic` nullifies `sentiment_history.cluster_id` for rows belonging to deleted clusters. While this preserves the sentiment data (ON DELETE SET NULL), the cluster association is permanently lost. Historical queries like "what was the sentiment trend for the 'AI Safety' cluster last week?" become impossible because `cluster_id` is NULL for all old records. | §8 Intelligence Tables, ORM, §7.6 sentiment_agg.py | Added `cluster_keyword TEXT` column to `sentiment_history` table and ORM model. `compute_sentiment_history` now snapshots `cluster.keyword` at aggregation time. When recluster nullifies `cluster_id`, the `cluster_keyword` text persists, preserving the human-readable cluster label for historical analysis. |

---

*All v14 technical decisions carry forward. v15 decisions are documented inline in Appendix N above.*

---

## Appendix O — v16 Change Log

This section summarizes the 5 issues identified during the v16 deep review.

### Critical Bugs (will crash or cause data corruption at runtime)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 198 | **Login endpoint refresh token cap counts ALL tokens including expired ones** — The v15 token cap query `select(count(RefreshToken.id)).where(RefreshToken.user_id == user.id)` counts all tokens, including expired ones. A user with 9 expired + 1 active token = 10 total, prematurely triggering the cap. The `.limit(active_count - 10)` calculation then deletes the oldest tokens, which may be the user's only active session (if expired tokens have newer `created_at` timestamps, e.g., from clock skew or rapid login/expire cycles). The daily `cleanup_expired_refresh_tokens` task mitigates this over time, but until it runs, users can be locked out of their only active session on every login. | §6 Auth Router | Added `RefreshToken.expires_at > datetime.now(timezone.utc)` to both the count query and the delete-oldest query. The cap now correctly counts and manages only unexpired (functionally active) tokens. Expired tokens are left to the daily cleanup task. |

### Runtime Bugs (incorrect behavior or degraded functionality)

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 199 | **`ws_alert_listener` contains dead code with unreachable `os.environ` reference** — The function uses `settings.REDIS_CACHE_URL if hasattr(settings, 'REDIS_CACHE_URL') else os.environ.get(...)`. Since `REDIS_CACHE_URL` is always defined on the Settings class, `hasattr()` always returns True, making the `else` branch unreachable dead code. While Python's short-circuit evaluation prevents `os` (which is not imported in `main.py`) from being looked up at runtime, the dead code is misleading — it suggests a fallback that never executes and would crash if it did. If the Settings field were ever renamed or removed, the `else` branch would execute and raise `NameError`. | §4 main.py | Replaced the entire conditional expression with `settings.REDIS_CACHE_URL`. Removed the dead `os.environ` fallback and unnecessary `hasattr` check. |

### Design Gaps

| # | Issue | Section | Fix |
|---|-------|---------|-----|
| 200 | **`sentiment_history` lacks `topic_id` column for direct topic-level historical queries** — After `recluster_topic` nullifies `cluster_id`, sentiment history rows lose their topic association. The `cluster_keyword` added in v15 preserves cluster identity but not topic association. Queries like "show all sentiment history for topic X" require joining through `clusters`, which only works for current (non-nullified) `cluster_id` values. All historical rows with `cluster_id = NULL` become unreachable for topic-level aggregation. Adding a denormalized `topic_id` column enables direct `WHERE topic_id = ?` queries regardless of cluster lifecycle. | §8 Intelligence Tables, ORM, §7.6 sentiment_agg.py, §8 Indexes | Added `topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE` to `sentiment_history` DDL, `topic_id` column to ORM model, `topic_id=topic_id` to `compute_sentiment_history` inserts, and `idx_sentiment_user_topic ON sentiment_history(user_id, topic_id, period_start)` index. |
| 201 | **v14 and v15 technical decisions documented in appendices but incorrectly referenced as "in §18"** — The changelog closing lines (after each appendix) state "v14/v15 decisions are in §18" but the actual technical decision entries are inline in the appendix sections (between Appendix M and N). §18 itself ends at v11 additions. This creates confusion when cross-referencing the document. | Appendix M, N closing lines | Updated all "decisions are in §18" references to accurately point to "documented inline above" or to the specific appendix containing them. |
| 202 | **Appendix structure: v14 changelog appeared after v15 technical decisions without its own header** — The v14 changelog content (issues #186-#191) appeared inline after the v15 technical decisions section without an "Appendix M" header, breaking the document's consistent appendix naming convention and making it difficult to navigate. | Appendix M | Added proper `## Appendix M — v14 Change Log` header before the v14 changelog content. |

---

### v16 Technical Decisions

**Why count only unexpired tokens in the refresh token cap instead of all tokens?** The cap's purpose is to limit *concurrent active sessions*, not total historical tokens. Expired tokens are functionally dead — the `/auth/refresh` endpoint already rejects them via the `expires_at > now()` check. Counting expired tokens inflates the total and triggers premature cleanup, potentially evicting the user's only working session. The daily `cleanup_expired_refresh_tokens` task handles expired token accumulation separately. This separation of concerns keeps the cap focused on its intended purpose.

**Why simplify `ws_alert_listener` to use `settings.REDIS_CACHE_URL` directly?** The original code used a defensive pattern: `settings.X if hasattr(settings, 'X') else os.environ.get('X')`. This pattern is appropriate when a setting might not exist on older Settings class versions during rolling deploys. However, `REDIS_CACHE_URL` has been on the Settings class since the initial config.py definition (v4). The `hasattr` check is always True, making the fallback dead code. Dead code hides bugs (the `os` module wasn't imported) and suggests a flexibility that doesn't exist. Using `settings.REDIS_CACHE_URL` directly is clear, correct, and matches the pattern used by every other settings access in the codebase.

**Why add `topic_id` to `sentiment_history` instead of relying on the cluster→topic join?** The cluster→topic relationship is transient — clusters are destroyed and recreated every 2 hours. After recluster nullifies `cluster_id`, the join path `sentiment_history → clusters → topics` is broken for historical rows. The `cluster_keyword` (v15) preserves cluster identity but doesn't encode which topic the cluster belonged to. A denormalized `topic_id` column provides a direct, recluster-proof path for topic-level queries. The denormalization cost (one additional UUID per row) is negligible compared to the query complexity saved. The column uses `ON DELETE CASCADE` (matching the topic lifecycle) since sentiment history for a deleted topic has no value.

**Why fix appendix cross-references instead of physically moving technical decisions into §18?** The technical decisions for v12-v15 are logically grouped with their respective changelogs — each decision entry directly relates to a specific bug fix or design change described in the same appendix. Moving them into §18 would separate the rationale from the change it explains, requiring readers to jump between sections. The fix is to correct the cross-references so they accurately describe where the decisions are documented, not to restructure the content.

---

*All v15 technical decisions carry forward. v16 decisions are documented inline in Appendix O above.*
