"""Qwen3-Embedding-0.6B embedding service.

Runs as a standalone FastAPI server on port 8001.
Always resident in GPU VRAM (~1.2GB for FP16).
Supports batch embedding up to 256 texts per call.
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    from sentence_transformers import SentenceTransformer

    model_name = os.environ.get("MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
    logger.info(f"Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name, device="cuda")
    logger.info(f"Model loaded. Embedding dimension: {model.get_sentence_embedding_dimension()}")
    yield
    logger.info("Shutting down embedder")


app = FastAPI(title="TTwatch Embedder", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dimension: int


@app.get("/health")
async def health():
    if model is None:
        raise HTTPException(503, "Model not loaded")
    return {"status": "ok", "model": os.environ.get("MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")}


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest):
    if model is None:
        raise HTTPException(503, "Model not loaded")
    if not request.texts:
        return EmbedResponse(embeddings=[], dimension=model.get_sentence_embedding_dimension())
    if len(request.texts) > 256:
        raise HTTPException(400, "Maximum 256 texts per batch")

    embeddings = model.encode(
        request.texts,
        batch_size=64,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return EmbedResponse(
        embeddings=embeddings.tolist(),
        dimension=embeddings.shape[1],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
