/** Auth layout — full-bleed slate, no GlobalNav. The root layout still wraps
 * (it provides the html/body + ThemeProvider) but `<GlobalNav />` returns null
 * for /auth/* routes, so visually this layout owns the screen. */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-4">
      {children}
    </div>
  )
}
