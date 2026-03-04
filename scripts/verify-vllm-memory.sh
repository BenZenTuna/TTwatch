#!/usr/bin/env bash
# verify-vllm-memory.sh — Verify vLLM dual-model GPU memory health
set -euo pipefail

PASS=0
FAIL=0
WARN=0

pass() { echo "  [PASS] $1"; ((PASS++)); }
fail() { echo "  [FAIL] $1"; ((FAIL++)); }
warn() { echo "  [WARN] $1"; ((WARN++)); }

# Determine vllm-fast host port from docker-compose.gpu.yml
VLLM_PORT=8100
VLLM_FAST_PORT=8102

echo "=== vLLM Memory Verification ==="
echo ""

# --- Check 1: VRAM usage ---
echo "--- 1. VRAM Usage ---"
if command -v nvidia-smi &>/dev/null; then
    VRAM_USED_MIB=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')
    VRAM_TOTAL_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
    VRAM_FREE_MIB=$((VRAM_TOTAL_MIB - VRAM_USED_MIB))
    echo "  VRAM Used:  ${VRAM_USED_MIB} MiB"
    echo "  VRAM Total: ${VRAM_TOTAL_MIB} MiB"
    echo "  VRAM Free:  ${VRAM_FREE_MIB} MiB"
    pass "nvidia-smi accessible"
else
    VRAM_FREE_MIB=0
    VRAM_USED_MIB="N/A"
    VRAM_TOTAL_MIB="N/A"
    fail "nvidia-smi not found"
fi
echo ""

# --- Check 2: Container health ---
echo "--- 2. Container Health ---"
for svc in vllm vllm-fast; do
    # Find the container (try common compose project name patterns)
    CID=$(docker ps --filter "name=${svc}" --format '{{.ID}}' | head -1)
    if [ -z "$CID" ]; then
        fail "${svc} container not running"
        continue
    fi
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CID" 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then
        pass "${svc} container is healthy"
    else
        fail "${svc} container status: ${STATUS}"
    fi
done
echo ""

# --- Check 3: Health endpoints ---
echo "--- 3. Health Endpoints ---"
for pair in "vllm:${VLLM_PORT}" "vllm-fast:${VLLM_FAST_PORT}"; do
    SVC="${pair%%:*}"
    PORT="${pair##*:}"
    if curl -sf --max-time 5 "http://localhost:${PORT}/health" >/dev/null 2>&1; then
        pass "${SVC} health endpoint (port ${PORT}) OK"
    else
        fail "${SVC} health endpoint (port ${PORT}) unreachable"
    fi
done
echo ""

# --- Check 4: Test inference ---
echo "--- 4. Test Inference ---"
PAYLOAD='{"model":"default","messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}'
for pair in "vllm:${VLLM_PORT}" "vllm-fast:${VLLM_FAST_PORT}"; do
    SVC="${pair%%:*}"
    PORT="${pair##*:}"
    RESP=$(curl -sf --max-time 30 -X POST "http://localhost:${PORT}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" 2>&1) || RESP=""
    if echo "$RESP" | grep -q '"choices"'; then
        pass "${SVC} inference returned valid response"
    else
        fail "${SVC} inference failed or returned error"
    fi
done
echo ""

# --- Check 5: vLLM EngineCore CPU usage ---
echo "--- 5. vLLM EngineCore CPU Usage ---"
# Take two samples 3 seconds apart and compute CPU delta
get_vllm_cputime() {
    # Sum CPU time (user+sys in clock ticks) for all vllm-related processes
    ps aux 2>/dev/null | grep -i '[v]llm' | awk '{sum += $3} END {print sum+0}'
}

CPU1=$(get_vllm_cputime)
sleep 3
CPU2=$(get_vllm_cputime)

# CPU% is already percentage from ps aux, average over the interval
CPU_AVG=$(echo "$CPU1 $CPU2" | awk '{printf "%.1f", ($1 + $2) / 2}')
echo "  vLLM aggregate CPU%: ${CPU_AVG}%"

CPU_HIGH=$(echo "$CPU_AVG" | awk '{print ($1 > 20) ? 1 : 0}')
if [ "$CPU_HIGH" = "0" ]; then
    pass "vLLM CPU usage is below 20% (${CPU_AVG}%)"
else
    fail "vLLM CPU usage is above 20% (${CPU_AVG}%) — likely CPU-side block swapping"
fi
echo ""

# --- Summary ---
echo "=== Summary ==="
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
echo ""

if [ "$VRAM_TOTAL_MIB" != "N/A" ]; then
    echo "  VRAM: ${VRAM_USED_MIB} MiB used / ${VRAM_TOTAL_MIB} MiB total (${VRAM_FREE_MIB} MiB free)"
    if [ "$VRAM_FREE_MIB" -gt 1500 ]; then
        echo "  Verdict: KV cache headroom adequate (${VRAM_FREE_MIB} MiB free > 1500 MiB threshold)"
    else
        echo "  WARNING: low KV cache headroom (${VRAM_FREE_MIB} MiB free) — consider further reducing gpu-memory-utilization"
    fi
fi

echo ""
if [ "$FAIL" -gt 0 ]; then
    echo "RESULT: FAIL (${FAIL} check(s) failed)"
    exit 1
else
    echo "RESULT: ALL CHECKS PASSED"
    exit 0
fi
