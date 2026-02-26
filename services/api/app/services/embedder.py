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
        resp = await self._client.post(
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

    async def close(self):
        await self._client.aclose()


def get_embedding_provider():
    """Return the configured embedding provider."""
    if settings.LLM_PROVIDER == "cloud" or not settings.EMBEDDER_URL:
        return CloudEmbeddingProvider()
    return LocalEmbeddingProvider()
