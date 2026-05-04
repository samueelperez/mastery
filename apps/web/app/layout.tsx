import { Inter, JetBrains_Mono } from "next/font/google"

import "./globals.css"
import { GlobalNav } from "@/components/nav/GlobalNav"
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
  description: "Copilot de trading cripto — intérprete y orquestador, nunca un oráculo.",
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
      <body className="bg-background text-foreground">
        <Providers>
          <GlobalNav />
          {children}
        </Providers>
      </body>
    </html>
  )
}
