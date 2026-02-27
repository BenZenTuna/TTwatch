#!/bin/bash
###############################################################################
# TTwatch Full System Diagnostic
# Run from the TTwatch project root: bash scripts/ttwatch-diagnose.sh
###############################################################################

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

pass() { echo -e "  ${GREEN}✓ $1${NC}"; }
fail() { echo -e "  ${RED}✗ $1${NC}"; }
warn() { echo -e "  ${YELLOW}⚠ $1${NC}"; }
info() { echo -e "  ${CYAN}ℹ $1${NC}"; }
header() { echo -e "\n${BOLD}━━━ $1 ━━━${NC}"; }

ERRORS=0
WARNINGS=0

###############################################################################
header "1. DOCKER CONTAINERS STATUS"
###############################################################################

REQUIRED_CONTAINERS=("postgres" "redis" "qdrant" "minio" "searxng" "api" "worker-io" "worker-cpu" "scheduler" "frontend")
OPTIONAL_CONTAINERS=("vllm" "embedder")

for c in "${REQUIRED_CONTAINERS[@]}"; do
    STATUS=$(docker compose ps --format '{{.State}}' "$c" 2>/dev/null || echo "not_found")
    if [[ "$STATUS" == "running" ]]; then
        HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q "$c" 2>/dev/null)" 2>/dev/null || echo "no_healthcheck")
        if [[ "$HEALTH" == "healthy" ]]; then
            pass "$c: running (healthy)"
        elif [[ "$HEALTH" == "no_healthcheck" ]]; then
            pass "$c: running (no healthcheck)"
        else
            warn "$c: running but health=$HEALTH"
            ((WARNINGS++))
        fi
    else
        fail "$c: $STATUS (REQUIRED)"
        ((ERRORS++))
    fi
done

for c in "${OPTIONAL_CONTAINERS[@]}"; do
    STATUS=$(docker compose ps --format '{{.State}}' "$c" 2>/dev/null || echo "not_found")
    if [[ "$STATUS" == "running" ]]; then
        HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q "$c" 2>/dev/null)" 2>/dev/null || echo "no_healthcheck")
        pass "$c: running (health=$HEALTH)"
    else
        info "$c: not running (optional - needed for local LLM)"
    fi
done

###############################################################################
header "2. SERVICE CONNECTIVITY (from API container)"
###############################################################################

API_CONTAINER=$(docker compose ps -q api 2>/dev/null)
if [[ -z "$API_CONTAINER" ]]; then
    fail "API container not found - cannot run connectivity tests"
    ((ERRORS++))
