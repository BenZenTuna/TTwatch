#!/usr/bin/env python3
"""Test vLLM and BGE-M3 connectivity and benchmark performance.

Usage:
    VLLM_URL=http://localhost:8100/v1 EMBEDDER_URL=http://localhost:8101 python scripts/benchmark-gpu.py

    # Or with defaults (Docker internal URLs):
    python scripts/benchmark-gpu.py
"""
import json
import os
import sys
import time

import requests


VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8100/v1")
EMBEDDER_URL = os.environ.get("EMBEDDER_URL", "http://localhost:8101")


def test_vllm_health():
    """Check vLLM is responding."""
    url = VLLM_URL.replace("/v1", "/health")
    try:
        r = requests.get(url, timeout=10)
        return r.status_code == 200
    except requests.RequestException as e:
        print(f"  vLLM health check failed: {e}")
        return False


def test_embedder_health():
    """Check embedder is responding."""
    try:
        r = requests.get(f"{EMBEDDER_URL}/health", timeout=10)
        return r.status_code == 200
    except requests.RequestException as e:
        print(f"  Embedder health check failed: {e}")
        return False


def benchmark_vllm():
    """Benchmark vLLM with a short generation task."""
    prompt = "Summarize the key risks of investing in semiconductor stocks in 3 bullet points."

    print("  Sending chat completion request...")
    start = time.perf_counter()
    try:
        r = requests.post(
            f"{VLLM_URL}/chat/completions",
            json={
                "model": os.environ.get("LOCAL_MODEL_NAME", "Qwen2.5-32B-Instruct-AWQ"),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 256,
                "temperature": 0.7,
            },
            timeout=120,
        )
        elapsed = time.perf_counter() - start

        if r.status_code != 200:
            print(f"  Error: HTTP {r.status_code}")
            print(f"  Response: {r.text[:500]}")
            return None

        data = r.json()
        usage = data.get("usage", {})
        output_text = data["choices"][0]["message"]["content"]
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        tokens_per_sec = output_tokens / elapsed if elapsed > 0 else 0

        print(f"  Response ({elapsed:.2f}s):")
        print(f"    {output_text[:300]}...")
        print(f"  Tokens: {total_tokens} total, {output_tokens} generated")
        print(f"  Speed: {tokens_per_sec:.1f} tokens/sec")
        return tokens_per_sec

    except requests.RequestException as e:
        print(f"  Request failed: {e}")
        return None


def benchmark_embedder():
    """Benchmark BGE-M3 embedder with batch embedding."""
    texts = [
        "Artificial intelligence safety research and alignment",
        "TSMC advanced semiconductor manufacturing process",
        "Federal Reserve interest rate decision impact on markets",
        "CRISPR gene editing breakthrough in clinical trials",
        "Cybersecurity zero-day vulnerability in critical infrastructure",
        "Renewable energy solar panel efficiency improvement",
        "Quantum computing error correction milestone",
        "Electric vehicle battery technology solid-state",
    ]

    # Single text
    print("  Single text embedding...")
    start = time.perf_counter()
    try:
        r = requests.post(
            f"{EMBEDDER_URL}/embed",
            json={"texts": [texts[0]]},
            timeout=30,
        )
        single_elapsed = time.perf_counter() - start

        if r.status_code != 200:
            print(f"  Error: HTTP {r.status_code}")
            return None

        data = r.json()
        dim = len(data["embeddings"][0])
        print(f"  Single: {single_elapsed*1000:.1f}ms, dimension={dim}")

    except requests.RequestException as e:
        print(f"  Request failed: {e}")
        return None

    # Batch
    print(f"  Batch embedding ({len(texts)} texts)...")
    start = time.perf_counter()
    try:
        r = requests.post(
            f"{EMBEDDER_URL}/embed",
            json={"texts": texts},
            timeout=30,
        )
        batch_elapsed = time.perf_counter() - start

        if r.status_code != 200:
            print(f"  Error: HTTP {r.status_code}")
            return None

        data = r.json()
        per_text = batch_elapsed / len(texts)
        print(f"  Batch: {batch_elapsed*1000:.1f}ms total, {per_text*1000:.1f}ms/text")
        return per_text

    except requests.RequestException as e:
        print(f"  Request failed: {e}")
        return None


def main():
    print("=" * 55)
    print("  TTwatch — GPU Service Benchmark")
    print("=" * 55)
    print()
    print(f"  vLLM URL:     {VLLM_URL}")
    print(f"  Embedder URL: {EMBEDDER_URL}")
    print()

    results = {}

    # --- vLLM ---
    print("[1/4] vLLM Health Check")
    if test_vllm_health():
        print("  OK")
        results["vllm_health"] = "PASS"
    else:
        print("  FAIL — vLLM is not reachable")
        results["vllm_health"] = "FAIL"

    print()
    print("[2/4] vLLM Generation Benchmark")
    if results.get("vllm_health") == "PASS":
        tps = benchmark_vllm()
        results["vllm_tps"] = f"{tps:.1f} tok/s" if tps else "FAIL"
    else:
        print("  Skipped (vLLM not available)")
        results["vllm_tps"] = "SKIP"

    print()

    # --- Embedder ---
    print("[3/4] Embedder Health Check")
    if test_embedder_health():
        print("  OK")
        results["embedder_health"] = "PASS"
    else:
        print("  FAIL — Embedder is not reachable")
        results["embedder_health"] = "FAIL"

    print()
    print("[4/4] Embedder Benchmark")
    if results.get("embedder_health") == "PASS":
        per_text = benchmark_embedder()
        results["embedder_ms_per_text"] = f"{per_text*1000:.1f} ms/text" if per_text else "FAIL"
    else:
        print("  Skipped (Embedder not available)")
        results["embedder_ms_per_text"] = "SKIP"

    # --- Summary ---
    print()
    print("=" * 55)
    print("  Results Summary")
    print("=" * 55)
    for key, value in results.items():
        label = key.replace("_", " ").title()
        status = "PASS" if "FAIL" not in value and "SKIP" not in value else value
        print(f"  {label:30s} {value}")

    all_pass = all("FAIL" not in v and "SKIP" not in v for v in results.values())
    print()
    if all_pass:
        print("  All GPU services operational.")
    else:
        print("  Some services are not available. Check logs.")
        sys.exit(1)


if __name__ == "__main__":
    main()
