import Link from "next/link"
import { ArrowLeftIcon } from "lucide-react"

import { ResearchNav } from "@/components/research/ResearchNav"

export const metadata = {
  title: "Research · Trading Copilot",
  description: "Backtests, journal, and bias detection.",
}

export default function ResearchLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex items-center gap-4 border-b border-border/40 px-6 py-3">
        <Link
          href="/"
          className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3.5" />
          chat
        </Link>
        <span className="font-mono text-sm tracking-tight text-foreground">
          research
        </span>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          F2 · backtests + journal
        </span>
      </header>
      <div className="grid flex-1 grid-cols-[12rem_minmax(0,1fr)]">
        <aside className="border-r border-border/40 px-3 py-4">
          <ResearchNav />
        </aside>
        <main className="overflow-x-hidden p-6">{children}</main>
      </div>
    </div>
  )
}
