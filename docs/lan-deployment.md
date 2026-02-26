# TTwatch LAN Deployment Guide

This guide covers deploying TTwatch across multiple machines on a local network, separating GPU-intensive services from the core platform.

## Architecture

```
Main Server (192.168.1.100)         GPU Server (192.168.1.200)
┌─────────────────────────┐         ┌─────────────────────────┐
│ frontend  :3000         │         │ vLLM       :8000        │
│ api       :8080    ◄────┼── LAN ──┼─► embedder  :8001       │
│ worker-io               │         └─────────────────────────┘
│ worker-cpu              │
│ scheduler               │         Search Server (192.168.1.201)
│ postgres  :5432         │         ┌─────────────────────────┐
│ redis     :6379         │         │ searxng    :8080        │
│ qdrant    :6333    ◄────┼── LAN ──┼─►                       │
│ minio     :9000         │         └─────────────────────────┘
└─────────────────────────┘
```

---

## Step 1: GPU Server Setup

On the GPU machine (e.g. `192.168.1.200`):

```bash
git clone <repo-url> && cd TTwatch

# Download model weights
bash scripts/download-models.sh

# Start GPU services
docker compose -f docker-compose.gpu-node.yml up -d
```

This starts:
- **vLLM** on port 8000 (Qwen2.5-32B-Instruct-AWQ)
- **BGE-M3 Embedder** on port 8001

Verify from the main server:
```bash
curl http://192.168.1.200:8000/health
curl http://192.168.1.200:8001/health
```

---

## Step 2: Search Server Setup (Optional)

On a third machine (e.g. `192.168.1.201`):

```bash
git clone <repo-url> && cd TTwatch
docker compose -f docker-compose.search-node.yml up -d
```

Verify: `curl http://192.168.1.201:8080/healthz`

---

## Step 3: Main Server Setup

### Environment Variables

Edit `.env` on the main server:

```bash
# === Database ===
POSTGRES_PASSWORD=your_secure_password
APP_DB_PASSWORD=your_app_password
WORKER_DB_PASSWORD=your_worker_password

# === JWT ===
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# === LAN Service URLs (point to GPU server) ===
VLLM_URL=http://192.168.1.200:8000/v1
EMBEDDER_URL=http://192.168.1.200:8001

# === SearXNG (point to search server, or keep local) ===
SEARXNG_URL=http://192.168.1.201:8080

# === LLM ===
LLM_PROVIDER=local
EMBEDDING_DIMENSION=1024

# === Frontend URLs (use main server's LAN IP) ===
NEXT_PUBLIC_API_URL=http://192.168.1.100:8080
NEXT_PUBLIC_WS_URL=ws://192.168.1.100:8080/ws

# === CORS (allow access from LAN) ===
CORS_ORIGINS=http://192.168.1.100:3000,http://localhost:3000
```

### Start Services

```bash
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d
```

The `docker-compose.lan.yml` overlay:
- Disables the local SearXNG container (uses remote)
- Removes SearXNG from dependency chains

---

## Step 4: Post-Deployment

```bash
# Run migrations
docker compose exec api alembic upgrade head

# Create admin user
docker compose exec api python scripts/create-admin-user.py

# Verify all services
curl -s http://localhost:8080/health/services | python3 -m json.tool
```

Expected output:
```json
{
  "vllm": true,
  "embedder": true,
  "searxng": true,
  "qdrant": true,
  "postgres": true,
  "redis": true,
  "mode": "local"
}
```

---

## Firewall Rules

### GPU Server (192.168.1.200)

Open ports for the main server only:

```bash
# Allow vLLM from main server
sudo ufw allow from 192.168.1.100 to any port 8000
# Allow embedder from main server
sudo ufw allow from 192.168.1.100 to any port 8001
```

### Search Server (192.168.1.201)

```bash
# Allow SearXNG from main server
sudo ufw allow from 192.168.1.100 to any port 8080
```

### Main Server (192.168.1.100)

```bash
# Allow frontend access from LAN
sudo ufw allow 3000/tcp
# Allow API access from LAN
sudo ufw allow 8080/tcp
```

Do **not** expose PostgreSQL (5432), Redis (6379), Qdrant (6333), or MinIO (9000) to the network unless explicitly needed.

---

## CORS Configuration

For LAN access from multiple devices, set `CORS_ORIGINS` to include all client origins:

```bash
# Single device
CORS_ORIGINS=http://192.168.1.100:3000

# Multiple devices
CORS_ORIGINS=http://192.168.1.100:3000,http://192.168.1.50:3000,http://localhost:3000
```

The frontend must access the API at the main server's LAN IP (not `localhost`) when other devices need to reach it.

---

## GPU Benchmark

Run from the main server to verify GPU connectivity and performance:

```bash
VLLM_URL=http://192.168.1.200:8000/v1 \
EMBEDDER_URL=http://192.168.1.200:8001 \
python scripts/benchmark-gpu.py
```

---

## Troubleshooting

### GPU services unreachable

1. Verify ports are open: `nc -zv 192.168.1.200 8000`
2. Check GPU container logs: `docker compose -f docker-compose.gpu-node.yml logs`
3. Verify GPU is visible: `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`

### vLLM takes a long time to start

Model loading for Qwen2.5-32B can take 2-5 minutes. The healthcheck has a 120s `start_period`. Check: `docker compose -f docker-compose.gpu-node.yml logs -f vllm`

### Workers can't reach GPU services

Verify that `VLLM_URL` and `EMBEDDER_URL` in `.env` use the GPU server's LAN IP (not Docker internal hostname). Worker containers resolve external IPs directly.

### WebSocket not connecting from LAN

Ensure `NEXT_PUBLIC_WS_URL` uses the main server's LAN IP:
```
NEXT_PUBLIC_WS_URL=ws://192.168.1.100:8080/ws
```
Not `ws://localhost:8080/ws` — `localhost` resolves to the client's own machine.

### Latency between machines

For LLM inference, LAN latency (<1ms) is negligible compared to generation time (seconds). For embedding, batch requests amortize the overhead. Single-text embeddings add ~2ms of LAN overhead.
