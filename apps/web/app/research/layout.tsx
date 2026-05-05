import { ResearchSubnav } from "@/components/nav/ResearchSubnav"

export const metadata = {
  title: "Investigación · Mastery Trader",
  description: "Backtests, diario y detección de sesgos.",
}

export default function ResearchLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ResearchSubnav />
      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden p-4 sm:p-6">
        {children}
      </main>
    </div>
  )
}
