"""Build the canonical text representation of a trade for embedding + BM25.

The template is opinionated by the F2 research finding: most-discriminative fields
first (setup_tag + regime), then free-text outcome and post-mortem, then news
context. Numeric values (entry, exit, R) are deliberately NOT embedded — vector
models are bad at numbers; we filter on those at SQL time.
"""

from __future__ import annotations

import hashlib
from typing import TypedDict


class TradeSummaryInput(TypedDict, total=False):
    setup_tag: str
    regime: str
    side: str
    symbol: str
    timeframe: str
    r_multiple: float | None
    mistakes: str | None
    news_24h: dict[str, list[str]] | None  # {"headlines": [...]} optional


def build_summary_text(t: TradeSummaryInput) -> str:
    """Concatenate the most-discriminative fields first.

    Format:
        [setup_tag] | [regime] | [symbol tf side outcome] | mistakes: ... | news: ...
    """
    setup = t.get("setup_tag", "no_setup")
    regime = t.get("regime", "unknown_regime")
    symbol = t.get("symbol", "")
    tf = t.get("timeframe", "")
    side = t.get("side", "")
    r = t.get("r_multiple")
    if r is None:
        outcome = "open"
    elif r > 0.05:
        outcome = f"win {r:+.2f}R"
    elif r < -0.05:
        outcome = f"loss {r:+.2f}R"
    else:
        outcome = "scratch"
    head = f"{setup} | {regime} | {symbol} {tf} {side} {outcome}".strip()

    parts = [head]
    if mistakes := (t.get("mistakes") or "").strip():
        parts.append(f"mistakes: {mistakes}")
    news = t.get("news_24h") or {}
    headlines = news.get("headlines") if isinstance(news, dict) else None
    if headlines:
        joined = " · ".join(str(h) for h in list(headlines)[:3])
        parts.append(f"news: {joined}")
    return " | ".join(parts)


def hash_summary(text: str) -> str:
    """Stable hash for staleness detection. Stored in journal_trades.summary_hash.

    Whitespace-normalized so that purely cosmetic edits (e.g., user adds an extra
    space to `mistakes`) don't trigger a redundant re-embed. Anything that
    actually changes meaning still produces a different hash.
    """
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
