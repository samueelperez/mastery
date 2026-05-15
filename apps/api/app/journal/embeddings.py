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
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import get_settings

EMBEDDING_MODEL = "voyage-4-large"
EMBEDDING_DIM = 1024  # voyage-4 family Matryoshka default; matches DB column
INPUT_TYPE_DOCUMENT = "document"  # for indexing
INPUT_TYPE_QUERY = "query"  # for retrieval — voyage uses different conditioning
EMBED_TIMEOUT_S = 30.0  # audit fix 2026-05: hard cap por call (sin esto, una
                       # Voyage colgada bloquea log_trade tool indefinidamente).
VOYAGE_BATCH_MAX = 128  # batch limit declared by Voyage; we auto-chunk above.

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


def _is_transient(exc: BaseException) -> bool:
    """Retry sólo en errores transitorios (429, 5xx, network). Audit fix
    2026-05: antes se reintentaba sobre `Exception` → re-tries de 400s y 401s
    inflaban latencia 14s en errores que son inmediatos."""
    # Timeouts asyncio.
    if isinstance(exc, TimeoutError):
        return True
    # httpx network errors.
    name = type(exc).__name__
    if name in {
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
    }:
        return True
    # Voyage SDK exposes specific subclasses; nombre-based check evita import
    # estricto si la SDK reorganiza el módulo.
    if name in {"RateLimitError", "ServerError", "APIStatusError"}:
        # `APIStatusError` puede ser 4xx o 5xx — sólo retry si parece 5xx.
        status = getattr(exc, "status_code", None)
        if status is not None:
            return 500 <= int(status) < 600 or int(status) == 429
        return True
    return False


def _retrying() -> AsyncRetrying:
    """Build a fresh AsyncRetrying iterator per call (tenacity instances aren't
    re-iterable). Retry sólo en errores transitorios (audit fix 2026-05).
    """
    return AsyncRetrying(
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=0.5, max=8.0),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )


async def embed_one(text: str, *, input_type: str = INPUT_TYPE_DOCUMENT) -> list[float]:
    """Embed a single string. Use input_type='query' for retrieval queries."""
    out = await embed_batch([text], input_type=input_type)
    return out[0]


async def embed_batch(
    texts: Sequence[str], *, input_type: str = INPUT_TYPE_DOCUMENT
) -> list[list[float]]:
    """Embed N texts. Auto-chunked en lotes de VOYAGE_BATCH_MAX (audit fix
    2026-05). Devuelve dense float lists de longitud EMBEDDING_DIM en orden
    de entrada; valida el shape antes de devolver para fallar loud si Voyage
    cambia su default Matryoshka (errores DB downstream son opacos)."""
    if not texts:
        return []

    client = _get_client()
    all_embeddings: list[list[float]] = []
    total_tokens = 0
    text_list = list(texts)
    for offset in range(0, len(text_list), VOYAGE_BATCH_MAX):
        chunk = text_list[offset : offset + VOYAGE_BATCH_MAX]
        result = None
        async for attempt in _retrying():
            with attempt:
                # Hard timeout por call — sin esto, una Voyage colgada
                # bloquea el caller (log_trade tool, scout) indefinidamente.
                result = await asyncio.wait_for(
                    client.embed(
                        chunk,
                        model=EMBEDDING_MODEL,
                        input_type=input_type,
                        output_dimension=EMBEDDING_DIM,
                    ),
                    timeout=EMBED_TIMEOUT_S,
                )
        assert result is not None
        embeddings_chunk: list[list[float]] = result.embeddings
        # Shape validation — falla loud si voyage cambia el output_dimension
        # default o devuelve menos vectores que textos.
        if len(embeddings_chunk) != len(chunk):
            raise RuntimeError(
                f"voyage returned {len(embeddings_chunk)} embeddings for "
                f"{len(chunk)} texts (chunk offset {offset})"
            )
        for i, vec in enumerate(embeddings_chunk):
            if len(vec) != EMBEDDING_DIM:
                raise RuntimeError(
                    f"voyage embedding dim mismatch at text {offset + i}: "
                    f"got {len(vec)}, expected {EMBEDDING_DIM}"
                )
        all_embeddings.extend(embeddings_chunk)
        total_tokens += int(getattr(result, "total_tokens", 0) or 0)
    log.info(
        "voyage.embed",
        model=EMBEDDING_MODEL,
        input_type=input_type,
        n_texts=len(texts),
        n_chunks=(len(texts) + VOYAGE_BATCH_MAX - 1) // VOYAGE_BATCH_MAX,
        total_tokens=total_tokens,
    )
    return all_embeddings


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
