export const metadata = {
  title: "Diario · Mastery Trader",
  description: "Setups propuestos por el agente con seguimiento automático.",
}

export default function JournalLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden">
      {children}
    </main>
  )
}
