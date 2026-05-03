"use client"

import {
  Source,
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources"

export interface SourceItem {
  /** Stable id (we use `${tool_name}#${tool_call_id}` for tool-derived sources). */
  id: string
  /** Human-readable label, e.g. "get_indicators · 4h". */
  title: string
  /** Optional URL (real source-url parts) or hash anchor for tool sources. */
  href?: string
}

interface SourcesStripProps {
  items: SourceItem[]
}

/**
 * Aggregated sources strip rendered at the end of an assistant turn. Each item
 * is either a real `source-url`/`source-document` part or a tool we called this
 * turn (we keep tool-derived sources visible so the user can audit "which tools
 * backed this answer" without expanding every tool block).
 */
export function SourcesStrip({ items }: SourcesStripProps) {
  if (items.length === 0) return null
  return (
    <Sources>
      <SourcesTrigger count={items.length} />
      <SourcesContent>
        {items.map((s) => (
          <Source key={s.id} href={s.href ?? "#"} title={s.title}>
            {s.title}
          </Source>
        ))}
      </SourcesContent>
    </Sources>
  )
}
