/** Mapeo del `toolName` interno (snake_case en inglés) a una etiqueta legible
 *  en español. Centralizado para que cards, banners, sources strip y
 *  ToolPart genérico muestren la misma traducción.
 *
 *  Orden alfabético del nombre interno para que sea fácil mantener. */
const TOOL_LABELS: Record<string, string> = {
  compute_panel: "Calcular indicadores",
  create_alert: "Crear alerta",
  detect_bias_patterns: "Detectar sesgos psicológicos",
  final_result: "Idea de trade",
  get_indicators: "Calcular indicadores",
  get_market_structure: "Analizar estructura",
  get_multi_tf_confluence: "Evaluar confluencia",
  get_ohlcv: "Cargar velas",
  get_similar_past_trades: "Buscar trades similares",
  get_strategy_metrics: "Métricas de estrategia",
  journal_query: "Buscar en el diario",
  list_alerts: "Listar alertas",
  log_trade: "Registrar operación",
  run_backtest: "Ejecutar backtest",
}

/** Traduce un toolName a español. Si no está en la tabla, devuelve el nombre
 *  con guiones bajos sustituidos por espacios y la primera letra mayúscula. */
export function toolLabel(name: string): string {
  if (TOOL_LABELS[name]) return TOOL_LABELS[name]
  const pretty = name.replace(/_/g, " ")
  return pretty.charAt(0).toUpperCase() + pretty.slice(1)
}
