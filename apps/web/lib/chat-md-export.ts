/**
 * Serializa un array de UIMessage (vercel-ai SDK v6) a Markdown legible.
 *
 * El objetivo es que un humano (o un LLM) pueda leer el .md y reconstruir:
 *   - Qué dijo el usuario en cada turn.
 *   - Qué herramientas llamó el agente, en qué orden, con qué inputs.
 *   - Qué outputs devolvieron las tools (con su shape).
 *   - Si hubo errores (tool errors o status=error) y dónde.
 *   - Qué tipo de respuesta principal renderizó la UI: prose libre,
 *     TradeIdeaCard, BriefAnalysisCard, AlertCreatedCard, etc. — vía
 *     `tool-final_result_*` o branches en CopilotChat.
 *   - Razonamiento del modelo (parts type=reasoning).
 *
 * Diseñado para ser pegado en un chat con Claude para iterar diseño.
 */

import type { UIMessage, ToolUIPart, DynamicToolUIPart } from "ai"

import { isBriefAnalysis, isTradeIdea } from "./chat-types"

const MAX_PRETTY_JSON_CHARS = 1500
const MAX_OUTPUT_PRETTY_CHARS = 2500

function pretty(value: unknown, max = MAX_PRETTY_JSON_CHARS): string {
  if (value === undefined) return "_undefined_"
  let str: string
  try {
    str = JSON.stringify(value, null, 2)
  } catch {
    str = String(value)
  }
  if (str.length <= max) return str
  return `${str.slice(0, max)}\n…(truncado, ${str.length} chars totales)`
}

function tsLine(): string {
  return new Date().toISOString()
}

function describeFinalResult(
  toolName: string,
  input: unknown,
): { label: string; cardKind: string; summaryLines: string[] } {
  // Pydantic-AI con union output namespacea: final_result_TradeIdea / _BriefAnalysis.
  if (isTradeIdea(input)) {
    const idea = input
    const lines: string[] = [
      `- symbol: ${idea.symbol} · timeframe: ${idea.timeframe}`,
      `- direction: ${idea.direction}`,
      `- regime: ${idea.regime?.label ?? "?"}`,
      `- confidence: ${idea.confidence}`,
      `- entry: ${idea.entry ?? "—"} · invalidation: ${idea.invalidation ?? "—"}`,
      `- targets: ${idea.targets.length} → ${idea.targets.map((t) => `${t.label}@${t.price}`).join(", ")}`,
      `- scenarios: ${idea.scenarios?.length ?? 0}`,
      `- position_size_pct: ${idea.position_size_pct ?? "—"} · leverage_x: ${idea.leverage_x ?? "—"}`,
      `- summary_es (len ${idea.summary_es.length}): ${idea.summary_es}`,
      `- risk_notes: ${idea.risk_notes}`,
    ]
    if (idea.bias_alert) {
      lines.push(
        `- bias_alert: ${idea.bias_alert.severity} · ${idea.bias_alert.kinds.join("+")} — ${idea.bias_alert.message}`,
      )
    }
    return {
      label: `Final result · TradeIdea (${idea.direction.toUpperCase()})`,
      cardKind: "TradeIdeaCard",
      summaryLines: lines,
    }
  }
  if (isBriefAnalysis(input)) {
    const brief = input
    const lines: string[] = [
      `- symbol: ${brief.symbol} · timeframe: ${brief.timeframe}`,
      `- confidence: ${brief.confidence}`,
      `- verdict_es (len ${brief.verdict_es.length}): ${brief.verdict_es}`,
      `- catalyst_es (len ${brief.catalyst_es.length}): ${brief.catalyst_es}`,
      `- risk_es (len ${brief.risk_es.length}): ${brief.risk_es}`,
      `- key_levels: ${brief.key_levels.length} → ${brief.key_levels.map((l) => `${l.kind}:${l.label}@${l.price}`).join(", ")}`,
    ]
    if (brief.bias_alert) {
      lines.push(
        `- bias_alert: ${brief.bias_alert.severity} · ${brief.bias_alert.kinds.join("+")} — ${brief.bias_alert.message}`,
      )
    }
    return {
      label: "Final result · BriefAnalysis (exploratorio)",
      cardKind: "BriefAnalysisCard",
      summaryLines: lines,
    }
  }
  return {
    label: `Final result · ${toolName} (shape no reconocido)`,
    cardKind: "ToolPart genérico",
    summaryLines: [],
  }
}

