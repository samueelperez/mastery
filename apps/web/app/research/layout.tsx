import { ResearchSubnav } from "@/components/nav/ResearchSubnav"

export const metadata = {
  title: "Investigación · Trading Copilot",
  description: "Backtests, diario y detección de sesgos.",
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
