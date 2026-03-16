from __future__ import annotations

from typing import Any

import httpx

from .config import settings


async def get_embedding(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=settings.EMBEDDING_TIMEOUT_SECONDS) as client:
        # Ollama embed API
        payload: dict[str, Any] = {
            "model": settings.EMBEDDING_MODEL,
            "input": text,
        }
        resp = await client.post(f"{settings.EMBEDDING_BASE_URL}/api/embed", json=payload)
        resp.raise_for_status()
        data = resp.json()
        # Ollama /api/embed returns {"embeddings": [[...]]}
        if "embeddings" in data and data["embeddings"]:
            return data["embeddings"][0]
        raise RuntimeError("Unexpected embedding response format")

