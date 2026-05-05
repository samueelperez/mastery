import { AuthShowcase } from "@/components/auth/AuthShowcase"

/** Layout para /auth/* — split pitch (izq) + form (der) en desktop.
 *
 * Mobile: showcase oculto, form-card centrado a 100% del ancho.
 *
 * Cada columna tiene su propio backdrop:
 *   - showcase: dot grid + radiales violet/amber (gestionado dentro del componente)
 *   - form: líneas finas (repeating-linear-gradient) + ambient amber abajo a la derecha
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="relative min-h-svh overflow-hidden bg-background">
      <div
        className="grid min-h-svh grid-cols-1 lg:grid-cols-[1.05fr_1fr]"
      >
        <AuthShowcase className="hidden lg:flex" />

        <div className="relative flex items-center justify-center px-4 py-10 sm:px-6 lg:px-12">
          {/* form backdrop — líneas finas verticales */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 opacity-30"
            style={{
              backgroundImage:
                "repeating-linear-gradient(90deg, transparent 0 47px, oklch(0.36 0.02 260 / 0.05) 47px 48px)",
            }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute -bottom-32 -right-24 size-[24rem] rounded-full"
            style={{
              background:
                "radial-gradient(circle, oklch(0.78 0.16 75 / 0.08) 0%, transparent 60%)",
            }}
          />
          <main className="relative z-10 w-full max-w-[420px]">{children}</main>
        </div>
      </div>
    </div>
  )
}
