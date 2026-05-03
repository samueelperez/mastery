"use client"

import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning"

interface ReasoningPartProps {
  text: string
  /** True while the reasoning block is still being streamed. */
  isStreaming: boolean
  /** Optional duration in seconds (auto-computed by Reasoning when streaming). */
  duration?: number
}

/**
 * Collapsible thinking block. Auto-opens while streaming, auto-closes 1s after
 * stream end (built into the ai-elements Reasoning component).
 */
export function ReasoningPart({ text, isStreaming, duration }: ReasoningPartProps) {
  return (
    <Reasoning isStreaming={isStreaming} duration={duration}>
      <ReasoningTrigger />
      <ReasoningContent>{text}</ReasoningContent>
    </Reasoning>
  )
}
