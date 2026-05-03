import { env } from "@/lib/env"

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
  const res = await fetch(url, { signal: opts.signal })
  if (!res.ok) {
    throw new Error(`fetchOhlcv failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as OHLCVResponseDTO
}

// -----------------------------------------------------------------------------
// Health (used by the navbar's ConnectionPill to show data-plane state)
// -----------------------------------------------------------------------------

export interface HealthDTO {
  status: "ok" | string
  db: "ok" | string
  valkey: "ok" | string
}

export async function fetchHealth(
  opts: { signal?: AbortSignal } = {},
): Promise<HealthDTO> {
  const res = await fetch(`${env.apiUrl}/health`, { signal: opts.signal })
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
  sortino: number
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
  const res = await fetch(`${env.apiUrl}/backtests?${params}`, { signal: opts.signal })
  if (!res.ok) throw new Error(`fetchBacktests failed: ${res.status}`)
  return (await res.json()) as BacktestRunSummaryDTO[]
}

export async function fetchBacktest(
  runId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<BacktestRunDetailDTO> {
  const res = await fetch(`${env.apiUrl}/backtests/${encodeURIComponent(runId)}`, {
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
  const res = await fetch(`${env.apiUrl}/journal/trades?${params}`, { signal: opts.signal })
  if (!res.ok) throw new Error(`fetchJournalTrades failed: ${res.status}`)
  return (await res.json()) as JournalTradeListRowDTO[]
}

export async function fetchJournalTrade(
  tradeId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<JournalTradeDetailDTO> {
  const res = await fetch(`${env.apiUrl}/journal/trades/${encodeURIComponent(tradeId)}`, {
    signal: opts.signal,
  })
  if (!res.ok) throw new Error(`fetchJournalTrade failed: ${res.status}`)
  return (await res.json()) as JournalTradeDetailDTO
}
