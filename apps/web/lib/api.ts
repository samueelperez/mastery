import { env } from "@/lib/env"

/** All API calls cross-origin (Next on :3001, FastAPI on :8000) so the
 * BetterAuth session cookie only rides along when `credentials: 'include'`
 * is set. Wrap fetch once instead of repeating it 12 times. */
function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(input, { credentials: "include", ...init })
}

export interface CandleDTO {
  ts: string
  o: number
  h: number
  l: number
  c: number
  v: number
}

export interface OHLCVResponseDTO {
  exchange: string
  symbol: string
  timeframe: string
  count: number
  candles: CandleDTO[]
}

export async function fetchOhlcv(
  symbol: string,
  timeframe: string,
  opts: { limit?: number; signal?: AbortSignal } = {},
): Promise<OHLCVResponseDTO> {
  const limit = opts.limit ?? 1000
  const url = `${env.apiUrl}/ohlcv/${encodeURIComponent(symbol)}/${encodeURIComponent(timeframe)}?limit=${limit}`
  const res = await apiFetch(url, { signal: opts.signal })
  if (!res.ok) {
    throw new Error(`fetchOhlcv failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as OHLCVResponseDTO
}

// -----------------------------------------------------------------------------
// Health (used by the navbar's ConnectionPill to show data-plane state)
// -----------------------------------------------------------------------------

export interface HealthDTO {
  status: "ok" | "degraded"
  db: "ok" | "fail"
  valkey: "ok" | "fail"
  openrouter: "configured" | "missing"
  voyage: "configured" | "missing"
}

export async function fetchHealth(
  opts: { signal?: AbortSignal } = {},
): Promise<HealthDTO> {
  const res = await apiFetch(`${env.apiUrl}/health`, { signal: opts.signal })
  if (!res.ok) throw new Error(`fetchHealth failed: ${res.status}`)
  return (await res.json()) as HealthDTO
}

// -----------------------------------------------------------------------------
// Backtest research surface
// -----------------------------------------------------------------------------

export interface StrategyMetricsDTO {
  n_trades: number
  win_rate: number
  avg_win_R: number
  avg_loss_R: number
  expectancy_R: number
  sharpe: number
  sortino: number | null
  max_drawdown: number
  max_drawdown_duration_bars: number
  calmar: number
  mar: number
  ulcer_index: number
  tail_ratio: number
  skew: number
  kurtosis: number
  probabilistic_sharpe: number
  deflated_sharpe: number
  overfit_warning: boolean
  probability_of_overfit: number | null
}

export interface BacktestRunSummaryDTO {
  id: string
  strategy_id: string
  symbol: string
  timeframe: string
  range_start: string
  range_end: string
  fees_bps: number
  slippage_atr: number
  status: "running" | "done" | "error"
  created_at: string
  finished_at: string | null
  metrics: StrategyMetricsDTO | null
}

export interface BacktestRunDetailDTO extends BacktestRunSummaryDTO {
  params: Record<string, unknown>
  equity_curve: [string, number][]
}

export async function fetchBacktests(
  opts: {
    strategy_id?: string
    symbol?: string
    timeframe?: string
    limit?: number
    signal?: AbortSignal
  } = {},
): Promise<BacktestRunSummaryDTO[]> {
  const params = new URLSearchParams()
  if (opts.strategy_id) params.set("strategy_id", opts.strategy_id)
  if (opts.symbol) params.set("symbol", opts.symbol)
  if (opts.timeframe) params.set("timeframe", opts.timeframe)
  params.set("limit", String(opts.limit ?? 50))
  const res = await apiFetch(`${env.apiUrl}/backtests?${params}`, { signal: opts.signal })
  if (!res.ok) throw new Error(`fetchBacktests failed: ${res.status}`)
  return (await res.json()) as BacktestRunSummaryDTO[]
}

export async function fetchBacktest(
  runId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<BacktestRunDetailDTO> {
  const res = await apiFetch(`${env.apiUrl}/backtests/${encodeURIComponent(runId)}`, {
    signal: opts.signal,
  })
  if (!res.ok) throw new Error(`fetchBacktest failed: ${res.status}`)
  return (await res.json()) as BacktestRunDetailDTO
}

// -----------------------------------------------------------------------------
// Journal
// -----------------------------------------------------------------------------

export interface JournalTradeListRowDTO {
  id: string
  trade_ts: string
  symbol: string
  timeframe: string
  mode: string
  side: "long" | "short"
  entry_px: number
  exit_px: number | null
  size: number
  r_multiple: number | null
  setup_tag: string
  regime: string
  mistakes: string | null
}

export interface JournalTradeDetailDTO extends JournalTradeListRowDTO {
  summary_text: string
  summary_hash: string
  embedding_version: number
  news_24h: Record<string, unknown>
  features: Record<string, unknown>
}

export async function fetchJournalTrades(
  opts: { mode?: string; regime?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<JournalTradeListRowDTO[]> {
  const params = new URLSearchParams()
  if (opts.mode) params.set("mode", opts.mode)
  if (opts.regime) params.set("regime", opts.regime)
  params.set("limit", String(opts.limit ?? 50))
  const res = await apiFetch(`${env.apiUrl}/journal/trades?${params}`, { signal: opts.signal })
  if (!res.ok) throw new Error(`fetchJournalTrades failed: ${res.status}`)
  return (await res.json()) as JournalTradeListRowDTO[]
}

export async function fetchJournalTrade(
  tradeId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<JournalTradeDetailDTO> {
  const res = await apiFetch(`${env.apiUrl}/journal/trades/${encodeURIComponent(tradeId)}`, {
    signal: opts.signal,
  })
  if (!res.ok) throw new Error(`fetchJournalTrade failed: ${res.status}`)
  return (await res.json()) as JournalTradeDetailDTO
}

// -----------------------------------------------------------------------------
// Alerts (F3)
// -----------------------------------------------------------------------------

export interface AlertConditionDTO {
  left: string
  op: "<" | "<=" | "==" | ">=" | ">" | "cross_above" | "cross_below"
  right: number | string
}

export interface AlertSpecDTO {
  kind: "candle_close"
  symbol: string
  timeframe: "15m" | "1h" | "4h" | "1d"
  indicators: { name: string; length?: number; source?: string }[]
  conditions: AlertConditionDTO[]
  logic: "all" | "any"
}

export interface AlertRuleDTO {
  id: string
  name: string
  spec: AlertSpecDTO
  enabled: boolean
  cooldown_s: number
  last_fired_at: string | null
  created_at: string
  updated_at: string
}

export interface AlertEventDTO {
  id: number
  rule_id: string | null
  kind: "rule_match" | "bias_promoted"
  severity: "low" | "medium" | "high"
  fired_at: string
  snapshot: Record<string, unknown>
  seen_at: string | null
}

export async function fetchAlerts(
  opts: { only_enabled?: boolean; signal?: AbortSignal } = {},
): Promise<AlertRuleDTO[]> {
  const params = new URLSearchParams()
  if (opts.only_enabled) params.set("only_enabled", "true")
  const res = await apiFetch(`${env.apiUrl}/alerts?${params}`, { signal: opts.signal })
  if (!res.ok) throw new Error(`fetchAlerts failed: ${res.status}`)
  return (await res.json()) as AlertRuleDTO[]
}

export async function createAlert(
  body: { name: string; spec: AlertSpecDTO; cooldown_s?: number },
): Promise<AlertRuleDTO> {
  const res = await apiFetch(`${env.apiUrl}/alerts`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`createAlert failed: ${res.status}`)
  return (await res.json()) as AlertRuleDTO
}

export async function patchAlert(
  id: string,
  body: { enabled?: boolean; cooldown_s?: number },
): Promise<AlertRuleDTO> {
  const res = await apiFetch(`${env.apiUrl}/alerts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`patchAlert failed: ${res.status}`)
  return (await res.json()) as AlertRuleDTO
}

export async function deleteAlert(id: string): Promise<void> {
  const res = await apiFetch(`${env.apiUrl}/alerts/${encodeURIComponent(id)}`, {
    method: "DELETE",
  })
  if (!res.ok && res.status !== 204) throw new Error(`deleteAlert failed: ${res.status}`)
}

export async function fetchAlertEvents(
  opts: { only_unread?: boolean; limit?: number; signal?: AbortSignal } = {},
): Promise<AlertEventDTO[]> {
  const params = new URLSearchParams()
  if (opts.only_unread) params.set("only_unread", "true")
  if (opts.limit) params.set("limit", String(opts.limit))
  const res = await apiFetch(`${env.apiUrl}/alerts/events?${params}`, {
    signal: opts.signal,
  })
  if (!res.ok) throw new Error(`fetchAlertEvents failed: ${res.status}`)
  return (await res.json()) as AlertEventDTO[]
}

export async function markEventSeen(eventId: number): Promise<void> {
  const res = await apiFetch(
    `${env.apiUrl}/alerts/events/${eventId}/seen`,
    { method: "POST" },
  )
  if (!res.ok && res.status !== 204) {
    throw new Error(`markEventSeen failed: ${res.status}`)
  }
}
