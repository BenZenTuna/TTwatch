from app.config import settings
from app.services.llm_cloud import CloudLLMProvider
from app.services.llm_local import LocalVLLMProvider


def get_llm_provider():
    """Return the configured LLM provider based on environment settings."""
    if settings.LLM_PROVIDER == "cloud" or not settings.VLLM_URL:
        return CloudLLMProvider()
    return LocalVLLMProvider()
