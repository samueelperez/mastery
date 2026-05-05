"use client"

import {
  ActivityIcon,
  ArrowRightIcon,
  CrosshairIcon,
  LayersIcon,
  LineChartIcon,
  TargetIcon,
  TrendingUpIcon,
  type LucideIcon,
} from "lucide-react"

import { BrandMark } from "@/components/nav/BrandMark"
import { cn } from "@/lib/utils"

interface ChatEmptyStateProps {
  onSuggestion: (text: string) => void
}

interface Suggestion {
  prompt: string
  /** Símbolo · tf que se mostrará como badge a la derecha. */
  badge: string
  /** Icono específico del tipo de análisis. */
  icon: LucideIcon
  /** Color tone del icon container. */
  tone: "violet" | "amber" | "long"
}

interface Section {
  kicker: string
  title: string
  description: string
  items: Suggestion[]
}

const SECTIONS: Section[] = [
  {
    kicker: "01",
    title: "Lee el mercado",
    description: "Confluencia multi-tf, estructura, indicadores.",
    items: [
      {
        prompt: "analiza BTC en 4h",
        badge: "BTC · 4h",
        icon: TrendingUpIcon,
        tone: "violet",
      },
      {
        prompt: "estructura de ETH en 1d",
        badge: "ETH · 1d",
        icon: LineChartIcon,
        tone: "violet",
      },
      {
        prompt: "confluencia multi-tf de BTC",
        badge: "BTC · 4 tfs",
        icon: LayersIcon,
        tone: "violet",
      },
    ],
  },
  {
    kicker: "02",
    title: "Define tu setup",
    description: "Una idea de trade con entry, stop y targets — R:R ≥ 1.5.",
    items: [
      {
        prompt: "trade idea long en BTC 1h",
        badge: "BTC · 1h",
        icon: TargetIcon,
        tone: "amber",
      },
      {
        prompt: "RSI + MACD en SOL 1h",
        badge: "SOL · 1h",
        icon: ActivityIcon,
        tone: "amber",
      },
      {
        prompt: "EMAs 21/55/200 en BNB 4h",
        badge: "BNB · 4h",
        icon: CrosshairIcon,
        tone: "amber",
      },
    ],
  },
]

const TONE_BG: Record<Suggestion["tone"], string> = {
  violet:
    "bg-[var(--violet-soft)] text-[var(--violet)] group-hover:bg-[var(--violet-bg)]",
  amber:
    "bg-[var(--amber-soft)] text-[var(--amber)] group-hover:bg-[var(--amber-bg)]",
  long: "bg-[var(--long-bg)] text-[var(--long)]",
}

/** Empty state del chat — diseño editorial con backdrop decorativo,
 *  BrandMark con breathing pulse, hero con cursor blink, secciones
 *  numeradas y cards con icon container coloreado + badge de contexto. */
