import { Inter, JetBrains_Mono } from "next/font/google"

import "./globals.css"
import { Providers } from "@/components/providers"
import { cn } from "@/lib/utils"

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" })
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
})

export const metadata = {
  title: "Trading Copilot",
  description: "Crypto trading copilot — interpreter and orchestrator, never an oracle.",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn("antialiased", sans.variable, mono.variable, "font-sans")}
    >
      <body className="bg-background text-foreground">
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
