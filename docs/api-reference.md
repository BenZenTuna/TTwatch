# TTwatch API Reference

Base URL: `http://localhost:8080`

All authenticated endpoints require either:
- `Authorization: Bearer <jwt>` header, or
- `X-API-Key: tw_live_...` header

Rate limiting: 60 requests/minute per user per endpoint.

---

## Authentication

### POST /auth/register

Register a new user account.

**Request body:**
```json
{
  "email": "user@example.com",
  "display_name": "User Name",
  "password": "StrongPass1!"
}
```

Password requirements: 10+ characters, 1 uppercase, 1 lowercase, 1 digit.

**Response:** `200 OK`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "abc123...",
  "token_type": "bearer"
}
```

### POST /auth/login

**Request body:**
```json
{
  "email": "user@example.com",
  "password": "StrongPass1!"
}
```

**Response:** `200 OK` — same shape as register.

### POST /auth/refresh

Rotate a refresh token for new access + refresh tokens.

**Request body:**
```json
{ "refresh_token": "abc123..." }
```

**Response:** `200 OK` — new token pair. Old refresh token is invalidated.

### POST /auth/logout

Invalidate a refresh token.

**Request body:**
```json
{ "refresh_token": "abc123..." }
```

**Response:** `200 OK` `{ "status": "logged_out" }`

---

## User Profile

### GET /api/me

Returns the current user's profile.

**Response:** `200 OK`
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "display_name": "User Name",
  "is_active": true,
  "max_topics": 10,
  "max_articles_per_topic": 5000,
  "max_api_keys": 5,
  "created_at": "2025-01-01T00:00:00Z",
  "last_login_at": "2025-01-15T10:00:00Z"
}
```

### PUT /api/me

Update display name.

**Request body:** `{ "display_name": "New Name" }`

### GET /api/me/api-keys

List active API keys (prefix + metadata only — full key never shown again).

### POST /api/me/api-keys

Generate a new API key. The full key is returned **once** in the response.

