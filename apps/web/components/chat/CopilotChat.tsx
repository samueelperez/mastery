"use client"

import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import { Message, MessageContent } from "@/components/ai-elements/message"
import { Card } from "@/components/ui/card"
import { BEARER_TOKEN_KEY } from "@/lib/auth/auth-client"
import { env } from "@/lib/env"
import { isBriefAnalysis, isTradeIdea } from "@/lib/chat-types"
import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport, type DynamicToolUIPart, type ToolUIPart } from "ai"
import { useState } from "react"

import { ChatComposer } from "./ChatComposer"
import { ChatEmptyState } from "./ChatEmptyState"

import { BrandMark } from "@/components/nav/BrandMark"
import { useActiveSymbol } from "@/lib/store/active-symbol"
import { toolLabel } from "@/lib/tool-labels"

import { AlertCreatedCard, type AlertCreatedToolOutput } from "./AlertCreatedCard"
import { BacktestResultCard, type BacktestToolOutput } from "./BacktestResultCard"
import {
  BtcCorrelationSummaryCard,
  isBtcCorrelationOutput,
} from "./BtcCorrelationSummaryCard"
import {
  ConfluenceSummaryCard,
  isConfluenceMapOutput,
} from "./ConfluenceSummaryCard"
import {
  FundingRateSummaryCard,
  isFundingRateOutput,
} from "./FundingRateSummaryCard"
import {
  IndicatorSummaryCard,
  isIndicatorPanelOutput,
} from "./IndicatorSummaryCard"
import {
  OpenInterestSummaryCard,
  isOpenInterestOutput,
} from "./OpenInterestSummaryCard"
import {
  VolumeProfileSummaryCard,
  isVolumeProfileOutput,
} from "./VolumeProfileSummaryCard"
import { MessageWorkbench } from "./parts/MessageWorkbench"
import { ReasoningPart } from "./parts/ReasoningPart"
import { SourcesStrip, type SourceItem } from "./parts/SourcePart"
import { TextPart } from "./parts/TextPart"
import { ToolErrorBanner } from "./parts/ToolErrorBanner"
import { ToolPart } from "./parts/ToolPart"
import { ToolRunningBanner } from "./parts/ToolRunningBanner"
import {
  StructureSummaryCard,
  isMarketStructureOutput,
} from "./StructureSummaryCard"
import { BriefAnalysisCard } from "./BriefAnalysisCard"
import { ChatExportButton } from "./ChatExportButton"
import { TradeIdeaCard } from "./TradeIdeaCard"
import { useSymbolBridge } from "./useSymbolBridge"

function isBacktestOutput(value: unknown): value is BacktestToolOutput {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  return (
    typeof v.run_id === "string" &&
    typeof v.strategy_id === "string" &&
    typeof v.deflated_sharpe === "number" &&
    typeof v.sharpe === "number" &&
    typeof v.max_drawdown === "number"
  )
}

function isAlertCreatedOutput(value: unknown): value is AlertCreatedToolOutput {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  if (typeof v.alert_id !== "string" || typeof v.name !== "string") return false
  if (typeof v.cooldown_s !== "number") return false
  const spec = v.spec as Record<string, unknown> | undefined
  if (!spec || typeof spec !== "object") return false
  return (
    typeof spec.symbol === "string" &&
    typeof spec.timeframe === "string" &&
    Array.isArray(spec.conditions)
  )
}

interface CopilotChatProps {
  className?: string
}

