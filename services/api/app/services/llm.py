from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Async LLM provider — used by FastAPI API handlers."""

    @abstractmethod
    async def generate(self, messages: list[dict], **kwargs) -> str: ...

    @abstractmethod
    async def generate_json(self, messages: list[dict], **kwargs) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...
