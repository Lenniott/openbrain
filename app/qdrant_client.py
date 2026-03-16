from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from .config import settings


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_API_KEY,
    )


def ensure_collection(client: QdrantClient) -> None:
    collections = client.get_collections().collections
    names = {c.name for c in collections}
    if settings.QDRANT_COLLECTION in names:
        return

    # Dense vector config (768d cosine) plus sparse vector slot called "sparse".
    vectors_config: dict[str, Any] = {
        "dense": qmodels.VectorParams(
            size=768,
            distance=qmodels.Distance.COSINE,
        )
    }

    sparse_vectors_config = {
        "sparse": qmodels.SparseVectorParams(
            index=qmodels.SparseIndexParams()
        )
    }

    client.recreate_collection(
        collection_name=settings.QDRANT_COLLECTION,
        vectors_config=vectors_config,
        sparse_vectors_config=sparse_vectors_config,
        on_disk_payload=True,
    )


def upsert_vectors(
    client: QdrantClient,
    points: list[qmodels.PointStruct],
) -> None:
    if not points:
        return
    client.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=points,
    )


def search_by_vector(
    client: QdrantClient,
    query_vector: list[float],
    limit: int,
    filters: qmodels.Filter | None = None,
) -> list[qmodels.ScoredPoint]:
    return client.search(
        collection_name=settings.QDRANT_COLLECTION,
        query_vector=("dense", query_vector),
        limit=limit,
        query_filter=filters,
    )


def search_by_point_id(
    client: QdrantClient,
    point_id: str,
    limit: int,
    filters: qmodels.Filter | None = None,
) -> list[qmodels.ScoredPoint]:
    return client.search(
        collection_name=settings.QDRANT_COLLECTION,
        query_vector=None,
        limit=limit,
        query_filter=filters,
        using="",
        search_params=qmodels.SearchParams(
            hnsw_ef=None,
            exact=None,
        ),
        with_payload=True,
        with_vectors=False,
        score_threshold=None,
        shard_key_selector=None,
        prefetch=None,
        lookup_from=qmodels.LookupLocation(
            collection_name=settings.QDRANT_COLLECTION,
            vector_name=None,
        ),
        vector_id=point_id,
    )