**Request body:** `{ "label": "my-agent" }`

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "key": "tw_live_abcd_1234567890abcdef1234567890abcdef",
  "key_prefix": "tw_live_abcd_1",
  "label": "my-agent"
}
```

### DELETE /api/me/api-keys/{key_id}

Revoke an API key. `204 No Content`.

---

## Topics

### GET /api/topics

List all topics for the current user.

### POST /api/topics

Create a new topic. Returns `403` if topic limit reached.

**Request body:**
```json
{
  "name": "AI Safety",
  "icon": "shield",
  "config": {
    "search_terms": ["AI safety", "AI alignment"],
    "search_engines": ["google", "bing"],
    "max_results_per_query": 10,
    "language": "en"
  },
  "refresh_interval_minutes": 120
}
```

### GET /api/topics/{topic_id}

Get a single topic.

### PUT /api/topics/{topic_id}

Partial update — only provided fields are changed.

### DELETE /api/topics/{topic_id}

Delete a topic and all associated data. `204 No Content`.

### GET /api/topics/{topic_id}/clusters

List clusters for a topic, ordered by trend score.

---

## Articles

### GET /api/topics/{topic_id}/articles

List articles for a topic with optional filters.

**Query parameters:**
| Param | Type | Description |
|---|---|---|
| `cluster_id` | UUID | Filter by cluster |
| `is_duplicate` | bool | Filter duplicates |
| `published_after` | datetime | Published after date |
| `published_before` | datetime | Published before date |
| `limit` | int (max 200) | Page size (default 50) |
| `offset` | int | Pagination offset |

### GET /api/articles/{article_id}

Get a single article with full details (summary, key quotes, sentiment).

### GET /api/articles/{article_id}/entities

List entities linked to an article.

---

## Clusters

### GET /api/clusters/{cluster_id}

Get a single cluster.

### GET /api/clusters/{cluster_id}/articles

List articles in a cluster (paginated).

---

## Search

### POST /api/search

Semantic search across articles using vector similarity.

**Request body:**
```json
{
  "query": "impact of AI regulation on tech stocks",
  "topic_id": "uuid",
  "limit": 20
}
```

**Response:** `200 OK`
```json
[
  {
    "article": { "id": "...", "title": "...", ... },
    "score": 0.95
  }
]
```

---

## Briefings

### GET /api/topics/{topic_id}/briefings

List generated briefings (most recent first).

### GET /api/briefings/{briefing_id}

Get a single briefing with summary, highlights, entities, watch items, coverage gaps.

### POST /api/topics/{topic_id}/briefings/generate

Manually trigger briefing generation. Returns `202 Accepted`.

**Response:** `{ "task_id": "celery-task-id", "status": "queued" }`

---

## Entities

### GET /api/topics/{topic_id}/entities

List named entities for a topic. Optional `type` query param to filter by entity type.

### GET /api/entities/{entity_id}

Get a single entity.

### GET /api/entities/{entity_id}/articles

List articles that mention an entity (paginated).

---

## Sentiment

### GET /api/topics/{topic_id}/sentiment

Get latest sentiment snapshot per cluster.

### GET /api/topics/{topic_id}/sentiment/history

Sentiment time series. Optional `cluster_keyword` filter for recluster-proof history.

---

## Sources

### GET /api/topics/{topic_id}/sources

List RSS/web sources for a topic.

### POST /api/topics/{topic_id}/sources

Add a source.

**Request body:**
```json
{
  "name": "TechCrunch",
  "url": "https://techcrunch.com/feed/",
  "source_type": "rss",
  "enabled": true,
  "config": {}
}
```

### PUT /api/sources/{source_id}

Update a source (partial update).

### DELETE /api/sources/{source_id}

Remove a source. `204 No Content`.

---

## Saved Queries

### GET /api/topics/{topic_id}/queries

List saved queries for a topic.

### POST /api/topics/{topic_id}/queries

Save a query.

**Request body:**
```json
{ "query_text": "semiconductor supply chain disruption", "schedule": "on_refresh" }
```

### DELETE /api/queries/{query_id}

Delete a saved query. `204 No Content`.

---

## Investment

### GET /api/topics/{topic_id}/watchlist

List watchlist items for a topic.

### POST /api/topics/{topic_id}/watchlist

Add to watchlist.

**Request body:**
```json
{
  "symbol": "AAPL",
  "asset_type": "equity",
  "added_reason": "Top tech holding",
  "notes": "Watch for earnings",
  "target_price": "200.50",
  "stop_loss": "150.00"
}
```

### DELETE /api/watchlist/{item_id}

Remove from watchlist. `204 No Content`.

### GET /api/topics/{topic_id}/analyses

List investment analyses for a topic.

### GET /api/topics/{topic_id}/correlation-signals

List recent correlation signals (max 50).

---

## Price Alerts

### POST /api/price-alerts

Create a price alert.

**Request body:**
```json
{
  "symbol": "NVDA",
  "condition": "above",
  "threshold": "150.00"
}
```

Conditions: `above`, `below`, `crosses_above`, `crosses_below`.

### GET /api/price-alerts

List active price alerts.

### DELETE /api/price-alerts/{alert_id}

Delete a price alert. `204 No Content`.

---

## Market Data

### GET /api/market-data/{symbol}

Get latest cached market data for a symbol (shared reference data).

### GET /api/market-data/{symbol}/history

OHLCV price history. Optional `limit` query param (default 90, max 365).

---

## Health

### GET /health

Basic health check. `{ "status": "ok" }`

### GET /health/services

Extended health check with connectivity status for all backend services.

**Response:**
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

## WebSocket

### WS /ws

Real-time updates for dashboard.

**Connection flow:**
1. Connect to `ws://host:8080/ws`
2. Send auth: `{"type": "auth", "token": "<jwt>"}`
3. Receive: `{"type": "connected", "user_id": "..."}`
4. Receive events: `article_ingested`, `cluster_updated`, `briefing_ready`, `price_alert_triggered`
5. Respond to `{"type": "ping"}` with `{"type": "pong"}` (90s timeout)

---

## Error Responses

All errors follow the format:
```json
{ "detail": "Error description" }
```

| Status | Meaning |
|---|---|
| 400 | Bad request / validation error |
| 401 | Authentication required or failed |
| 403 | Forbidden (limit reached) |
| 404 | Resource not found |
| 409 | Conflict (duplicate) |
| 422 | Validation error (Pydantic) |
| 429 | Rate limit exceeded |
