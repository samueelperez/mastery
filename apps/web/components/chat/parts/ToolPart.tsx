"use client"

import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"
import type { DynamicToolUIPart, ToolUIPart } from "ai"

interface ToolPartProps {
  /**
   * The full tool part from useChat's message.parts. Types:
   *   { type: 'tool-get_indicators', state, input, output, toolCallId }
   *   { type: 'dynamic-tool', toolName, state, input, output }
   */
  part: ToolUIPart | DynamicToolUIPart
}

/**
 * Generic renderer for any tool call. Shows status badge, collapsible input/output
 * with JSON pretty-printing. We intentionally do NOT special-case tool names here —
 * the special TradeIdea card path is handled upstream in CopilotChat.
 */
export function ToolPart({ part }: ToolPartProps) {
  const isDynamic = part.type === "dynamic-tool"
  const toolName = isDynamic
    ? (part as DynamicToolUIPart).toolName
    : part.type.replace(/^tool-/, "")
  const errorText =
    part.state === "output-error" ? (part as { errorText?: string }).errorText : undefined

  return (
    <Tool>
      <ToolHeader
        type={part.type as never}
        state={part.state}
        toolName={isDynamic ? toolName : undefined}
        title={isDynamic ? undefined : toolName.replace(/_/g, " ")}
      />
      <ToolContent>
        {part.input ? <ToolInput input={part.input} /> : null}
        {part.state === "output-available" || part.state === "output-error" ? (
          <ToolOutput output={part.output as never} errorText={errorText} />
        ) : null}
      </ToolContent>
    </Tool>
  )
}
