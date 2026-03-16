from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from qdrant_client.http import models as qmodels

from .config import settings
from .db import get_session
from .models import Inbox, Vector
from .schemas import (
    DocResponse,
    EmbedRequest,
    EmbedResponse,
    SearchByTextRequest,
    SearchByVectorIdRequest,
    SearchHit,
    SearchResponse,
    TranscribeResponse,
)
from .chunking import chunk_text
from .diffing import jaccard_change_ratio
from .embedding import get_embedding
from .qdrant_client import get_qdrant_client, ensure_collection, upsert_vectors, search_by_vector
from .s3_client import get_s3_client, ensure_bucket_exists
from .transcription import transcribe_audio


SessionDep = Annotated[Session, Depends(get_session)]


app = FastAPI(title="OpenBrain Chunking API")


@app.on_event("startup")
def _startup() -> None:
    client = get_qdrant_client()
    try:
        ensure_collection(client)
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] Warning: could not ensure Qdrant collection: {exc}")
    try:
        ensure_bucket_exists(settings.S3_BUCKET_FILES)
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] Warning: could not ensure Minio bucket '{settings.S3_BUCKET_FILES}': {exc}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/embed", response_model=EmbedResponse)
async def embed_text(payload: EmbedRequest, session: SessionDep) -> EmbedResponse:
    confidence = payload.confidence if payload.confidence is not None else 1.0

    inbox = Inbox(
        raw_text=payload.raw_text,
        type=payload.type,
        source=payload.source,
        session_id=payload.session_id,
        fields=payload.fields,
        confidence=confidence,
        status="pending",
        vectorised=False,
    )
    session.add(inbox)
    session.flush()

    text = payload.raw_text or ""
    chunks = chunk_text(text)
    chunk_count = len(chunks)

    if confidence < settings.CONFIDENCE_THRESHOLD:
        inbox.status = "needs_review"
        return EmbedResponse(inbox_id=str(inbox.id), chunk_count=chunk_count, embedded=False)

    client = get_qdrant_client()
    points: list[qmodels.PointStruct] = []

    if chunk_count == 0:
        # nothing to embed
        inbox.status = "ok"
        inbox.vectorised = True
        return EmbedResponse(inbox_id=str(inbox.id), chunk_count=0, embedded=False)

    try:
        if chunk_count == 1:
            idx, chunk_text_value = chunks[0]
            vec = Vector(inbox_id=inbox.id, chunk_index=idx, chunk_text=chunk_text_value)
            session.add(vec)
            session.flush()
            embedding = await get_embedding(chunk_text_value)
            points.append(
                qmodels.PointStruct(
                    id=str(vec.id),
                    vector={"dense": embedding},
                    payload={
                        "inbox_id": str(inbox.id),
                        "chunk_index": idx,
                        "type": inbox.type,
                        "source": inbox.source,
                        "filename": inbox.filename,
                    },
                )
            )
        else:
            for idx, chunk_text_value in chunks:
                vec = Vector(inbox_id=inbox.id, chunk_index=idx, chunk_text=chunk_text_value)
                session.add(vec)
                session.flush()
                embedding = await get_embedding(chunk_text_value)
                points.append(
                    qmodels.PointStruct(
                        id=str(vec.id),
                        vector={"dense": embedding},
                        payload={
                            "inbox_id": str(inbox.id),
                            "chunk_index": idx,
                            "type": inbox.type,
                            "source": inbox.source,
                            "filename": inbox.filename,
                        },
                    )
                )

        upsert_vectors(client, points)
    except Exception as exc:  # noqa: BLE001
        inbox.status = "embed_error"
        inbox.vectorised = False
        raise HTTPException(status_code=502, detail=f"Embedding/Qdrant failed: {exc}") from exc

    inbox.status = "ok"
    inbox.vectorised = True
    return EmbedResponse(inbox_id=str(inbox.id), chunk_count=chunk_count, embedded=True)


