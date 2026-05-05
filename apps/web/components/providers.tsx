"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState } from "react"

import { ThemeProvider } from "@/components/theme-provider"
import { TooltipProvider } from "@/components/ui/tooltip"

export function Providers({ children }: { children: React.ReactNode }) {
  // useState ensures one client per browser session — never shared across requests on the server.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            // Mantener data del usuario fresca aunque la pestaña pierda
            // foco — lo importante: refetchear al volver de un sleep o
            // de un blip de red. window-focus genera ruido en pestañas
            // de fondo, reconnect es señal real de "cliente vuelve a
            // estar online" que sí merece refetch.
            refetchOnWindowFocus: false,
            refetchOnReconnect: "always",
            retry: 1,
          },
        },
      }),
  )

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={150}>{children}</TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
