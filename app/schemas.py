from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EmbedRequest(BaseModel):
    raw_text: str
    type: str
    source: str | None = None
    session_id: str | None = None
    fields: dict[str, Any] | None = None
    confidence: float | None = None


class EmbedResponse(BaseModel):
    inbox_id: str
    chunk_count: int
    embedded: bool


class DocResponse(BaseModel):
    status: str
    inbox_id: str | None = None
    filename: str | None = None
    is_new: bool | None = None
    chunk_count: int | None = None
    error: str | None = None


class SearchByTextRequest(BaseModel):
    query_text: str
    limit: int = 20
    filters: dict[str, Any] | None = None


class SearchByVectorIdRequest(BaseModel):
    vector_id: str
    limit: int = 20
    filters: dict[str, Any] | None = None


class SearchHit(BaseModel):
    score: float
    vector_id: str
    inbox_id: str
    type: str | None = None
    source: str | None = None
    chunk_index: int | None = None
    chunk_text: str | None = None
    filename: str | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class TranscribeResponse(BaseModel):
    text: str


class TranscribeEmbedResponse(BaseModel):
    text: str
    inbox_id: str
    chunk_count: int
    embedded: bool