export function ChatEmptyState({ onSuggestion }: ChatEmptyStateProps) {
  return (
    <div className="relative flex h-full w-full flex-col items-center px-3 pt-4 pb-2">
      {/* Backdrop dot grid con radial mask para enfocar el centro */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 grid-bg opacity-[0.35]"
        style={{
          maskImage:
            "radial-gradient(ellipse at 50% 35%, black 0%, transparent 65%)",
          WebkitMaskImage:
            "radial-gradient(ellipse at 50% 35%, black 0%, transparent 65%)",
        }}
      />
      {/* Glow violet difuso detrás del brand */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-2 size-48 -translate-x-1/2 rounded-full opacity-50"
        style={{
          background:
            "radial-gradient(circle, oklch(0.68 0.18 290 / 0.18) 0%, transparent 60%)",
        }}
      />

      {/* Hero */}
      <div
        className="relative z-10 flex flex-col items-center gap-2 pb-5 motion-reduce:animate-none"
        style={{
          animation: "empty-fade-in 600ms ease-out both",
        }}
      >
        <span className="empty-pulse">
          <BrandMark size={36} withDot={false} />
        </span>
        <h2 className="font-sans text-[17px] font-semibold tracking-tight text-foreground">
          ¿Qué quieres analizar
          <span
            aria-hidden
            className="ml-0.5 inline-block h-[0.85em] w-[0.5ch] translate-y-[0.05em] bg-[var(--amber)] align-middle motion-reduce:animate-none [animation:cursor-blink_1s_steps(2)_infinite]"
          />
        </h2>
      </div>

      {/* Sections */}
      <div className="relative z-10 flex w-full max-w-[24rem] flex-col gap-4">
        {SECTIONS.map((section, sIdx) => (
          <section
            key={section.kicker}
            className="flex flex-col gap-2 motion-reduce:animate-none"
            style={{
              animation: `empty-slide-up 500ms cubic-bezier(0.2,0.8,0.2,1) ${
                100 + sIdx * 120
              }ms both`,
            }}
          >
            <header className="flex items-baseline gap-3">
              <span className="font-mono text-[10px] tabular-nums tracking-[0.2em] text-[var(--violet)]">
                {section.kicker}
              </span>
              <span className="font-sans text-[13px] font-semibold tracking-tight text-foreground">
                {section.title}
              </span>
              <span
                aria-hidden
                className="ml-auto h-px flex-1 bg-gradient-to-r from-[color:var(--line-strong)] via-[color:var(--line-soft)] to-transparent"
              />
            </header>
            <p className="pl-7 font-sans text-[11px] leading-relaxed text-[var(--fg-3)]">
              {section.description}
            </p>
            <ul className="flex flex-col gap-1.5">
              {section.items.map((item, i) => (
                <li
                  key={item.prompt}
                  className="motion-reduce:animate-none"
                  style={{
                    animation: `empty-slide-up 400ms cubic-bezier(0.2,0.8,0.2,1) ${
                      200 + sIdx * 120 + i * 60
                    }ms both`,
                  }}
                >
                  <SuggestionCard
                    suggestion={item}
                    onClick={() => onSuggestion(item.prompt)}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      <style>{`
        @keyframes empty-fade-in {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes empty-slide-up {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes empty-pulse {
          0%, 100% { transform: scale(1); filter: drop-shadow(0 0 0 oklch(0.78 0.16 75 / 0)); }
          50% { transform: scale(1.04); filter: drop-shadow(0 0 12px oklch(0.78 0.16 75 / 0.35)); }
        }
        .empty-pulse {
          display: inline-block;
          animation: empty-pulse 3.5s ease-in-out infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .empty-pulse { animation: none; }
        }
      `}</style>
    </div>
  )
}

interface SuggestionCardProps {
  suggestion: Suggestion
  onClick: () => void
}

function SuggestionCard({ suggestion, onClick }: SuggestionCardProps) {
  const Icon = suggestion.icon
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`enviar sugerencia: ${suggestion.prompt}`}
      className={cn(
        "group relative flex w-full items-center gap-3 rounded-md border border-[color:var(--line-soft)]",
        "bg-[var(--bg-1)]/60 px-3 py-2.5 text-left",
        "transition-all duration-200 ease-out",
        "hover:-translate-y-px hover:border-[oklch(0.55_0.16_290_/_0.5)] hover:bg-[var(--bg-2)]",
        "hover:shadow-[0_0_0_1px_oklch(0.68_0.18_290_/_0.15),0_8px_24px_-12px_oklch(0.68_0.18_290_/_0.4)]",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
      )}
    >
      {/* Icon container con tono semántico (violet/amber) */}
      <span
        aria-hidden
        className={cn(
          "grid size-8 shrink-0 place-items-center rounded-md transition-colors duration-200",
          TONE_BG[suggestion.tone],
        )}
      >
        <Icon className="size-4" aria-hidden />
      </span>

      {/* Pregunta + badge contexto */}
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="font-sans text-[13px] leading-tight tracking-tight text-foreground">
          {suggestion.prompt}
        </span>
        <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-[var(--fg-4)] transition-colors group-hover:text-[var(--fg-2)]">
          {suggestion.badge}
        </span>
      </span>

      <ArrowRightIcon
        className={cn(
          "size-3.5 shrink-0 text-[var(--fg-4)]",
          "transition-all duration-200",
          "group-hover:translate-x-1 group-hover:text-[var(--violet)]",
        )}
        aria-hidden
      />
    </button>
  )
}
