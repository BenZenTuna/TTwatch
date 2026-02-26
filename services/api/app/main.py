import asyncio
import json
import logging
from contextlib import asynccontextmanager

import jwt as pyjwt
import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.embedder import get_embedding_provider
from app.services.init_services import init_all
from app.services.llm_factory import get_llm_provider

logger = logging.getLogger(__name__)


# === WebSocket Connection Manager ===

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


# === Redis pub/sub listener for price alert notifications ===

async def ws_alert_listener():
    """Background task: subscribe to Redis pub/sub for triggered price alerts
    and forward them to the appropriate user's WebSocket connections.

    Workers publish to 'ttwatch:alerts:triggered' (synchronous Redis).
    This coroutine subscribes asynchronously and bridges to ws_manager.
    Started during API lifespan; cancelled on shutdown.
    """
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


# === FastAPI Lifespan ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize external services and persistent clients
    init_all()  # Qdrant collection + MinIO bucket (idempotent)
    app.state.llm = get_llm_provider()
    app.state.embedder = get_embedding_provider()

    # Start background Redis pub/sub listener for price alert notifications
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
from fastapi import Depends

from app.routers import health, topics, clusters, articles, search, briefings
from app.routers import entities, sentiment, sources, queries, investment, market_data
from app.routers import users
from app.auth.router import router as auth_router
from app.deps import rate_limit_dependency

# Rate-limited dependencies applied to all authenticated API routers.
# FastAPI caches dependencies per request, so get_current_user (called inside
# rate_limit_dependency) runs only once even though routers also depend on it.
_rate_limited = [Depends(rate_limit_dependency)]

app.include_router(health.router, tags=["health"])
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/api", tags=["users"], dependencies=_rate_limited)
app.include_router(topics.router, prefix="/api", tags=["topics"], dependencies=_rate_limited)
app.include_router(clusters.router, prefix="/api", tags=["clusters"], dependencies=_rate_limited)
app.include_router(articles.router, prefix="/api", tags=["articles"], dependencies=_rate_limited)
app.include_router(search.router, prefix="/api", tags=["search"], dependencies=_rate_limited)
app.include_router(briefings.router, prefix="/api", tags=["briefings"], dependencies=_rate_limited)
app.include_router(entities.router, prefix="/api", tags=["entities"], dependencies=_rate_limited)
app.include_router(sentiment.router, prefix="/api", tags=["sentiment"], dependencies=_rate_limited)
app.include_router(sources.router, prefix="/api", tags=["sources"], dependencies=_rate_limited)
app.include_router(queries.router, prefix="/api", tags=["queries"], dependencies=_rate_limited)
app.include_router(investment.router, prefix="/api", tags=["investment"], dependencies=_rate_limited)
app.include_router(market_data.router, prefix="/api", tags=["market_data"], dependencies=_rate_limited)


# === WebSocket endpoint for real-time dashboard updates ===

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates (new articles, cluster changes, briefings).

    Client sends: {"type": "auth", "token": "<jwt>"}
    Server sends: {"type": "article_ingested", ...}, {"type": "cluster_updated", ...}

    Heartbeat: Server sends {"type": "ping"} every 30s.
    Client should respond with {"type": "pong"} to keep the connection alive.
    Connections idle for >90s without pong are terminated.
    """
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
