"""voyage-4-large embeddings for the trade journal.

We use the official `voyageai.AsyncClient` and pin the model + output_dimension
so re-embedding the same text always yields the same vector shape (1024 dims),
matching the `embedding vector(1024)` column in the migration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import structlog
import voyageai
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import get_settings

EMBEDDING_MODEL = "voyage-4-large"
EMBEDDING_DIM = 1024  # voyage-4 family Matryoshka default; matches DB column
INPUT_TYPE_DOCUMENT = "document"  # for indexing
INPUT_TYPE_QUERY = "query"  # for retrieval — voyage uses different conditioning

log = structlog.get_logger(__name__)

_client: voyageai.AsyncClient | None = None  # type: ignore[name-defined]


def _get_client() -> voyageai.AsyncClient:  # type: ignore[name-defined]
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.voyage_api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY is not set. Add it to apps/api/.env "
                "(get a key at https://dash.voyageai.com)."
            )
        _client = voyageai.AsyncClient(api_key=settings.voyage_api_key)  # type: ignore[attr-defined]
    return _client


def _retrying() -> AsyncRetrying:
    """Build a fresh AsyncRetrying iterator per call (tenacity instances aren't
    re-iterable). Voyage SDK raises plain Exception subclasses; we retry on
    anything that isn't a programming error to ride out 429s / transient 5xx.
    """
    return AsyncRetrying(
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=0.5, max=8.0),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )


async def embed_one(text: str, *, input_type: str = INPUT_TYPE_DOCUMENT) -> list[float]:
    """Embed a single string. Use input_type='query' for retrieval queries."""
    out = await embed_batch([text], input_type=input_type)
    return out[0]


async def embed_batch(
    texts: Sequence[str], *, input_type: str = INPUT_TYPE_DOCUMENT
) -> list[list[float]]:
    """Embed up to ~128 texts at once (voyage's batch limit).

    Returns dense float lists of length EMBEDDING_DIM. Caller is responsible for
    chunking larger batches; we don't auto-chunk to keep accounting transparent.
    """
    client = _get_client()
    result = None
    async for attempt in _retrying():
        with attempt:
            result = await client.embed(
                list(texts),
                model=EMBEDDING_MODEL,
                input_type=input_type,
                output_dimension=EMBEDDING_DIM,
            )
    assert result is not None  # tenacity reraise=True guarantees we got here only on success
    log.info(
        "voyage.embed",
        model=EMBEDDING_MODEL,
        input_type=input_type,
        n_texts=len(texts),
        total_tokens=getattr(result, "total_tokens", None),
    )
    embeddings: list[list[float]] = result.embeddings
    return embeddings


async def close() -> None:
    """Close the global async client. Wire to FastAPI lifespan if desired."""
    global _client
    if _client is not None:
        # voyageai 0.3.x's AsyncClient holds an httpx client; close gracefully.
        close_fn = getattr(_client, "aclose", None) or getattr(_client, "close", None)
        if close_fn is not None:
            try:
                result = close_fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.warning("voyage.close.error", error=str(e))
        _client = None
