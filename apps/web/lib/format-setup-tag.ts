/**
 * Convierte un `setup_tag` técnico (snake_case, e.g. `reclaim_ema21`) a un
 * label legible (e.g. `Reclaim EMA 21`).
 *
 * Reglas:
 * 1. Split por `_`.
 * 2. Dentro de cada palabra, split letter→digit (`ema21` → `ema` + `21`).
 * 3. Si la sub-palabra es un acrónimo conocido (EMA, SMA, ATR, RSI, BB,
 *    VWAP, TP, SL, MA, MACD, OB, OS), upper-case completo.
 * 4. Si es solo dígitos o termina en una unidad (`4h`, `15m`, `1d`),
 *    deja minúscula.
 * 5. Resto: Title Case.
 *
 * El raw `setup_tag` sigue siendo el id real en BD — esto es solo
 * presentación. Si más adelante el user quiere atar nombres custom, se
 * añade un campo `display_name` en backend; mientras, esta función
 * resuelve el 95% de casos sin schema change.
 */

const ACRONYMS = new Set([
  "ema",
  "sma",
  "rsi",
  "atr",
  "ma",
  "bb",
  "vwap",
  "tp",
  "sl",
  "macd",
  "ob",
  "os",
  "obv",
  "adx",
])

/** Timeframes que mantienen formato compacto: "4h", "15m", "1d". */
const TIMEFRAME_RE = /^\d+[hmdw]$/i

export function formatSetupTag(tag: string): string {
  if (!tag) return tag
  return tag
    .split("_")
    .map(formatWord)
    .filter((s) => s.length > 0)
    .join(" ")
}

function formatWord(word: string): string {
  if (!word) return ""
  // Mantener timeframes tal cual ("4h", "15m").
  if (TIMEFRAME_RE.test(word)) return word.toLowerCase()
  // Split en boundary letra→dígito ("ema21" → ["ema", "21"]).
  const parts = word.split(/(?<=[a-z])(?=\d)/i)
  return parts
    .map(formatPart)
    .filter((s) => s.length > 0)
    .join(" ")
}

function formatPart(part: string): string {
  if (!part) return ""
  const lower = part.toLowerCase()
  if (ACRONYMS.has(lower)) return lower.toUpperCase()
  if (TIMEFRAME_RE.test(part)) return part.toLowerCase()
  // Solo dígitos: deja como está.
  if (/^\d+$/.test(part)) return part
  // Title Case.
  return part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()
}
