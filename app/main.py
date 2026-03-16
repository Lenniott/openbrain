from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from qdrant_client.http import models as qmodels

from .config import settings
from .db import get_session, create_tables
from .models import Inbox, Neighbour, PipelineLog, Vector
from .schemas import (
    DocResponse,
    EmbedRequest,
    EmbedResponse,
    SearchByTextRequest,
    SearchByVectorIdRequest,
    SearchHit,
    SearchResponse,
    TranscribeEmbedResponse,
    TranscribeResponse,
)
from .chunking import chunk_text
from .diffing import jaccard_change_ratio
from .doc_extraction import extract_text
from .embedding import get_embedding
from .qdrant_client import get_qdrant_client, ensure_collection, find_neighbours, upsert_vectors, search_by_vector
from .s3_client import get_s3_client, ensure_bucket_exists
from .transcription import transcribe_audio


SessionDep = Annotated[Session, Depends(get_session)]


app = FastAPI(title="OpenBrain Chunking API")


def _plog(session: Session, inbox_id, step: str, status: str = "ok", detail: str | None = None) -> None:
    session.add(PipelineLog(inbox_id=inbox_id, step=step, status=status, detail=detail))


def _write_neighbours(
    session: Session,
    qdrant_client,
    inbox_id: str,
    embeddings_by_chunk: list[list[float]],
) -> None:
    """
    For each chunk embedding, find nearest neighbours in Qdrant (excluding
    same inbox), deduplicate at inbox level, and write to ob_neighbours.
    Called after upsert_vectors so the new points are already searchable.
    """
    seen_pairs: set[tuple[str, str]] = set()
    for embedding in embeddings_by_chunk:
        for neighbour_inbox_id, distance in find_neighbours(qdrant_client, embedding, exclude_inbox_id=inbox_id):
            pair = (inbox_id, neighbour_inbox_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            session.add(Neighbour(
                entity_id=inbox_id,
                neighbour_id=neighbour_inbox_id,
                distance=distance,
            ))


@app.on_event("startup")
def _startup() -> None:
    create_tables()
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
    _plog(session, inbox.id, "inbox_created", detail=f"type={payload.type} source={payload.source}")

    text = payload.raw_text or ""
    chunks = chunk_text(text)
    chunk_count = len(chunks)

    if confidence < settings.CONFIDENCE_THRESHOLD:
        inbox.status = "needs_review"
        _plog(session, inbox.id, "low_confidence", status="skipped", detail=f"confidence={confidence}")
        return EmbedResponse(inbox_id=str(inbox.id), chunk_count=chunk_count, embedded=False)

    client = get_qdrant_client()
    points: list[qmodels.PointStruct] = []

    if chunk_count == 0:
        inbox.status = "ok"
        inbox.vectorised = True
        _plog(session, inbox.id, "chunk", status="skipped", detail="empty text")
        return EmbedResponse(inbox_id=str(inbox.id), chunk_count=0, embedded=False)

    embeddings: list[list[float]] = []
    try:
        for idx, chunk_text_value in chunks:
            vec = Vector(inbox_id=inbox.id, chunk_index=idx, chunk_text=chunk_text_value)
            session.add(vec)
            session.flush()
            embedding = await get_embedding(chunk_text_value)
            embeddings.append(embedding)
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
            _plog(session, inbox.id, "chunk", detail=f"idx={idx} vec={vec.id}")

        upsert_vectors(client, points)
        _plog(session, inbox.id, "qdrant_upsert", detail=f"points={len(points)}")

        _write_neighbours(session, client, str(inbox.id), embeddings)
        _plog(session, inbox.id, "neighbour", detail=f"searched from {len(embeddings)} chunk(s)")
    except Exception as exc:  # noqa: BLE001
        inbox.status = "embed_error"
        inbox.vectorised = False
        _plog(session, inbox.id, "embed_error", status="error", detail=str(exc))
        raise HTTPException(status_code=502, detail=f"Embedding/Qdrant failed: {exc}") from exc

    inbox.status = "ok"
    inbox.vectorised = True
    _plog(session, inbox.id, "vectorised", detail=f"chunks={chunk_count}")
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

    # Extract text from the uploaded file
    try:
        extracted_text = extract_text(data, filename)
    except ValueError as exc:
        _plog(session, None, "embed_error", status="error", detail=f"extract failed: {filename}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing: Inbox | None = (
        session.query(Inbox)
        .filter(Inbox.filename == filename, Inbox.type == "document")
        .one_or_none()
    )

    change_ratio: float = 1.0  # default: treat as fully changed (new doc)
    # --- Diff check: skip re-embedding if content hasn't meaningfully changed ---
    if existing is not None:
        change_ratio = jaccard_change_ratio(existing.raw_text or "", extracted_text)
        if change_ratio < 0.05:
            _plog(session, existing.id, "diff_skip", status="skipped", detail=f"change_ratio={change_ratio:.3f}")
            return DocResponse(
                status="no_change",
                inbox_id=str(existing.id),
                filename=filename,
                is_new=False,
                chunk_count=0,
            )

    if settings.ENVIRONMENT == "local":
        if existing is None:
            inbox = Inbox(
                raw_text=extracted_text,
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
            _plog(session, inbox.id, "inbox_created", detail=f"local mode filename={filename}")
            return DocResponse(status="ok", inbox_id=str(inbox.id), filename=filename, is_new=True, chunk_count=0)
        existing.raw_text = extracted_text
        existing.vectorised = False
        existing.vectorised_at = None
        existing.status = "pending"
        _plog(session, existing.id, "inbox_created", detail=f"local mode update filename={filename}")
        return DocResponse(status="ok", inbox_id=str(existing.id), filename=filename, is_new=False, chunk_count=0)

    s3 = get_s3_client()
    qdrant = get_qdrant_client()

    s3.put_object(Bucket=settings.S3_BUCKET_FILES, Key=filename, Body=data)

    if existing is None:
        inbox = Inbox(
            raw_text=extracted_text,
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
        _plog(session, inbox.id, "inbox_created", detail=f"new doc filename={filename}")
        is_new = True
    else:
        qdrant.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(key="inbox_id", match=qmodels.MatchValue(value=str(existing.id)))]
                )
            ),
        )
        session.query(Vector).filter(Vector.inbox_id == existing.id).delete()
        existing.raw_text = extracted_text
        existing.vectorised = False
        existing.vectorised_at = None
        existing.status = "pending"
        session.flush()
        inbox = existing
        _plog(session, inbox.id, "purge", detail=f"re-embed filename={filename} change_ratio={change_ratio:.3f}")
        is_new = False

    chunks = chunk_text(extracted_text)
    points: list[qmodels.PointStruct] = []
    embeddings: list[list[float]] = []

    try:
        for idx, chunk_text_value in chunks:
            vec = Vector(inbox_id=inbox.id, chunk_index=idx, chunk_text=chunk_text_value)
            session.add(vec)
            session.flush()
            embedding = await get_embedding(chunk_text_value)
            embeddings.append(embedding)
            points.append(
                qmodels.PointStruct(
                    id=str(vec.id),
                    vector={"dense": embedding},
                    payload={
                        "inbox_id": str(inbox.id),
                        "chunk_index": idx,
                        "type": "document",
                        "source": inbox.source,
                        "filename": filename,
                    },
                )
            )
            _plog(session, inbox.id, "chunk", detail=f"idx={idx} vec={vec.id}")

        upsert_vectors(qdrant, points)
        _plog(session, inbox.id, "qdrant_upsert", detail=f"points={len(points)}")

        _write_neighbours(session, qdrant, str(inbox.id), embeddings)
        _plog(session, inbox.id, "neighbour", detail=f"searched from {len(embeddings)} chunk(s)")
    except Exception as exc:  # noqa: BLE001
        inbox.status = "embed_error"
        _plog(session, inbox.id, "embed_error", status="error", detail=str(exc))
        raise HTTPException(status_code=502, detail=f"Embedding/Qdrant failed: {exc}") from exc

    inbox.vectorised = True
    inbox.vectorised_at = datetime.utcnow()
    inbox.status = "ok"
    _plog(session, inbox.id, "vectorised", detail=f"chunks={len(chunks)} filename={filename}")

    return DocResponse(
        status="ok",
        inbox_id=str(inbox.id),
        filename=filename,
        is_new=is_new,
        chunk_count=len(chunks),
    )


