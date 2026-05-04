/** Auth layout — atmospheric backdrop with dot-grid + faint gold radial glow.
 *
 * Visual: solid slate base, subtle dot pattern across the whole canvas, a
 * gold radial gradient anchored at top-center bleeding ~600px down. No
 * GlobalNav (that component returns null on /auth/*) so this layout owns
 * the screen.
 *
 * Both decorative layers are aria-hidden — they're pure atmosphere.
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="relative flex min-h-svh flex-col items-center justify-center overflow-hidden bg-background px-4 py-8">
      {/* Dot-grid texture */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "radial-gradient(circle, var(--color-border) 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />
      {/* Top gold glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[40rem] opacity-25"
        style={{
          background:
            "radial-gradient(ellipse at top, var(--color-primary) 0%, transparent 60%)",
        }}
      />
      {/* Content */}
      <div className="relative z-10 flex flex-col items-center gap-6">
        {children}
      </div>
    </div>
  )
}
