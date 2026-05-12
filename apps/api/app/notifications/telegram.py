"""C.3 — Telegram Bot API client + setup-alert formatter.

Pure HTTPX-based client (we don't pull in `python-telegram-bot` because the
heavyweight bot framework is overkill — we only need 3 endpoints: sendMessage,
answerCallbackQuery, and the webhook receiver in `app/api/notifications.py`).

Design:
- `send_setup_alert(chat_id, setup)` posts a markdown-formatted message with
  inline buttons [Approve / Reject / Snooze]. Idempotent at the Telegram
  layer (re-call sends a NEW message; the caller should de-dup by setup_id).
- `answer_callback(callback_id)` acknowledges a button press so the spinner
  in Telegram clears immediately.
- All API calls are wrapped in try/except so a Telegram outage NEVER crashes
  the scout dispatcher. On failure, we log + return False; the setup persists
  regardless.

Webhook secret: Telegram pings our `/telegram/webhook` endpoint with the
secret in `X-Telegram-Bot-Api-Secret-Token`. The endpoint validates the
header before processing.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.agent.models import TradeIdea
from app.core.config import get_settings
from app.core.observability.metrics import telegram_sends_total

log = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Low-level API client
# -----------------------------------------------------------------------------


def _api_base() -> str | None:
    """Returns the Telegram Bot API base URL or None when no token is set.
    Callers check for None and degrade gracefully — Telegram is optional."""
    token = get_settings().telegram_bot_token
    if not token:
        return None
    return f"https://api.telegram.org/bot{token}"


async def _post(method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to a Telegram method. Returns the `result` field on success;
    None on any error (network, 4xx/5xx, bad JSON). Logs with structured
    fields so a TG outage is auditable but does not propagate. Each outcome
    increments `mt_telegram_sends_total{outcome=…}` so an ops alert can fire
    on `http_error` or `transport_error` spikes."""
    base = _api_base()
    if base is None:
        telegram_sends_total.labels(method=method, outcome="no_token").inc()
        log.debug("telegram.skip.no_token", method=method)
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{base}/{method}", json=payload)
        if resp.status_code != 200:
            telegram_sends_total.labels(
                method=method, outcome="http_error"
            ).inc()
            log.warning(
                "telegram.http_error",
                method=method,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
        data = resp.json()
        if not data.get("ok"):
            telegram_sends_total.labels(
                method=method, outcome="api_error"
            ).inc()
            log.warning(
                "telegram.api_error",
                method=method,
                description=data.get("description", "?"),
            )
            return None
        result = data.get("result")
        telegram_sends_total.labels(method=method, outcome="ok").inc()
        return result if isinstance(result, dict) else None
    except (httpx.HTTPError, ValueError) as exc:
        telegram_sends_total.labels(
            method=method, outcome="transport_error"
        ).inc()
        log.warning(
            "telegram.transport_error",
            method=method,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


# -----------------------------------------------------------------------------
# Formatting helpers
# -----------------------------------------------------------------------------


def _escape_md(text: str) -> str:
    """MarkdownV2 escape — Telegram is strict about reserved chars.
    Cheaper than importing a full markdown lib; covers the chars we generate."""
    reserved = r"_*[]()~`>#+-=|{}.!\\"
    return "".join("\\" + c if c in reserved else c for c in text)


def _fmt_price(p: float) -> str:
    """Tight numeric format for prices. Strips trailing zeros so 80250.0 →
    80250 and 0.12340 → 0.1234."""
    if p >= 1000:
        return f"{p:,.1f}".rstrip("0").rstrip(".")
    if p >= 1:
        return f"{p:,.3f}".rstrip("0").rstrip(".")
    return f"{p:,.6f}".rstrip("0").rstrip(".")


def format_setup_alert(setup_id: str, idea: TradeIdea) -> str:
    """MarkdownV2 message body for a scout-proposed setup. Short enough to
    read on mobile in <5 seconds — the bot's whole value is friction <10s
    from notification to approval decision.

    Scout dispatcher only invokes this for actionable ideas (long/short
    with entry+SL+TPs), so the optional fields are guaranteed populated.
    The fallbacks below are belt-and-suspenders for the few edge cases
    where a caller might hand us a malformed idea.
    """
    direction = "📈 LONG" if idea.direction == "long" else "📉 SHORT"
    entry_str = _fmt_price(idea.entry) if idea.entry is not None else "—"
    sl_str = (
        f"SL `{_fmt_price(idea.stop_loss)}`"
        if idea.stop_loss is not None
        else "SL —"
    )
    tps_str = " · ".join(_fmt_price(t.price) for t in idea.targets) or "—"
    conf = idea.confidence or "—"
    return (
        f"*{_escape_md(direction)}* `{_escape_md(idea.symbol)}` "
        f"`{_escape_md(idea.timeframe)}`\n"
        f"Entry `{_escape_md(entry_str)}` · "
        f"{_escape_md(sl_str)}\n"
        f"TPs `{_escape_md(tps_str)}`\n"
        f"Confidence `{_escape_md(conf)}` · Regime "
        f"`{_escape_md(idea.regime.label)}`\n\n"
        f"_{_escape_md(idea.summary_es[:280])}_"
    )


def _inline_kb_for_setup(setup_id: str) -> dict[str, Any]:
    """Inline keyboard JSON for approve/reject/snooze. callback_data is
    constrained to 64 bytes by Telegram, so we use short prefixes."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"a:{setup_id}"},
                {"text": "❌ Reject", "callback_data": f"r:{setup_id}"},
            ],
            [
                {"text": "🔗 Open chart", "url": _chart_url(setup_id)},
            ],
        ]
    }


def _chart_url(setup_id: str) -> str:
    base = get_settings().telegram_app_base_url.rstrip("/")
    return f"{base}/journal?setup={setup_id}"


# -----------------------------------------------------------------------------
# Public actions
# -----------------------------------------------------------------------------


async def send_setup_alert(
    *, chat_id: str, setup_id: str, idea: TradeIdea
) -> bool:
    """Sends the Approve/Reject message. Returns True on success, False on
    any failure (no exception leaks). Caller can fire-and-forget safely."""
    payload = {
        "chat_id": chat_id,
        "text": format_setup_alert(setup_id, idea),
        "parse_mode": "MarkdownV2",
        "reply_markup": _inline_kb_for_setup(setup_id),
    }
    result = await _post("sendMessage", payload)
    if result is None:
        log.warning("telegram.send_setup_alert.failed", setup_id=setup_id)
        return False
    log.info(
        "telegram.send_setup_alert.sent",
        setup_id=setup_id,
        message_id=result.get("message_id"),
    )
    return True


async def send_text(chat_id: str, text: str) -> bool:
    """Plain-text message helper (used by the bind flow + ack messages)."""
    payload = {"chat_id": chat_id, "text": text}
    return await _post("sendMessage", payload) is not None


async def answer_callback(callback_id: str, text: str | None = None) -> bool:
    """Acknowledge a button press so Telegram clears the spinner. The
    optional `text` shows as a transient toast in the user's chat."""
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text is not None:
        payload["text"] = text
    return await _post("answerCallbackQuery", payload) is not None
