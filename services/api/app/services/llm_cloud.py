import copy

import httpx

from app.config import settings
from app.services.llm import LLMProvider
from app.services.llm_utils import parse_json_response


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