@app.post("/doc", response_model=DocResponse)
async def ingest_document(
    file: UploadFile,
    session: SessionDep,
    x_ob_filename: Annotated[str | None, Header(alias="x-ob-filename")] = None,
    x_ob_filetype: Annotated[str | None, Header(alias="x-ob-filetype")] = None,
    x_ob_source: Annotated[str | None, Header(alias="x-ob-source")] = None,
    x_ob_session_id: Annotated[str | None, Header(alias="x-ob-session-id")] = None,
    x_ob_description: Annotated[str | None, Header(alias="x-ob-description")] = None,
) -> DocResponse:
    filename = x_ob_filename or file.filename
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required (x-ob-filename or file.filename)")

    data = await file.read()

    # In purely local mode we may not have Minio/Qdrant running; just create/update inbox
    # and skip external calls so the endpoint can be tested in isolation.
    if settings.ENVIRONMENT == "local":
        existing: Inbox | None = (
            session.query(Inbox)
            .filter(Inbox.filename == filename, Inbox.type == "document")
            .one_or_none()
        )
        if existing is None:
            inbox = Inbox(
                raw_text=x_ob_description or "",
                source=x_ob_source or "unknown",
                type="document",
                filename=filename,
                filetype=x_ob_filetype,
                session_id=x_ob_session_id,
                status="pending",
                confidence=1.0,
                vectorised=False,
                fields={"note": "local-test-no-s3"},
            )
            session.add(inbox)
            session.flush()
            return DocResponse(status="ok", inbox_id=str(inbox.id), filename=filename, is_new=True)
        return DocResponse(status="ok", inbox_id=str(existing.id), filename=filename, is_new=False)

    existing: Inbox | None = (
        session.query(Inbox)
        .filter(Inbox.filename == filename, Inbox.type == "document")
        .one_or_none()
    )

    s3 = get_s3_client()
    client = get_qdrant_client()

    if existing is None:
        # New document path
        s3.put_object(Bucket=settings.S3_BUCKET_FILES, Key=filename, Body=data)
        inbox = Inbox(
            raw_text=x_ob_description,
            source=x_ob_source or "unknown",
            type="document",
            filename=filename,
            filetype=x_ob_filetype,
            session_id=x_ob_session_id,
            status="pending",
            confidence=1.0,
            vectorised=False,
            fields={"minio_key": filename},
        )
        session.add(inbox)
        session.flush()

        # For now we don't extract full text; embedding will be driven elsewhere
        # or extended later with document extraction as needed.
        inbox.status = "ok"
        return DocResponse(status="ok", inbox_id=str(inbox.id), filename=filename, is_new=True)

    # Existing document: overwrite file and mark stale.
    s3.put_object(Bucket=settings.S3_BUCKET_FILES, Key=filename, Body=data)
    existing.vectorised = False
    existing.vectorised_at = None
    existing.status = "pending"

    return DocResponse(status="ok", inbox_id=str(existing.id), filename=filename, is_new=False)


@app.post("/search/text", response_model=SearchResponse)
async def search_by_text(payload: SearchByTextRequest, session: SessionDep) -> SearchResponse:
    client = get_qdrant_client()
    try:
        embedding = await get_embedding(payload.query_text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Embedding failed: {exc}") from exc

    hits = search_by_vector(client, embedding, limit=payload.limit)

    results: list[SearchHit] = []
    for h in hits:
        payload_obj = h.payload or {}
        inbox_id = payload_obj.get("inbox_id")
        if not inbox_id:
            continue
        inbox: Inbox | None = session.query(Inbox).get(inbox_id)
        vector: Vector | None = session.query(Vector).get(h.id)
        results.append(
            SearchHit(
                score=h.score,
                vector_id=str(h.id),
                inbox_id=str(inbox_id),
                type=inbox.type if inbox else None,
                source=inbox.source if inbox else None,
                chunk_index=payload_obj.get("chunk_index"),
                chunk_text=vector.chunk_text if vector else None,
                filename=inbox.filename if inbox else None,
            )
        )

    return SearchResponse(hits=results)


@app.post("/search/vector", response_model=SearchResponse)
async def search_by_vector_id(payload: SearchByVectorIdRequest, session: SessionDep) -> SearchResponse:
    client = get_qdrant_client()
    # For now, reuse vector search using stored vector by id.
    try:
        hits = client.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=None,
            using=None,
            limit=payload.limit,
            query_filter=None,
            vector_id=payload.vector_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Qdrant search failed: {exc}") from exc

    results: list[SearchHit] = []
    for h in hits:
        payload_obj = h.payload or {}
        inbox_id = payload_obj.get("inbox_id")
        if not inbox_id:
            continue
        inbox: Inbox | None = session.query(Inbox).get(inbox_id)
        vector: Vector | None = session.query(Vector).get(h.id)
        results.append(
            SearchHit(
                score=h.score,
                vector_id=str(h.id),
                inbox_id=str(inbox_id),
                type=inbox.type if inbox else None,
                source=inbox.source if inbox else None,
                chunk_index=payload_obj.get("chunk_index"),
                chunk_text=vector.chunk_text if vector else None,
                filename=inbox.filename if inbox else None,
            )
        )

    return SearchResponse(hits=results)


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_endpoint(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> TranscribeResponse:
    data = await file.read()
    try:
        text = await transcribe_audio(file.filename or "audio", data, language=language)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc
    return TranscribeResponse(text=text)


def get_app() -> FastAPI:
    return app

