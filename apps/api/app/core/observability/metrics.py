"""Centralized Prometheus metrics for the Trading Copilot.

All metrics live here so they're easy to inventory and so we never
double-declare a counter (Prometheus errors out on registry collision).
Each metric has the same shape: `mt_<area>_<event>{label=value}`.

Why these specific metrics:
- `mt_scout_drops_total{reason}` — single most valuable signal for ops.
  If scout drop rate spikes, scanner rules are too noisy OR cooldown is
  permanently engaged OR agent is failing. Each `reason` distinguishes.
- `mt_scout_accepted_total` — denominator for "scout signal-to-noise".
- `mt_agent_invocations_total{kind}` / `mt_agent_invocation_seconds`
  — agent cost + latency. Histogram buckets sized for the typical
  pydantic-ai turn (3-25s).
- `mt_risk_actions_total{action}` — RiskManager activity. Sudden zero
  means the runtime stopped firing; sudden surge means a regime shift.
- `mt_gap_fill_inserts_total` — mid-runtime gap-fill inserts (>0 means
  WS instability). Should be near-zero in steady state.
- `mt_telegram_sends_total{outcome}` — delivery health. Fails should
  trip an ops alert.
- `mt_setup_transitions_total{from,to,event}` — lifecycle audit. Useful
  for cohort analysis (pending→cancelled rate vs pending→active rate).

Cardinality discipline: labels are bounded enums or fixed strings, NEVER
user IDs / symbols / setup IDs (would explode the registry).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# -----------------------------------------------------------------------------
# Scout dispatcher
# -----------------------------------------------------------------------------

scout_drops_total = Counter(
    "mt_scout_drops_total",
    "Scout dispatcher drops by reason — denominator for signal-to-noise.",
    ["reason"],
)

scout_accepted_total = Counter(
    "mt_scout_accepted_total",
    "Scout dispatcher accepts that persisted a setup.",
)


# -----------------------------------------------------------------------------
# Agent invocations (both interactive chat AND scout-triggered)
# -----------------------------------------------------------------------------

agent_invocations_total = Counter(
    "mt_agent_invocations_total",
    "Agent runs by kind (chat | scout | review | post_mortem).",
    ["kind", "outcome"],  # outcome ∈ {trade_idea, brief, text, error}
)

agent_invocation_seconds = Histogram(
    "mt_agent_invocation_seconds",
    "Agent run wall time. Bucketed for typical pydantic-ai turns.",
    ["kind"],
    buckets=(1.0, 2.5, 5.0, 10.0, 15.0, 25.0, 45.0, 90.0),
)


# -----------------------------------------------------------------------------
# Risk Manager
# -----------------------------------------------------------------------------

risk_actions_total = Counter(
    "mt_risk_actions_total",
    "RiskManager actions applied to setups.",
    ["action"],  # action ∈ {be_moved, trailing_updated, time_stopped}
)


# -----------------------------------------------------------------------------
# Ingestion / gap-fill
# -----------------------------------------------------------------------------

gap_fill_inserts_total = Counter(
    "mt_gap_fill_inserts_total",
    "Candles inserted by _fill_gap. >0 mid-runtime signals WS instability.",
    ["symbol", "timeframe", "phase"],  # phase ∈ {startup, reconnect}
)


# -----------------------------------------------------------------------------
# Telegram
# -----------------------------------------------------------------------------

telegram_sends_total = Counter(
    "mt_telegram_sends_total",
    "Telegram bot send outcomes.",
    ["method", "outcome"],  # outcome ∈ {ok, http_error, api_error, transport_error, no_token}
)


# -----------------------------------------------------------------------------
# Setup lifecycle
# -----------------------------------------------------------------------------

setup_transitions_total = Counter(
    "mt_setup_transitions_total",
    "Setup state transitions by event kind.",
    ["from_status", "to_status", "event"],
)

# Counts how each scout-proposed setup resolves its approval gate.
# outcome ∈ {approved, rejected, timeout, auto_approved}.
# - approved / rejected: human pulled the trigger in Telegram or web.
# - auto_approved: dispatcher bypassed the human step because all gates were
#   green and sources_agreement met the AUTO_APPROVE_MIN_AGREEMENT threshold.
# - timeout: scheduler cancelled the setup after APPROVAL_TIMEOUT_SECONDS
#   without operator response (wiring pending — knob exposed in Settings).
setup_approval_outcome_total = Counter(
    "mt_setup_approval_outcome_total",
    "Setup approval resolutions by outcome.",
    ["outcome"],
)


# -----------------------------------------------------------------------------
# Runtime health (alerts/setups/risk runtimes alive)
# -----------------------------------------------------------------------------

runtime_streams_alive = Gauge(
    "mt_runtime_streams_alive",
    "Number of (symbol, timeframe) live ingestion streams currently subscribed.",
)


# -----------------------------------------------------------------------------
# Cerebro 1 — Liquidation Heatmap Engine
# -----------------------------------------------------------------------------
# Labels bounded: symbol ∈ {BTCUSDT,ETHUSDT,SOLUSDT}, timeframe ∈ {1h,4h,1d},
# provider ∈ {A_derived,B_hyperliquid,D_coinglass}.

liq_snapshots_total = Counter(
    "mt_liq_snapshots_total",
    "HeatmapService.get_snapshot returns by (symbol, timeframe, outcome).",
    ["symbol", "timeframe", "outcome"],  # outcome ∈ {ok, empty, degraded}
)

liq_snapshot_latency_seconds = Histogram(
    "mt_liq_snapshot_latency_seconds",
    "Per-provider get_heatmap wall time inside HeatmapService.",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0),
)

liq_active_addresses = Gauge(
    "mt_liq_active_addresses",
    "Total rows in hyperliquid_known_addresses. Drives Provider B coverage.",
)

liq_provider_errors_total = Counter(
    "mt_liq_provider_errors_total",
    "Provider failures inside HeatmapService.",
    ["provider", "kind"],  # kind ∈ {timeout, exception, stale}
)

# Background snapshot scheduler (HM-PR1).
liq_scheduler_runs_total = Counter(
    "mt_liq_scheduler_runs_total",
    "LiquidationSnapshotScheduler run outcomes per (symbol, timeframe, outcome).",
    ["symbol", "timeframe", "outcome"],  # outcome ∈ {ok, empty, error, no_price}
)

liq_scheduler_latency_seconds = Histogram(
    "mt_liq_scheduler_latency_seconds",
    "Wall time for a single scheduled HeatmapService.get_snapshot call.",
    ["symbol", "timeframe"],
    buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 10.0),
)
