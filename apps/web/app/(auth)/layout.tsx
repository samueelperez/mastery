import { AuthShowcase } from "@/components/auth/AuthShowcase"

/** Auth layout — split: live BTCUSDT chart + identity on the left, form on
 * the right (lg+). Mobile collapses to the form alone (showcase hidden) so
 * sign-in stays fast on small screens.
 *
 * The dot-grid + gold radial glow live on the layout's full canvas behind
 * both columns; AuthShowcase has its own border-right separator. */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="relative min-h-svh overflow-hidden bg-background">
      {/* Decorative atmosphere */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-30"
        style={{
          backgroundImage:
            "radial-gradient(circle, var(--color-border) 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[36rem] opacity-20"
        style={{
          background:
            "radial-gradient(ellipse at top, var(--color-primary) 0%, transparent 60%)",
        }}
      />

      <div className="relative z-10 grid min-h-svh grid-cols-1 lg:grid-cols-[minmax(0,1fr)_28rem]">
        <AuthShowcase className="hidden lg:flex" />
        <main className="flex items-center justify-center px-4 py-10 sm:px-6 lg:px-10">
          {children}
        </main>
      </div>
    </div>
  )
}
