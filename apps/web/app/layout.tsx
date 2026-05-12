import { Geist, JetBrains_Mono } from "next/font/google"

import "./globals.css"
import { GlobalNav } from "@/components/nav/GlobalNav"
import { Statusbar } from "@/components/nav/Statusbar"
import { Providers } from "@/components/providers"
import { cn } from "@/lib/core/utils"

// Geist Sans (Vercel) pareja con JetBrains Mono — geometría coherente.
// Sustituye a Inter_Tight, que estaba cargada pero el body forzaba mono
// y nunca se aplicaba.
const sans = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
})
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
})

export const metadata = {
  title: "Mastery Trader",
  description:
    "Copiloto de trading cripto que analiza el mercado contigo, valida estrategias con datos y te avisa en tiempo real.",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="es"
      suppressHydrationWarning
      className={cn("antialiased", sans.variable, mono.variable, "font-sans")}
    >
      <body className="flex h-dvh flex-col bg-background text-foreground">
        <Providers>
          <GlobalNav />
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
          <Statusbar />
        </Providers>
      </body>
    </html>
  )
}
