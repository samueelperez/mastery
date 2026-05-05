"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"

import { Spinner } from "@/components/ui/spinner"

/** /research/journal está deprecado. El Diario unificado vive en /journal
 *  desde el rediseño de mayo 2026 — combina setups del agente y trades
 *  importados con un toggle de fuente. Esta ruta hace soft-redirect para
 *  mantener bookmarks viejos funcionando sin romper. */
export default function DeprecatedResearchJournal() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/journal")
  }, [router])

  return (
    <div className="flex flex-col items-center justify-center gap-3 p-12 text-center">
      <Spinner />
      <p className="text-[14px] text-foreground">
        El Diario se ha unificado en <code className="font-mono">/journal</code>
      </p>
      <p className="text-[12px] text-muted-foreground">
        Te llevamos allí…
      </p>
    </div>
  )
}