function renderToolPart(
  part: ToolUIPart | DynamicToolUIPart,
  resolvedToolName: string,
): string {
  const lines: string[] = []
  const isFinal = resolvedToolName.startsWith("final_result")
  const heading = isFinal
    ? `### 🎯 ${resolvedToolName}`
    : `### 🔧 tool · ${resolvedToolName}`
  lines.push(heading)

  const state = "state" in part ? part.state : "input-available"
  lines.push(`- state: \`${state}\``)
  if ("toolCallId" in part && part.toolCallId) {
    lines.push(`- toolCallId: \`${part.toolCallId}\``)
  }

  // Final result inputs son la respuesta — explicámoslos en alto nivel.
  if (isFinal && part.input !== undefined) {
    const desc = describeFinalResult(resolvedToolName, part.input)
    lines.push("")
    lines.push(`**Card renderizada:** \`${desc.cardKind}\` — ${desc.label}`)
    if (desc.summaryLines.length > 0) {
      lines.push("")
      lines.push(...desc.summaryLines)
    }
    lines.push("")
    lines.push("<details><summary>input completo (JSON)</summary>")
    lines.push("")
    lines.push("```json")
    lines.push(pretty(part.input, MAX_OUTPUT_PRETTY_CHARS))
    lines.push("```")
    lines.push("")
    lines.push("</details>")
    return lines.join("\n")
  }

  if (part.input !== undefined) {
    lines.push("")
    lines.push("**input:**")
    lines.push("```json")
    lines.push(pretty(part.input))
    lines.push("```")
  }

  if (state === "output-available" && "output" in part && part.output !== undefined) {
    lines.push("")
    lines.push("**output:**")
    lines.push("```json")
    lines.push(pretty(part.output, MAX_OUTPUT_PRETTY_CHARS))
    lines.push("```")
  }

  if (state === "output-error") {
    const errorText = (part as { errorText?: string }).errorText
    lines.push("")
    lines.push(`> ⚠️ **Tool error:** ${errorText ?? "(sin errorText)"}`)
  }

  return lines.join("\n")
}

function renderPart(part: unknown, idx: number): string | null {
  if (!part || typeof part !== "object") return null
  const p = part as { type?: unknown }
  if (typeof p.type !== "string") return null

  if (p.type === "text") {
    const text = (p as { text?: string }).text ?? ""
    if (!text.trim()) return null
    return `### 💬 text\n\n${text}`
  }

  if (p.type === "reasoning") {
    const text = (p as { text?: string }).text ?? ""
    if (!text.trim()) return null
    return [
      `### 🧠 reasoning`,
      "",
      "<details><summary>razonamiento del modelo</summary>",
      "",
      text,
      "",
      "</details>",
    ].join("\n")
  }

  if (p.type === "source-url") {
    const sp = p as { url?: string; title?: string }
    return `### 🔗 source-url · ${sp.title ?? sp.url ?? `#${idx}`}\n\n${sp.url ?? ""}`
  }

  if (p.type === "source-document") {
    const sp = p as { title?: string }
    return `### 📄 source-document · ${sp.title ?? `#${idx}`}`
  }

  if (p.type.startsWith("tool-")) {
    const toolName = p.type.replace(/^tool-/, "")
    return renderToolPart(p as ToolUIPart, toolName)
  }

  if (p.type === "dynamic-tool") {
    const dyn = p as DynamicToolUIPart
    return renderToolPart(dyn, dyn.toolName ?? "(dynamic)")
  }

  // Fallback: dump del shape para no perder data.
  return [`### ❓ part type=\`${p.type}\``, "```json", pretty(part), "```"].join("\n")
}

export function exportChatToMarkdown(
  messages: UIMessage[],
  meta: { activeSymbol?: string; activeTimeframe?: string } = {},
): string {
  const header: string[] = [
    "# Mastery Trader — Chat export",
    "",
    `- Exportado: \`${tsLine()}\``,
    `- Mensajes: ${messages.length}`,
  ]
  if (meta.activeSymbol) {
    header.push(`- Símbolo activo al exportar: \`${meta.activeSymbol}${meta.activeTimeframe ? " " + meta.activeTimeframe : ""}\``)
  }
  header.push("")
  header.push("Formato: cada mensaje incluye el orden de partes que la UI procesó.")
  header.push("`tool-final_result_TradeIdea` y `tool-final_result_BriefAnalysis` indican qué card renderizó CopilotChat.tsx.")
  header.push("Reasoning va en `<details>` para que el .md sea navegable pero completo.")
  header.push("")
  header.push("---")
  header.push("")

  const sections: string[] = []
  messages.forEach((m, mi) => {
    const roleEmoji = m.role === "user" ? "👤" : m.role === "assistant" ? "🤖" : "⚙️"
    const roleLabel =
      m.role === "user" ? "Usuario" : m.role === "assistant" ? "Asistente" : m.role
    sections.push(`## ${roleEmoji} ${roleLabel} — turno ${mi + 1}`)
    sections.push("")
    sections.push(`- id: \`${m.id}\``)
    sections.push(`- parts: ${m.parts.length}`)
    sections.push("")

    m.parts.forEach((part, pi) => {
      const rendered = renderPart(part, pi)
      if (rendered) {
        sections.push(rendered)
        sections.push("")
      }
    })

    sections.push("---")
    sections.push("")
  })

  return [...header, ...sections].join("\n")
}

export function downloadMarkdown(filename: string, content: string): void {
  if (typeof window === "undefined") return
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 100)
}
