"use client"

import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import { Message, MessageContent } from "@/components/ai-elements/message"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input"
import { Card } from "@/components/ui/card"
import { env } from "@/lib/env"
import { isTradeIdea } from "@/lib/chat-types"
import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport, type DynamicToolUIPart, type ToolUIPart } from "ai"
import { BotIcon } from "lucide-react"
import { useState } from "react"

import { ReasoningPart } from "./parts/ReasoningPart"
import { SourcesStrip, type SourceItem } from "./parts/SourcePart"
import { TextPart } from "./parts/TextPart"
import { ToolPart } from "./parts/ToolPart"
import { TradeIdeaCard } from "./TradeIdeaCard"

interface CopilotChatProps {
  className?: string
}

const SUGGESTIONS = [
  "analiza BTCUSDT en 4h",
  "¿qué es RSI?",
  "estructura de BTCUSDT en 1d",
]

export function CopilotChat({ className }: CopilotChatProps) {
  const [text, setText] = useState("")
  const { messages, sendMessage, status, error, stop } = useChat({
    transport: new DefaultChatTransport({ api: `${env.apiUrl}/chat` }),
  })

  const submitting = status === "submitted" || status === "streaming"

  return (
    <Card
      className={`flex h-full flex-col overflow-hidden border-border/60 bg-card/40 ${className ?? ""}`}
    >
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-2">
        <BotIcon className="size-4 text-muted-foreground" />
        <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          copilot · sonnet 4.6
        </span>
        {status === "error" && (
          <span className="ml-auto text-xs text-destructive">{String(error)}</span>
        )}
      </div>

      <Conversation className="flex-1 px-3">
        <ConversationContent className="space-y-4 py-3">
          {messages.length === 0 && (
            <ConversationEmptyState
              icon={<BotIcon className="size-6 text-muted-foreground" />}
              title="Pregúntame sobre el mercado"
              description="Cada cifra se cita a una herramienta determinista."
            >
              <div className="mt-4 flex flex-col gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => sendMessage({ text: s })}
                    className="rounded-md border border-border/60 bg-background/40 px-3 py-1.5 text-left text-xs text-foreground/80 hover:bg-accent/40"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </ConversationEmptyState>
          )}

          {messages.map((m) => {
            // Collect sources from tool parts so we can render a single Sources strip
            const sources: SourceItem[] = []
            const rendered: React.ReactNode[] = []

            m.parts.forEach((part, idx) => {
              const key = `${m.id}-${idx}`
              switch (part.type) {
                case "text":
                  rendered.push(<TextPart key={key} text={part.text} />)
                  break
                case "reasoning":
                  rendered.push(
                    <ReasoningPart
                      key={key}
                      text={part.text}
                      isStreaming={
                        m.role === "assistant" && submitting && idx === m.parts.length - 1
                      }
                    />,
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
                    // Special case: Pydantic AI emits the structured TradeIdea
                    // output as a `tool-final_result` part. Render it as the card.
                    if (toolName === "final_result" && isTradeIdea(toolPart.input)) {
                      rendered.push(
                        <TradeIdeaCard key={key} idea={toolPart.input} />,
                      )
                    } else {
                      rendered.push(<ToolPart key={key} part={toolPart} />)
                      sources.push({
                        id: `tool-${toolPart.toolCallId ?? idx}`,
                        title: `${toolName.replace(/_/g, " ")}`,
                      })
                    }
                  } else if (part.type === "dynamic-tool") {
                    rendered.push(
                      <ToolPart key={key} part={part as DynamicToolUIPart} />,
                    )
                  }
                  break
              }
            })

            if (m.role === "assistant" && sources.length > 0) {
              rendered.push(<SourcesStrip key={`${m.id}-sources`} items={sources} />)
            }

            return (
              <Message key={m.id} from={m.role}>
                <MessageContent>{rendered}</MessageContent>
              </Message>
            )
          })}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      <PromptInput
        onSubmit={(message) => {
          const value = message.text?.trim()
          if (!value) return
          sendMessage({ text: value })
          setText("")
        }}
        className="m-3 mt-1"
      >
        <PromptInputBody>
          <PromptInputTextarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Pregunta al copiloto…"
          />
          <PromptInputFooter>
            <PromptInputTools />
            <PromptInputSubmit
              status={status}
              onStop={stop}
              disabled={!submitting && text.trim().length === 0}
            />
          </PromptInputFooter>
        </PromptInputBody>
      </PromptInput>
    </Card>
  )
}
