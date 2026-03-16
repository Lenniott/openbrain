from __future__ import annotations

import httpx

from .config import settings


async def transcribe_audio(filename: str, file_bytes: bytes, language: str | None = None) -> str:
    async with httpx.AsyncClient(timeout=settings.WHISPER_TIMEOUT_SECONDS) as client:
        files = {
            "file": (filename, file_bytes, "application/octet-stream"),
        }
        data: dict[str, str] = {"model": settings.WHISPER_MODEL}
        if language:
            data["language"] = language
        resp = await client.post(
            f"{settings.WHISPER_BASE_URL}/v1/audio/transcriptions",
            data=data,
            files=files,
        )
        resp.raise_for_status()
        body = resp.json()
        # Expect {"text": "..."}
        text = body.get("text")
        if not isinstance(text, str):
            raise RuntimeError("Unexpected transcription response format")
        return text

