import httpx

from app.config import settings
from app.services.llm import LLMProvider
from app.services.llm_utils import parse_json_response


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
        resp = await self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.3),
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def generate_json(self, messages, **kwargs):
        kwargs.setdefault("temperature", 0.1)
        raw = await self.generate(messages, **kwargs)
        return parse_json_response(raw)

    async def close(self):
        await self._client.aclose()
