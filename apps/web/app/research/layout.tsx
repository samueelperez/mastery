import { ResearchSubnav } from "@/components/nav/ResearchSubnav"

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
    <div className="flex flex-col">
      <ResearchSubnav />
      <main className="overflow-x-hidden p-4 sm:p-6">{children}</main>
    </div>
  )
}