export function CopilotChat({ className }: CopilotChatProps) {
  const [text, setText] = useState("")
  const activeSymbol = useActiveSymbol((s) => s.symbol)
  const activeTimeframe = useActiveSymbol((s) => s.timeframe)
  const { messages, sendMessage, setMessages, status, error, stop } = useChat({
    transport: new DefaultChatTransport({
      api: `${env.apiUrl}/chat`,
      // El chat es cross-origin (Vercel → Railway) y `useChat` no usa nuestro
      // `apiFetch` wrapper. En cross-domain las cookies no viajan, así que
      // pasamos el bearer token (capturado en login y persistido en localStorage)
      // como Authorization header. `credentials:"include"` se mantiene para
      // entornos same-origin (dev local).
      credentials: "include",
      headers: () => {
        const token =
          typeof window === "undefined"
            ? null
            : window.localStorage.getItem(BEARER_TOKEN_KEY)
        const h: Record<string, string> = {}
        if (token) h.Authorization = `Bearer ${token}`
        return h
      },
    }),
  })
  const { warning: bridgeWarning, dismissWarning } = useSymbolBridge(messages)

  const submitting = status === "submitted" || status === "streaming"

  return (
    <Card
      className={`flex h-full flex-col overflow-hidden border-border bg-card ${className ?? ""}`}
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <BrandMark size={14} />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--fg-2)]">
          copilot
        </span>
        <span className="text-[var(--fg-4)]">·</span>
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--fg-2)]">
          sonnet 4.6
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5">
          {status === "error" ? (
            <span className="pill-status pill-status-err">{String(error)}</span>
          ) : (
            <span className="pill-status pill-status-info">
              <span className="dot dot-violet" aria-hidden />
              14 herramientas
            </span>
          )}
          <ChatExportButton
            messages={messages}
            activeSymbol={activeSymbol}
            activeTimeframe={activeTimeframe}
          />
        </span>
      </div>

      {bridgeWarning && (
        <div className="border-b border-border bg-[var(--bg-1)] px-3.5 py-2">
          <button
            type="button"
            onClick={dismissWarning}
            className="pill-status pill-status-info w-full justify-between gap-2 text-left"
            title="cerrar"
          >
            <span className="truncate">{bridgeWarning}</span>
            <span aria-hidden className="text-[var(--fg-3)]">
              ✕
            </span>
          </button>
        </div>
      )}

      <Conversation className="flex-1 px-3">
        <ConversationContent className="space-y-4 py-3">
          {messages.length === 0 && (
            <ChatEmptyState
              onSuggestion={(text) => sendMessage({ text })}
            />
          )}

          {messages.map((m) => {
            // Collect sources from tool parts so we can render a single Sources strip
            const sources: SourceItem[] = []
            // Cuando el agente emite un final_result estructurado (TradeIdea
            // o BriefAnalysis), todo el razonamiento previo (tool calls
            // intermedios + reasoning blocks) se agrupa en un Collapsible
            // cerrado para que la respuesta principal sea protagonista.
            // Sin final_result estructurado, todo va al main como antes.
            // Pydantic-AI con output_type union (BriefAnalysis | TradeIdea |
            // str) nombra los tools como `final_result_BriefAnalysis` y
            // `final_result_TradeIdea` — por eso el prefix match.
            //
            // ModelRetry edge case: cuando el validator dispara ModelRetry,
            // el primer intento queda en `parts` con shape válido (el
            // type-guard pasa) Y luego entra el retry exitoso. Sin
            // deduplicación ambos se renderizan como cards. Calculamos el
            // índice del ÚLTIMO match — solo ese instancia la card; los
            // anteriores se silencian (son intentos rechazados que el
            // usuario no necesita ver dos veces).
            let lastFinalCardIdx = -1
            for (let i = m.parts.length - 1; i >= 0; i--) {
              const p = m.parts[i]
              if (!p || typeof p.type !== "string") continue
              if (!p.type.startsWith("tool-final_result")) continue
              const input = (p as ToolUIPart).input
              if (isTradeIdea(input) || isBriefAnalysis(input)) {
                lastFinalCardIdx = i
                break
              }
            }
            const hasFinalCard = lastFinalCardIdx >= 0
            const main: React.ReactNode[] = []
            const workbench: React.ReactNode[] = []
            let workbenchSteps = 0
            const pushNode = (
              node: React.ReactNode,
              bucket: "main" | "workbench",
            ) => {
              if (bucket === "workbench") {
                workbench.push(node)
                workbenchSteps += 1
              } else {
                main.push(node)
              }
            }

            m.parts.forEach((part, idx) => {
              const key = `${m.id}-${idx}`
              switch (part.type) {
                case "text":
                  // Cuando el LLM emite un final TradeIdea estructurado,
                  // los `text` parts intermedios ("tengo todos los datos…",
                  // "sintetizo la estructura completa") son scaffolding —
                  // van al workbench para no enterrar la card. Sin final
                  // idea (text-only response), el text ES la respuesta y
                  // va al main.
                  pushNode(
                    <TextPart key={key} text={part.text} />,
                    hasFinalCard ? "workbench" : "main",
                  )
                  break
                case "reasoning":
                  // El razonamiento crudo del modelo NO es la explicación
                  // curada — esa vive en summary_es + confluences[].narrative
                  // del TradeIdeaCard. Cuando hay final idea, el reasoning va
                  // al workbench (Collapsible cerrada) para quedar accesible
                  // como debug sin protagonismo. Sin final idea (text-only,
                  // pregunta definitional), va al main como antes.
                  pushNode(
                    <ReasoningPart
                      key={key}
                      text={part.text}
                      isStreaming={
                        m.role === "assistant" && submitting && idx === m.parts.length - 1
                      }
                    />,
                    hasFinalCard ? "workbench" : "main",
                  )
                  break
                case "source-url":
                  sources.push({
                    id: `url-${idx}`,
                    title: part.title || part.url,
                    href: part.url,
                  })
                  break
                case "source-document":
                  sources.push({
                    id: `doc-${idx}`,
                    title: part.title || `document #${idx}`,
                  })
                  break
                default:
                  if (typeof part.type === "string" && part.type.startsWith("tool-")) {
                    const toolName = part.type.replace(/^tool-/, "")
                    const toolPart = part as ToolUIPart
                    // Estados pre-output: en lugar del Collapsible opaco
                    // mostramos un banner narrado con spinner. Excepción:
                    // tool-final_result con TradeIdea o BriefAnalysis válido
                    // siempre va a su card. Pydantic-AI con union output
                    // namespacea: `final_result_BriefAnalysis`,
                    // `final_result_TradeIdea` (no solo `final_result`).
                    const isFinalResultTool = toolName.startsWith("final_result")
                    const isFinalStructured =
                      isFinalResultTool &&
                      (isTradeIdea(toolPart.input) ||
                        isBriefAnalysis(toolPart.input))
                    // Bucket por defecto: si hay final_result, los tools
                    // intermedios van al workbench; si no, al main.
                    const bucket: "main" | "workbench" = hasFinalCard
                      ? "workbench"
                      : "main"

                    if (
                      !isFinalStructured &&
                      (toolPart.state === "input-streaming" ||
                        toolPart.state === "input-available")
                    ) {
                      pushNode(
                        <ToolRunningBanner
                          key={key}
                          toolName={toolName}
                          input={toolPart.input}
                        />,
                        bucket,
                      )
                    } else if (
                      !isFinalStructured &&
                      toolPart.state === "output-error"
                    ) {
                      const errPart = toolPart as ToolUIPart & {
                        errorText?: string
                      }
                      pushNode(
                        <ToolErrorBanner
                          key={key}
                          toolName={toolName}
                          errorText={errPart.errorText}
                        />,
                        bucket,
                      )
                    } else if (
                      isFinalResultTool &&
                      isBriefAnalysis(toolPart.input)
                    ) {
                      // Solo el ÚLTIMO final_result válido se pinta como
                      // card. Intentos anteriores (rechazados por
                      // ModelRetry) se silencian — su input ya pasa el
                      // type-guard, pero no son la respuesta canónica.
                      if (idx === lastFinalCardIdx) {
                        pushNode(
                          <BriefAnalysisCard
                            key={key}
                            brief={toolPart.input}
                          />,
                          "main",
                        )
                      }
                    } else if (
                      isFinalResultTool &&
                      isTradeIdea(toolPart.input)
                    ) {
                      // Idem TradeIdea: solo el último intento exitoso se
                      // renderiza como card. Anteriores (retries) callan.
                      if (idx === lastFinalCardIdx) {
                        pushNode(
                          <TradeIdeaCard key={key} idea={toolPart.input} />,
                          "main",
                        )
                      }
                    } else if (
                      toolName === "run_backtest" &&
                      toolPart.state === "output-available" &&
                      isBacktestOutput(toolPart.output)
                    ) {
                      pushNode(
                        <BacktestResultCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string; timeframe?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "ejecutar backtest",
                      })
                    } else if (
                      toolName === "create_alert" &&
                      toolPart.state === "output-available" &&
                      isAlertCreatedOutput(toolPart.output)
                    ) {
                      // create_alert es respuesta principal (no parte del razonamiento) → main siempre.
                      pushNode(
                        <AlertCreatedCard key={key} output={toolPart.output} />,
                        "main",
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "crear alerta",
                      })
                    } else if (
                      toolName === "get_indicators" &&
                      toolPart.state === "output-available" &&
                      isIndicatorPanelOutput(toolPart.output)
                    ) {
                      pushNode(
                        <IndicatorSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string; timeframe?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "indicadores",
                      })
                    } else if (
                      toolName === "get_multi_tf_confluence" &&
                      toolPart.state === "output-available" &&
                      isConfluenceMapOutput(toolPart.output)
                    ) {
                      pushNode(
                        <ConfluenceSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "confluencia multi-tf",
                      })
                    } else if (
                      toolName === "get_market_structure" &&
                      toolPart.state === "output-available" &&
                      isMarketStructureOutput(toolPart.output)
                    ) {
                      pushNode(
                        <StructureSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string; timeframe?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "estructura",
                      })
                    } else if (
                      toolName === "get_volume_profile" &&
                      toolPart.state === "output-available" &&
                      isVolumeProfileOutput(toolPart.output)
                    ) {
                      pushNode(
                        <VolumeProfileSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string; timeframe?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "perfil de volumen",
                      })
                    } else if (
                      toolName === "get_funding_rate" &&
                      toolPart.state === "output-available" &&
                      isFundingRateOutput(toolPart.output)
                    ) {
                      pushNode(
                        <FundingRateSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "tasa de financiación",
                      })
                    } else if (
                      toolName === "get_open_interest" &&
                      toolPart.state === "output-available" &&
                      isOpenInterestOutput(toolPart.output)
                    ) {
                      pushNode(
                        <OpenInterestSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "interés abierto",
                      })
                    } else if (
                      toolName === "get_btc_correlation" &&
                      toolPart.state === "output-available" &&
                      isBtcCorrelationOutput(toolPart.output)
                    ) {
                      pushNode(
                        <BtcCorrelationSummaryCard
                          key={key}
                          output={toolPart.output}
                          input={toolPart.input as { symbol?: string; timeframe?: string }}
                        />,
                        bucket,
                      )
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: "correlación con BTC",
                      })
                    } else {
                      pushNode(<ToolPart key={key} part={toolPart} />, bucket)
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: toolLabel(toolName),
                      })
                    }
                  } else if (part.type === "dynamic-tool") {
                    const dyn = part as DynamicToolUIPart
                    const dynBucket: "main" | "workbench" = hasFinalCard
                      ? "workbench"
                      : "main"
                    if (
                      dyn.state === "input-streaming" ||
                      dyn.state === "input-available"
                    ) {
                      pushNode(
                        <ToolRunningBanner
                          key={key}
                          toolName={dyn.toolName}
                          input={dyn.input}
                        />,
                        dynBucket,
                      )
                    } else if (dyn.state === "output-error") {
                      const errDyn = dyn as DynamicToolUIPart & {
                        errorText?: string
                      }
                      pushNode(
                        <ToolErrorBanner
                          key={key}
                          toolName={dyn.toolName}
                          errorText={errDyn.errorText}
                        />,
                        dynBucket,
                      )
                    } else {
                      pushNode(<ToolPart key={key} part={dyn} />, dynBucket)
                    }
                  }
                  break
              }
            })

            const showSources = m.role === "assistant" && sources.length > 0

            return (
              <Message key={m.id} from={m.role}>
                <MessageContent>
                  {workbench.length > 0 && (
                    <MessageWorkbench
                      key={`${m.id}-workbench`}
                      count={workbenchSteps}
                    >
                      {workbench}
                    </MessageWorkbench>
                  )}
                  {main}
                  {showSources && (
                    <SourcesStrip
                      key={`${m.id}-sources`}
                      items={sources}
                    />
                  )}
                </MessageContent>
              </Message>
            )
          })}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      <ChatComposer
        value={text}
        onChange={setText}
        onSubmit={(value) => {
          sendMessage({ text: value })
          setText("")
        }}
        status={status}
        onStop={stop}
        activeSymbol={activeSymbol}
        activeTimeframe={activeTimeframe}
        hasMessages={messages.length > 0}
        onClearMessages={() => setMessages([])}
        className="m-3"
      />
    </Card>
  )
}
