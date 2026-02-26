"""Synchronous LLM client for Celery worker tasks.

Celery tasks MUST be synchronous (def, not async def).
This module provides httpx.Client-based sync wrappers.
"""
import copy
import json
import os
import re

import httpx
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
        text = text[first_brace : last_brace + 1]

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
        lambda e: isinstance(
            e, (httpx.ConnectError, httpx.TimeoutException, ConnectionError)
        )
    ),
    before_sleep=lambda rs: None,
)


class SyncLLMClient:
    """Synchronous LLM client that talks to vLLM or cloud providers."""

    def __init__(self):
        self.provider = os.environ.get("LLM_PROVIDER", "local")
        self.vllm_url = os.environ.get("VLLM_URL", "http://vllm:8000/v1")
        model_name = os.environ.get("LOCAL_MODEL_NAME", "Qwen2.5-32B-Instruct-AWQ")
        # vLLM registers models with their full path (e.g. /models/QwQ-32B-AWQ)
        self.model = f"/models/{model_name}" if self.provider == "local" else model_name
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
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
            self._is_anthropic = cloud_provider == "anthropic"
        else:
            self._client = httpx.Client(
                base_url=self.vllm_url,
                timeout=httpx.Timeout(300.0, connect=10.0),
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
            msg = resp.json()["choices"][0]["message"]
            # Reasoning models (QwQ, DeepSeek-R1) may put output in "reasoning"
            # field when using --reasoning-parser; fall back to it if content is null
            return msg.get("content") or msg.get("reasoning") or ""

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
            resp = self._client.post(
                "/v1/embeddings",
                json={
                    "model": self.model,
                    "input": texts,
                },
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])
            return [d["embedding"] for d in data]

    def close(self):
        self._client.close()
