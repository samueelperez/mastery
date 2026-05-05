"use client"

import { ThemeProvider as NextThemesProvider } from "next-themes"

/** El usuario puede elegir tema claro u oscuro. Default = oscuro (el copilot
 *  está pensado para sesiones largas mirando charts). El claro usa una paleta
 *  cálida (arena/parchment, hue 80) — no blanco neutro. */
function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  )
}

export { ThemeProvider }
