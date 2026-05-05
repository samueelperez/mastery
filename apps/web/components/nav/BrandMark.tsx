import { cn } from "@/lib/utils"

interface BrandMarkProps {
  size?: number
  className?: string
  /** Si false, oculta el "mastery point" violet del valle central. Útil en
   *  contextos donde el dot resulta ruidoso (e.g. empty state del chat). */
  withDot?: boolean
}

/** Mastery Trader monograma — una "M" geométrica con stroke ámbar y un
 *  punto violet en el valle central (el "mastery point"). El stroke-width
 *  se ajusta dinámicamente al size para mantener proporción visual.
 *
 *  Usado en topbar, login pitch, splashes, etc. */
export function BrandMark({ size = 18, className, withDot = true }: BrandMarkProps) {
  // strokeWidth proporcional al size (≈ 11% del lado para ~2px en size=18).
  const stroke = Math.max(1.5, size * 0.11)
  // Radio del dot proporcional, mínimo 1.4 para que se vea en sizes pequeños.
  const dotR = Math.max(1.4, size * 0.085)

  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      aria-hidden
      className={cn("inline-block shrink-0", className)}
    >
      {/* Sombra sutil bajo el stroke para sensación de profundidad */}
      <path
        d="M 3 21 L 3 3 L 12 13 L 21 3 L 21 21"
        stroke="oklch(0 0 0 / 0.25)"
        strokeWidth={stroke}
        strokeLinejoin="miter"
        strokeLinecap="square"
        transform="translate(0.5, 0.5)"
      />
      {/* M outline en ámbar — la marca principal */}
      <path
        d="M 3 21 L 3 3 L 12 13 L 21 3 L 21 21"
        stroke="var(--amber)"
        strokeWidth={stroke}
        strokeLinejoin="miter"
        strokeLinecap="square"
      />
      {/* Mastery point: valle central destacado en violet */}
      {withDot && <circle cx="12" cy="13" r={dotR} fill="var(--violet)" />}
    </svg>
  )
}
