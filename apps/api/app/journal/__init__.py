"""Trade journal — embeddings, bias detection, retrieval.

The journal is the trader's unique corpus: each closed trade becomes a row
with structured metadata + a free-text post-mortem. We embed the concatenated
summary so the agent's `get_similar_past_trades` tool can retrieve historical
analogues to the current setup.
"""

from app.journal.bias_detector import BiasFlag, run_for_user
from app.journal.embeddings import EMBEDDING_DIM, embed_batch, embed_one
from app.journal.summary import build_summary_text, hash_summary

__all__ = [
    "EMBEDDING_DIM",
    "BiasFlag",
    "build_summary_text",
    "embed_batch",
    "embed_one",
    "hash_summary",
    "run_for_user",
]
