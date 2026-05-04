"use client"

import { ThemeProvider as NextThemesProvider } from "next-themes"

/** Theme is forced dark — the trading copilot is a dark-first product (slate
 * background + gold/purple accents). The light tokens in globals.css are still
 * shadcn neutral defaults; finishing them is a future-fix. We pin `dark` here
 * so light theme can never sneak in via system preference. */
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
      forcedTheme="dark"
      {...props}
    >
      {children}
    </NextThemesProvider>
  )
}

export { ThemeProvider }