else
    # PostgreSQL
    PG_OK=$(docker exec "$API_CONTAINER" python3 -c "
import asyncio, os
async def check():
    try:
        import asyncpg
        conn = await asyncpg.connect(os.environ['DATABASE_URL'])
        await conn.fetchval('SELECT 1')
        await conn.close()
        print('ok')
    except Exception as e:
        print(f'fail: {e}')
asyncio.run(check())
" 2>&1)
    if [[ "$PG_OK" == "ok" ]]; then
        pass "PostgreSQL: connected"
    else
        fail "PostgreSQL: $PG_OK"
        ((ERRORS++))
    fi

    # Redis (all 4 databases)
    for db in 0 1 2 3; do
        REDIS_OK=$(docker exec "$API_CONTAINER" python3 -c "
import redis, os
try:
    base = os.environ.get('REDIS_URL', 'redis://redis:6379/0').rsplit('/', 1)[0]
    r = redis.from_url(f'{base}/$db')
    r.ping()
    print('ok')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
        DB_NAMES=("broker" "results" "dedup" "cache/pubsub")
        if [[ "$REDIS_OK" == "ok" ]]; then
            pass "Redis db$db (${DB_NAMES[$db]}): connected"
        else
            fail "Redis db$db (${DB_NAMES[$db]}): $REDIS_OK"
            ((ERRORS++))
        fi
    done

    # Qdrant
    QDRANT_OK=$(docker exec "$API_CONTAINER" python3 -c "
import httpx, os
try:
    r = httpx.get(os.environ.get('QDRANT_URL', 'http://qdrant:6333') + '/readyz', timeout=5)
    print('ok' if r.status_code == 200 else f'fail: HTTP {r.status_code}')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    if [[ "$QDRANT_OK" == "ok" ]]; then
        pass "Qdrant: connected"
    else
        fail "Qdrant: $QDRANT_OK"
        ((ERRORS++))
    fi

    # MinIO
    MINIO_OK=$(docker exec "$API_CONTAINER" python3 -c "
import httpx, os
try:
    r = httpx.get(os.environ.get('MINIO_URL', 'http://minio:9000') + '/minio/health/live', timeout=5)
    print('ok' if r.status_code == 200 else f'fail: HTTP {r.status_code}')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    if [[ "$MINIO_OK" == "ok" ]]; then
        pass "MinIO: connected"
    else
        fail "MinIO: $MINIO_OK"
        ((ERRORS++))
    fi

    # SearXNG
    SEARX_OK=$(docker exec "$API_CONTAINER" python3 -c "
import httpx, os
try:
    r = httpx.get(os.environ.get('SEARXNG_URL', 'http://searxng:8080') + '/healthz', timeout=5)
    print('ok' if r.status_code == 200 else f'fail: HTTP {r.status_code}')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    if [[ "$SEARX_OK" == "ok" ]]; then
        pass "SearXNG: connected"
    else
        fail "SearXNG: $SEARX_OK"
        ((ERRORS++))
    fi

    # SearXNG actual search test
    SEARX_SEARCH=$(docker exec "$API_CONTAINER" python3 -c "
import httpx, os
try:
    url = os.environ.get('SEARXNG_URL', 'http://searxng:8080')
    r = httpx.get(f'{url}/search', params={'q': 'test', 'format': 'json'}, timeout=15)
    data = r.json()
    count = len(data.get('results', []))
    print(f'ok: {count} results')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    if [[ "$SEARX_SEARCH" == ok* ]]; then
        pass "SearXNG search test: $SEARX_SEARCH"
    else
        fail "SearXNG search test: $SEARX_SEARCH"
        ((ERRORS++))
    fi

    # vLLM
    VLLM_URL=$(docker exec "$API_CONTAINER" printenv VLLM_URL 2>/dev/null || echo "http://vllm:8000/v1")
    VLLM_OK=$(docker exec "$API_CONTAINER" python3 -c "
import httpx
try:
    base = '${VLLM_URL}'.replace('/v1', '')
    r = httpx.get(f'{base}/health', timeout=10)
    print('ok' if r.status_code == 200 else f'fail: HTTP {r.status_code}')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    if [[ "$VLLM_OK" == "ok" ]]; then
        pass "vLLM: connected at $VLLM_URL"
    else
        fail "vLLM: $VLLM_OK (at $VLLM_URL)"
        ((ERRORS++))
    fi

    # Embedder
    EMBEDDER_URL=$(docker exec "$API_CONTAINER" printenv EMBEDDER_URL 2>/dev/null || echo "http://embedder:8001")
    EMBED_OK=$(docker exec "$API_CONTAINER" python3 -c "
import httpx
try:
    r = httpx.get('${EMBEDDER_URL}/health', timeout=10)
    print('ok' if r.status_code == 200 else f'fail: HTTP {r.status_code}')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    if [[ "$EMBED_OK" == "ok" ]]; then
        pass "Embedder: connected at $EMBEDDER_URL"
    else
        fail "Embedder: $EMBED_OK (at $EMBEDDER_URL)"
        ((ERRORS++))
    fi

    # LLM Provider setting
    LLM_PROVIDER=$(docker exec "$API_CONTAINER" printenv LLM_PROVIDER 2>/dev/null || echo "not_set")
    info "LLM_PROVIDER=$LLM_PROVIDER"
    if [[ "$LLM_PROVIDER" == "cloud" ]]; then
        CLOUD_KEY=$(docker exec "$API_CONTAINER" python3 -c "
import os
key = os.environ.get('CLOUD_LLM_API_KEY', '')
print(f'{key[:8]}...' if len(key) > 8 else 'EMPTY' if not key else key)
" 2>&1)
        info "CLOUD_LLM_API_KEY=$CLOUD_KEY"
        if [[ "$CLOUD_KEY" == "EMPTY" ]]; then
            fail "Cloud LLM mode but no API key set!"
            ((ERRORS++))
        fi
    fi
fi

###############################################################################
header "3. DATABASE STATE"
###############################################################################

DB_CONTAINER=$(docker compose ps -q postgres 2>/dev/null)
if [[ -n "$DB_CONTAINER" ]]; then
    echo ""
    # Check RLS roles exist
    ROLES=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT rolname FROM pg_roles WHERE rolname IN ('ttwatch_app', 'ttwatch_worker') ORDER BY rolname;
    " 2>&1 | tr -d ' ' | grep -v '^$')
    if echo "$ROLES" | grep -q "ttwatch_app"; then
        pass "Role ttwatch_app exists"
    else
        fail "Role ttwatch_app missing - run init-db.sh"
        ((ERRORS++))
    fi
    if echo "$ROLES" | grep -q "ttwatch_worker"; then
        pass "Role ttwatch_worker exists"
    else
        fail "Role ttwatch_worker missing - run init-db.sh"
        ((ERRORS++))
    fi

    # Check tables exist
    TABLE_COUNT=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT count(*) FROM information_schema.tables WHERE table_schema='public';
    " 2>&1 | tr -d ' ')
    info "Tables in database: $TABLE_COUNT"

    # Check RLS status
    RLS_COUNT=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT count(*) FROM pg_tables WHERE schemaname='public' AND rowsecurity=true;
    " 2>&1 | tr -d ' ')
    info "Tables with RLS enabled: $RLS_COUNT (expected: 15)"

    # Users
    USER_DATA=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT id, email, is_active, is_admin, max_topics FROM users;
    " 2>&1)
    echo -e "  ${CYAN}Users:${NC}"
    echo "$USER_DATA" | while IFS= read -r line; do
        [[ -n "${line// /}" ]] && echo "    $line"
    done

    # Topics
    TOPIC_DATA=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT t.id, t.name, t.config::text, t.last_refreshed_at, t.next_refresh_at
        FROM topics t ORDER BY t.created_at DESC LIMIT 10;
    " 2>&1)
    echo -e "  ${CYAN}Topics:${NC}"
    echo "$TOPIC_DATA" | while IFS= read -r line; do
        [[ -n "${line// /}" ]] && echo "    $line"
    done

    # Check if search_queries were generated
    QUERIES_CHECK=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT t.name,
               t.config->>'search_queries' as search_queries,
               t.config->>'search_terms' as search_terms
        FROM topics t;
    " 2>&1)
    echo -e "  ${CYAN}Topic search configs:${NC}"
    echo "$QUERIES_CHECK" | while IFS= read -r line; do
        [[ -n "${line// /}" ]] && echo "    $line"
    done

    # Articles
    ART_COUNT=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT count(*) FROM articles;
    " 2>&1 | tr -d ' ')
    info "Total articles: $ART_COUNT"

    # Clusters
    CLUS_COUNT=$(docker exec "$DB_CONTAINER" psql -U postgres -d ttwatch -t -c "
        SELECT count(*) FROM clusters;
    " 2>&1 | tr -d ' ')
    info "Total clusters: $CLUS_COUNT"
fi

###############################################################################
header "4. CELERY TASK QUEUE STATUS"
###############################################################################

WORKER_IO=$(docker compose ps -q worker-io 2>/dev/null)
if [[ -n "$WORKER_IO" ]]; then
    # Check registered tasks
    info "Checking registered tasks on worker-io..."
    TASK_LIST=$(docker exec "$WORKER_IO" python3 -c "
from worker.celeryconfig import app
insp = app.control.inspect()
registered = insp.registered()
if registered:
    for worker, tasks in registered.items():
        print(f'Worker: {worker}')
        for t in sorted(tasks):
            print(f'  - {t}')
else:
    print('NO WORKERS RESPONDING')
" 2>&1)
    if echo "$TASK_LIST" | grep -q "NO WORKERS"; then
        fail "No Celery workers responding to inspect!"
        ((ERRORS++))
    else
        pass "Workers responding to inspect"
    fi

    # Check for pending tasks
    PENDING=$(docker exec "$WORKER_IO" python3 -c "
import redis
r = redis.from_url('redis://redis:6379/0')
default_len = r.llen('ttwatch:default')
compute_len = r.llen('ttwatch:compute')
print(f'ttwatch:default={default_len}, ttwatch:compute={compute_len}')
" 2>&1)
    info "Pending tasks: $PENDING"

    # Check active tasks
    ACTIVE=$(docker exec "$WORKER_IO" python3 -c "
from worker.celeryconfig import app
insp = app.control.inspect()
active = insp.active()
if active:
    for worker, tasks in active.items():
        if tasks:
            for t in tasks:
                print(f'{worker}: {t[\"name\"]} (id={t[\"id\"][:8]}...)')
        else:
            print(f'{worker}: idle')
else:
    print('No workers responding')
" 2>&1)
    echo -e "  ${CYAN}Active tasks:${NC}"
    echo "$ACTIVE" | while IFS= read -r line; do
        [[ -n "$line" ]] && echo "    $line"
    done

    # Check reserved (prefetched) tasks
    RESERVED=$(docker exec "$WORKER_IO" python3 -c "
from worker.celeryconfig import app
insp = app.control.inspect()
reserved = insp.reserved()
if reserved:
    for worker, tasks in reserved.items():
        if tasks:
            for t in tasks:
                print(f'{worker}: {t[\"name\"]}')
        else:
            print(f'{worker}: none reserved')
else:
    print('No workers responding')
" 2>&1)
    echo -e "  ${CYAN}Reserved tasks:${NC}"
    echo "$RESERVED" | while IFS= read -r line; do
        [[ -n "$line" ]] && echo "    $line"
    done
fi

###############################################################################
header "5. RECENT TASK FAILURES (worker-io logs, last 100 lines)"
###############################################################################

echo ""
TASK_ERRORS=$(docker compose logs --tail=100 worker-io 2>/dev/null | grep -iE "(error|exception|traceback|failed|refused)" | tail -20)
if [[ -n "$TASK_ERRORS" ]]; then
    fail "Found errors in worker-io logs:"
    echo "$TASK_ERRORS" | while IFS= read -r line; do
        echo -e "    ${RED}$line${NC}"
    done
    ((ERRORS++))
else
    pass "No obvious errors in recent worker-io logs"
fi

echo ""
TASK_ERRORS_CPU=$(docker compose logs --tail=100 worker-cpu 2>/dev/null | grep -iE "(error|exception|traceback|failed|refused)" | tail -20)
if [[ -n "$TASK_ERRORS_CPU" ]]; then
    fail "Found errors in worker-cpu logs:"
    echo "$TASK_ERRORS_CPU" | while IFS= read -r line; do
        echo -e "    ${RED}$line${NC}"
    done
    ((ERRORS++))
else
    pass "No obvious errors in recent worker-cpu logs"
fi

###############################################################################
header "6. RECENT TASK FAILURES (scheduler logs)"
###############################################################################

echo ""
SCHED_ERRORS=$(docker compose logs --tail=50 scheduler 2>/dev/null | grep -iE "(error|exception|traceback|failed)" | tail -10)
if [[ -n "$SCHED_ERRORS" ]]; then
    fail "Found errors in scheduler logs:"
    echo "$SCHED_ERRORS" | while IFS= read -r line; do
        echo -e "    ${RED}$line${NC}"
    done
    ((ERRORS++))
else
    pass "No obvious errors in recent scheduler logs"
fi

# Check if beat is sending tasks
BEAT_SENDS=$(docker compose logs --tail=50 scheduler 2>/dev/null | grep -i "Scheduler: Sending" | tail -5)
if [[ -n "$BEAT_SENDS" ]]; then
    pass "Celery Beat is dispatching tasks:"
    echo "$BEAT_SENDS" | while IFS= read -r line; do
        echo "    $line"
    done
else
    warn "No recent Celery Beat dispatch messages found"
    ((WARNINGS++))
fi

###############################################################################
header "7. API LOGS (last 50 lines, errors only)"
###############################################################################

echo ""
API_ERRORS=$(docker compose logs --tail=50 api 2>/dev/null | grep -iE "(error|exception|traceback|500)" | tail -10)
if [[ -n "$API_ERRORS" ]]; then
    fail "Found errors in API logs:"
    echo "$API_ERRORS" | while IFS= read -r line; do
        echo -e "    ${RED}$line${NC}"
    done
    ((ERRORS++))
else
    pass "No obvious errors in recent API logs"
fi

###############################################################################
header "8. QDRANT COLLECTION STATUS"
###############################################################################

if [[ -n "${API_CONTAINER:-}" ]]; then
    QDRANT_COLLECTIONS=$(docker exec "$API_CONTAINER" python3 -c "
import httpx, os
try:
    url = os.environ.get('QDRANT_URL', 'http://qdrant:6333')
    r = httpx.get(f'{url}/collections', timeout=5)
    data = r.json()
    for c in data.get('result', {}).get('collections', []):
        name = c['name']
        detail = httpx.get(f'{url}/collections/{name}', timeout=5).json()
        result = detail.get('result', {})
        points = result.get('points_count', 0)
        vectors = result.get('vectors_count', 0)
        dim = result.get('config', {}).get('params', {}).get('vectors', {}).get('size', '?')
        print(f'{name}: {points} points, {vectors} vectors, dim={dim}')
    if not data.get('result', {}).get('collections'):
        print('NO COLLECTIONS FOUND')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    echo "$QDRANT_COLLECTIONS" | while IFS= read -r line; do
        if [[ "$line" == "NO COLLECTIONS"* ]]; then
            warn "$line"
            ((WARNINGS++))
        elif [[ "$line" == "fail:"* ]]; then
            fail "$line"
        else
            info "$line"
        fi
    done
fi

###############################################################################
header "9. MINIO BUCKET STATUS"
###############################################################################

if [[ -n "${API_CONTAINER:-}" ]]; then
    MINIO_STATUS=$(docker exec "$API_CONTAINER" python3 -c "
import httpx, os
try:
    url = os.environ.get('MINIO_URL', 'http://minio:9000')
    bucket = os.environ.get('MINIO_BUCKET', 'ttwatch-content')
    # Just check if bucket endpoint responds
    r = httpx.head(f'{url}/{bucket}', timeout=5)
    print(f'Bucket {bucket}: HTTP {r.status_code}')
except Exception as e:
    print(f'fail: {e}')
" 2>&1)
    info "$MINIO_STATUS"
fi

###############################################################################
header "10. ENVIRONMENT CONFIGURATION"
###############################################################################

if [[ -n "${API_CONTAINER:-}" ]]; then
    ENV_CHECK=$(docker exec "$API_CONTAINER" python3 -c "
import os
keys = [
    'DATABASE_URL', 'REDIS_URL', 'QDRANT_URL', 'VLLM_URL', 'EMBEDDER_URL',
    'SEARXNG_URL', 'MINIO_URL', 'MINIO_BUCKET', 'LLM_PROVIDER',
    'LOCAL_MODEL_NAME', 'CLOUD_LLM_PROVIDER', 'CLOUD_LLM_MODEL',
    'EMBEDDING_DIMENSION', 'JWT_SECRET', 'CORS_ORIGINS'
]
for k in keys:
    v = os.environ.get(k, 'NOT SET')
    # Mask sensitive values
    if 'SECRET' in k or 'KEY' in k or 'PASSWORD' in k:
        v = v[:4] + '****' if len(v) > 4 else '****'
    print(f'{k}={v}')
" 2>&1)
    echo "$ENV_CHECK" | while IFS= read -r line; do
        info "$line"
    done
fi

###############################################################################
header "11. END-TO-END PIPELINE TEST"
###############################################################################

echo ""
info "Testing if generate_search_queries task can be dispatched..."

if [[ -n "${WORKER_IO:-}" ]]; then
    E2E_TEST=$(docker exec "$WORKER_IO" python3 -c "
from worker.celeryconfig import app

# Check if task is registered
try:
    task = app.tasks.get('generate_search_queries')
    if task:
        print('task_registered: yes')
    else:
        print('task_registered: no (not in app.tasks)')
except Exception as e:
    print(f'task_registered: error - {e}')

# Check broker connectivity
try:
    conn = app.connection()
    conn.ensure_connection(max_retries=1)
    conn.close()
    print('broker_connected: yes')
except Exception as e:
    print(f'broker_connected: no - {e}')
" 2>&1)
    echo "$E2E_TEST" | while IFS= read -r line; do
        if echo "$line" | grep -q ": yes"; then
            pass "$line"
        elif echo "$line" | grep -q ": no"; then
            fail "$line"
            ((ERRORS++))
        else
            info "$line"
        fi
    done
fi

###############################################################################
header "12. FULL WORKER LOGS (last 200 lines for generate_search_queries / run_topic_search)"
###############################################################################

echo ""
info "Searching for search-related task activity..."
SEARCH_LOGS=$(docker compose logs --tail=200 worker-io worker-cpu 2>/dev/null | grep -iE "(generate_search_queries|run_topic_search|search_plan|ingest_article)" | tail -20)
if [[ -n "$SEARCH_LOGS" ]]; then
    pass "Found search-related task activity:"
    echo "$SEARCH_LOGS" | while IFS= read -r line; do
        echo "    $line"
    done
else
    warn "No search-related task activity found in recent logs"
    ((WARNINGS++))
fi

###############################################################################
header "SUMMARY"
###############################################################################

echo ""
if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All checks passed!${NC}"
elif [[ $ERRORS -eq 0 ]]; then
    echo -e "${YELLOW}${BOLD}$WARNINGS warning(s), no critical errors${NC}"
else
    echo -e "${RED}${BOLD}$ERRORS error(s), $WARNINGS warning(s) found${NC}"
fi

echo ""
echo -e "${BOLD}Common fixes:${NC}"
echo "  1. vLLM not running → search queries can't be generated → no searches happen"
echo "     Fix: make gpu (or set LLM_PROVIDER=cloud with valid API key)"
echo "  2. Workers not picking up tasks → check Redis broker and worker logs"
echo "     Fix: docker compose restart worker-io worker-cpu scheduler"
echo "  3. SearXNG not returning results → external search engines may be blocked"
echo "     Fix: docker compose restart searxng"
echo "  4. Missing database roles → RLS blocks all queries"
echo "     Fix: docker exec -it \$(docker compose ps -q postgres) bash /docker-entrypoint-initdb.d/init-db.sh"
echo "  5. Topic has no search_queries in config → generate_search_queries task failed"
echo "     Fix: check vLLM connectivity, then delete and recreate the topic"
echo ""
