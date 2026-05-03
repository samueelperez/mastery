"use client"

import { MessageResponse } from "@/components/ai-elements/message"

interface TextPartProps {
  text: string
}

/**
 * Renders streaming text via Streamdown (markdown + math + code + mermaid).
 * Used for both assistant text and any user echo we want formatted.
 */
export function TextPart({ text }: TextPartProps) {
  return <MessageResponse>{text}</MessageResponse>
}