@app.get("/doc/{inbox_id}/file")
def download_doc(inbox_id: str, session: SessionDep) -> Response:
    inbox: Inbox | None = session.query(Inbox).get(inbox_id)
    if inbox is None or inbox.type != "document":
        raise HTTPException(status_code=404, detail="document not found")
    if not inbox.filename:
        raise HTTPException(status_code=404, detail="no filename on record")

    s3 = get_s3_client()
    try:
        obj = s3.get_object(Bucket=settings.S3_BUCKET_FILES, Key=inbox.filename)
        data = obj["Body"].read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"S3 fetch failed: {exc}") from exc

    # Guess content-type from filetype/filename
    ft = (inbox.filetype or inbox.filename.rsplit(".", 1)[-1]).lower()
    content_type_map = {
        "pdf": "application/pdf",
        "txt": "text/plain",
        "md":  "text/markdown",
        "csv": "text/csv",
    }
    content_type = content_type_map.get(ft, "application/octet-stream")

    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{inbox.filename}"'},
    )


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


@app.post("/transcribe/embed", response_model=TranscribeEmbedResponse)
async def transcribe_and_embed(
    session: SessionDep,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    source: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
) -> TranscribeEmbedResponse:
    filename = file.filename or "audio"
    data = await file.read()

    # Step 1: transcribe
    try:
        text = await transcribe_audio(filename, data, language=language)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc

    # Step 2: store audio in S3, create inbox row as a document
    filetype = filename.rsplit(".", 1)[-1].lower() if "." in filename else "audio"
    if settings.ENVIRONMENT != "local":
        s3 = get_s3_client()
        try:
            s3.put_object(Bucket=settings.S3_BUCKET_FILES, Key=filename, Body=data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"S3 upload failed: {exc}") from exc

    inbox = Inbox(
        raw_text=text,
        type="document",
        source=source or "whisper",
        session_id=session_id,
        filename=filename,
        filetype=filetype,
        confidence=1.0,
        status="pending",
        vectorised=False,
        fields={"minio_key": filename, "transcript": True},
    )
    session.add(inbox)
    session.flush()
    _plog(session, inbox.id, "inbox_created", detail=f"audio doc filename={filename} chars={len(text)}")

    # Step 3: chunk → embed → write vectors → neighbours
    chunks = chunk_text(text)
    chunk_count = len(chunks)
    points: list[qmodels.PointStruct] = []
    embeddings: list[list[float]] = []

    if chunk_count == 0:
        inbox.status = "ok"
        inbox.vectorised = True
        _plog(session, inbox.id, "chunk", status="skipped", detail="empty transcript")
        return TranscribeEmbedResponse(text=text, inbox_id=str(inbox.id), chunk_count=0, embedded=False)

    client = get_qdrant_client()
    try:
        for idx, chunk_text_value in chunks:
            vec = Vector(inbox_id=inbox.id, chunk_index=idx, chunk_text=chunk_text_value)
            session.add(vec)
            session.flush()
            embedding = await get_embedding(chunk_text_value)
            embeddings.append(embedding)
            points.append(
                qmodels.PointStruct(
                    id=str(vec.id),
                    vector={"dense": embedding},
                    payload={
                        "inbox_id": str(inbox.id),
                        "chunk_index": idx,
                        "type": "document",
                        "source": inbox.source,
                        "filename": filename,
                    },
                )
            )
            _plog(session, inbox.id, "chunk", detail=f"idx={idx} vec={vec.id}")

        upsert_vectors(client, points)
        _plog(session, inbox.id, "qdrant_upsert", detail=f"points={len(points)}")

        _write_neighbours(session, client, str(inbox.id), embeddings)
        _plog(session, inbox.id, "neighbour", detail=f"searched from {len(embeddings)} chunk(s)")
    except Exception as exc:  # noqa: BLE001
        inbox.status = "embed_error"
        inbox.vectorised = False
        _plog(session, inbox.id, "embed_error", status="error", detail=str(exc))
        raise HTTPException(status_code=502, detail=f"Embedding failed: {exc}") from exc

    inbox.status = "ok"
    inbox.vectorised = True
    _plog(session, inbox.id, "vectorised", detail=f"chunks={chunk_count}")

    return TranscribeEmbedResponse(text=text, inbox_id=str(inbox.id), chunk_count=chunk_count, embedded=True)


def get_app() -> FastAPI:
    return app

