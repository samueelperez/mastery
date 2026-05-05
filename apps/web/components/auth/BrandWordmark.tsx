import { BrandMark } from "@/components/nav/BrandMark"

/** Header compacto para el form-card de /auth/*. La identidad fuerte vive
 * en `AuthShowcase` (left pitch). Aquí basta brand mark + caption. */
export function BrandWordmark({ caption }: { caption: string }) {
  return (
    <div className="flex flex-col items-start gap-2.5">
      <div className="flex items-center gap-2">
        <BrandMark size={18} />
        <span className="font-sans text-[14px] tracking-tight">
          <span className="font-semibold text-foreground">Mastery</span>{" "}
          <span className="font-medium text-[var(--fg-2)]">Trader</span>
        </span>
      </div>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
        {caption}
      </span>
    </div>
  )
}
