"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  CircleIcon,
  LightbulbIcon,
  MinusIcon,
  XIcon,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo } from "react"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Spinner } from "@/components/ui/spinner"
import {
  approveSetupRequest,
  cancelSetupRequest,
  fetchSetup,
  rejectSetupRequest,
  type SetupDetailDTO,
  type SetupEventDTO,
  type SetupTargetDTO,
} from "@/lib/core/api"
import {
  CHAT_SEED_REVIEW_KEY,
  type ChatSeedReview,
} from "@/lib/chat/seed-review"
import { formatSetupTag } from "@/lib/journal/format-setup-tag"
import { cn } from "@/lib/core/utils"

interface SetupDetailPanelProps {
  setupId: string | null
  onClose?: () => void
}

const EVENT_LABEL: Record<SetupEventDTO["event"], string> = {
  proposed: "propuesto",
  entry_hit: "entry tocado",
  tp_hit: "TP tocado",
  sl_hit: "SL tocado",
  expired: "expirado",
  manual_close: "cierre manual",
  cancelled: "cancelado",
  invalidated: "auto-invalidado",
  review_generated: "revisión IA",
  be_moved: "SL a breakeven",
  trailing_updated: "trailing actualizado",
  time_stopped: "time stop",
  approved: "aprobado",
  rejected_by_user: "rechazado",
}

const EVENT_TONE: Record<SetupEventDTO["event"], string> = {
  proposed: "var(--violet)",
  entry_hit: "var(--long)",
  tp_hit: "var(--long)",
  sl_hit: "var(--short)",
  expired: "var(--fg-3)",
  manual_close: "var(--fg-3)",
  cancelled: "var(--fg-3)",
  invalidated: "var(--amber)",
  review_generated: "var(--violet)",
  be_moved: "var(--long)",
  trailing_updated: "var(--long)",
  time_stopped: "var(--amber)",
  approved: "var(--long)",
  rejected_by_user: "var(--short)",
}

const STATUS_LABEL: Record<SetupDetailDTO["status"], string> = {
  pending: "esperando",
  active: "activo",
  closed: "cerrado",
  cancelled: "cancelado",
}

const STATUS_TONE: Record<
  SetupDetailDTO["status"],
  { color: string; bg: string }
> = {
  pending: {
    color: "var(--violet)",
    bg: "color-mix(in oklch, var(--violet) 12%, transparent)",
  },
  active: {
    color: "var(--long)",
    bg: "color-mix(in oklch, var(--long) 12%, transparent)",
  },
  closed: {
    color: "var(--fg-2)",
    bg: "color-mix(in oklch, var(--fg-2) 8%, transparent)",
  },
  cancelled: {
    color: "var(--fg-3)",
    bg: "color-mix(in oklch, var(--fg-3) 8%, transparent)",
  },
}

export function SetupDetailPanel({
  setupId,
  onClose,
}: SetupDetailPanelProps) {
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery<SetupDetailDTO>({
    queryKey: ["setup-detail", setupId],
    queryFn: ({ signal }) => fetchSetup(setupId!, { signal }),
    enabled: !!setupId,
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => cancelSetupRequest(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["setup-list"] })
      queryClient.invalidateQueries({ queryKey: ["setup-detail", setupId] })
    },
  })

  // C.3 — scout proposals require explicit approval before SetupRuntime
  // activates them on entry hit. These mutations are no-op (rejected by
  // backend with 4xx) on agent_proposal setups, but the UI hides them in
  // that case so the user only sees Approve/Reject when relevant.
  const approveMutation = useMutation({
    mutationFn: (id: string) => approveSetupRequest(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["setup-list"] })
      queryClient.invalidateQueries({ queryKey: ["diario-setups"] })
      queryClient.invalidateQueries({ queryKey: ["setup-detail", setupId] })
    },
  })
  const rejectMutation = useMutation({
    mutationFn: (id: string) => rejectSetupRequest(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["setup-list"] })
      queryClient.invalidateQueries({ queryKey: ["diario-setups"] })
      queryClient.invalidateQueries({ queryKey: ["setup-detail", setupId] })
    },
  })

  if (!setupId) {
    return (
      <aside className="flex h-full items-center justify-center bg-[var(--bg-1)] p-4 font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
        selecciona un setup
      </aside>
    )
  }
  if (isLoading) {
    return (
      <aside className="flex h-full items-center justify-center gap-2 bg-[var(--bg-1)] font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
        <Spinner /> cargando…
      </aside>
    )
  }
  if (error || !data) {
    return (
      <aside className="flex h-full items-center justify-center bg-[var(--bg-1)] p-4 font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--short)]">
        error al cargar el setup
      </aside>
    )
  }

  const isLong = data.side === "long"
  const sideIcon = isLong ? (
    <ArrowUpIcon className="size-4 text-[var(--long)]" aria-hidden />
  ) : (
    <ArrowDownIcon className="size-4 text-[var(--short)]" aria-hidden />
  )
  const statusTone = STATUS_TONE[data.status]
  const hasMistakes = Boolean(data.mistakes && data.mistakes.trim())
  const hasEvents = data.events.length > 0

  return (
    <aside className="flex h-full flex-col overflow-hidden border-l border-border bg-[var(--bg-1)]">
      <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {sideIcon}
          <span className="truncate font-mono text-sm font-semibold text-foreground">
            {data.symbol}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            · {data.timeframe} · {data.side.toUpperCase()}
          </span>
          <Badge
            variant="outline"
            className="ml-1 border-transparent font-mono text-[10px] uppercase tracking-[0.14em]"
            style={{
              color: statusTone.color,
              backgroundColor: statusTone.bg,
              borderColor: `color-mix(in oklch, ${statusTone.color} 30%, transparent)`,
            }}
          >
            {cancelledFlavorLabel(data) ?? STATUS_LABEL[data.status]}
          </Badge>
        </div>
        {onClose && (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onClose}
            aria-label="Cerrar"
          >
            <XIcon className="size-4" />
          </Button>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        <PnlHero data={data} />

        <Separator />

        <LevelsLadder data={data} />

        <Separator />

        <StrategyMeta data={data} />

        {hasMistakes && (
          <div className="px-3 pb-3">
            <Alert className="border-[color:var(--amber)]/30 bg-[color:var(--amber)]/[0.06]">
              <LightbulbIcon
                className="size-4 text-[var(--amber)]"
                aria-hidden
              />
              <AlertTitle
                className="text-[13px] font-semibold"
                style={{ color: "var(--amber)" }}
              >
                Lección
              </AlertTitle>
              <AlertDescription className="text-[13px] leading-relaxed text-foreground/85">
                {data.mistakes}
              </AlertDescription>
            </Alert>
          </div>
        )}

        <Accordion
          type="multiple"
          defaultValue={accordionDefaults({
            hasEvents,
            isAgent: data.source === "agent_proposal",
          })}
          className="border-t border-border"
        >
          <AccordionItem
            value="summary"
            className="border-b border-[color:var(--line-soft)] px-3"
          >
            <AccordionTrigger className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--fg-2)] hover:no-underline">
              resumen del setup
            </AccordionTrigger>
            <AccordionContent>
              <p className="whitespace-pre-line text-[13px] leading-relaxed text-[var(--fg-2)]">
                {data.summary_es_full ?? data.summary_text}
              </p>
            </AccordionContent>
          </AccordionItem>

          <AccordionItem
            value="events"
            disabled={!hasEvents}
            className="px-3"
          >
            <AccordionTrigger className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--fg-2)] hover:no-underline data-disabled:opacity-50">
              <span className="flex items-center gap-2">
                eventos
                <span className="font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                  {data.events.length}
                </span>
              </span>
            </AccordionTrigger>
            <AccordionContent>
              {hasEvents ? (
                <Timeline data={data} />
              ) : (
                <p className="text-[11px] text-[var(--fg-3)]">
                  sin eventos registrados.
                </p>
              )}
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </div>

      {data.status === "pending" && (
        <footer className="border-t border-border px-3 py-2.5">
          {data.source === "scout_proposal" &&
          !hasApprovalEvent(data.events) ? (
            // Scout autónomo sin aprobar — el SetupRuntime NO transitará a
            // active sin un evento `approved`. Aprobar = autorizar; rechazar
            // cancela. La opción "cancelar" plana queda escondida porque el
            // contexto natural aquí es decidir sobre la propuesta del scout.
            <div className="flex gap-2">
              <Button
                size="sm"
                className="flex-1 font-mono text-[11px] uppercase tracking-[0.14em]"
                disabled={
                  approveMutation.isPending || rejectMutation.isPending
                }
                onClick={() => approveMutation.mutate(data.id)}
              >
                {approveMutation.isPending ? "aprobando…" : "aprobar"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="flex-1 font-mono text-[11px] uppercase tracking-[0.14em]"
                disabled={
                  approveMutation.isPending || rejectMutation.isPending
                }
                onClick={() => rejectMutation.mutate(data.id)}
              >
                {rejectMutation.isPending ? "rechazando…" : "rechazar"}
              </Button>
            </div>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="w-full font-mono text-[11px] uppercase tracking-[0.14em]"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate(data.id)}
            >
              {cancelMutation.isPending ? "cancelando…" : "cancelar setup"}
            </Button>
          )}
        </footer>
      )}
    </aside>
  )
}

// -----------------------------------------------------------------------------
// P&L Hero — for closed: big R-multiple + diverging bar; for active/pending:
// más sutil, mostrando la fase del lifecycle.
// -----------------------------------------------------------------------------

function PnlHero({ data }: { data: SetupDetailDTO }) {
  const r = data.r_multiple
  const targets = data.targets
  const targetRs = useMemo(
    () =>
      targetRMultiples(
        data.entry_px,
        data.stop_loss_px,
        data.side as "long" | "short",
        targets,
      ),
    [data.entry_px, data.stop_loss_px, data.side, targets],
  )

  if (data.status === "closed" && r !== null) {
    const tone =
      r > 0 ? "var(--long)" : r < 0 ? "var(--short)" : "var(--fg-2)"
    return (
      <section className="px-3 py-4">
        <p className="eyebrow mb-1.5">resultado</p>
        <div className="flex items-baseline gap-2">
          <span
            className="font-mono text-3xl font-medium tabular-nums leading-none tracking-tight"
            style={{ color: tone }}
          >
            {r >= 0 ? "+" : ""}
            {r.toFixed(2)}
          </span>
          <span
            className="font-mono text-sm uppercase tracking-[0.16em]"
            style={{ color: tone, opacity: 0.7 }}
          >
            R
          </span>
        </div>
        <RBar rMultiple={r} targetRs={targetRs} />
        <div className="mt-2 flex items-center justify-between font-mono text-[11px] tabular-nums text-[var(--fg-3)]">
          <span>
            entry{" "}
            <span className="text-foreground">
              {formatPrice(data.entry_px)}
            </span>
          </span>
          {data.exit_px !== null && (
            <span>
              exit{" "}
              <span style={{ color: tone }}>
                {formatPrice(data.exit_px)}
              </span>
            </span>
          )}
        </div>
      </section>
    )
  }

  if (data.status === "active") {
    return (
      <section className="px-3 py-4">
        <p className="eyebrow mb-1.5">en curso</p>
        <p className="text-[14px] text-foreground">
          entry tocado · siguiendo hacia{" "}
          <span className="font-mono text-[var(--long)] tabular-nums">
            {targets.length} TPs
          </span>
        </p>
        {data.entry_hit_at && (
          <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            activado {formatRelative(data.entry_hit_at)}
          </p>
        )}
      </section>
    )
  }

  if (data.status === "pending") {
    return (
      <section className="px-3 py-4">
        <p className="eyebrow mb-1.5">esperando</p>
        <p className="text-[14px] text-foreground">
          entry en{" "}
          <span className="font-mono tabular-nums">
            {formatPrice(data.entry_px)}
          </span>
        </p>
        {data.proposed_at && (
          <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            propuesto {formatRelative(data.proposed_at)}
          </p>
        )}
      </section>
    )
  }

  return (
    <section className="px-3 py-4">
      <p className="eyebrow mb-1.5">cancelado</p>
      <p className="text-[14px] text-[var(--fg-3)]">
        este setup nunca llegó a activarse.
      </p>
    </section>
  )
}

/** Diverging bar: SL (-1R) en el extremo izquierdo, mejor TP en el derecho.
 *  Llenamos desde el 0R (entry) hasta el R-multiple actual; el sentido del
 *  llenado indica win/loss. Tick marks marcan dónde están SL y cada TP. */
function RBar({
  rMultiple,
  targetRs,
}: {
  rMultiple: number
  targetRs: number[]
}) {
  const minR = -1
  // Asegura headroom incluso si todos los TPs están entre 0 y 1.
  const maxR = Math.max(...targetRs, rMultiple, 1.5)
  const span = maxR - minR
  const pctOf = (r: number) =>
    Math.max(0, Math.min(100, ((r - minR) / span) * 100))

  const zeroPct = pctOf(0)
  const exitPct = pctOf(rMultiple)
  const fillStart = Math.min(zeroPct, exitPct)
  const fillEnd = Math.max(zeroPct, exitPct)
  const fillTone =
    rMultiple > 0
      ? "var(--long)"
      : rMultiple < 0
        ? "var(--short)"
        : "var(--fg-3)"

  return (
    <div className="mt-3 mb-1">
      <div className="relative h-6 w-full">
        {/* track */}
        <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-[var(--bg-3)]" />
        {/* fill */}
        <div
          className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full"
          style={{
            left: `${fillStart}%`,
            width: `${Math.max(fillEnd - fillStart, 0.5)}%`,
            backgroundColor: fillTone,
          }}
        />
        {/* SL marker */}
        <Tick
          left={pctOf(-1)}
          color="var(--short)"
          size={8}
          shape="square"
        />
        {/* zero / entry tick */}
        <Tick left={zeroPct} color="var(--fg-3)" size={6} shape="bar" />
        {/* TP markers */}
        {targetRs.map((tr, i) => (
          <Tick
            key={i}
            left={pctOf(tr)}
            color="var(--long)"
            size={8}
            shape="square"
          />
        ))}
        {/* exit pointer */}
        <div
          className="absolute -top-0.5 size-3 -translate-x-1/2 rotate-45"
          style={{
            left: `${exitPct}%`,
            backgroundColor: fillTone,
            borderRadius: 1,
          }}
        />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
        <span style={{ color: "var(--short)" }}>SL −1R</span>
        <span>0</span>
        <span style={{ color: "var(--long)" }}>
          TP{targetRs.length > 0 ? ` +${maxR.toFixed(1)}R` : ""}
        </span>
      </div>
    </div>
  )
}

function Tick({
  left,
  color,
  size,
  shape,
}: {
  left: number
  color: string
  size: number
  shape: "square" | "bar"
}) {
  if (shape === "bar") {
    return (
      <div
        aria-hidden
        className="absolute top-1/2 h-3 w-px -translate-x-1/2 -translate-y-1/2"
        style={{ left: `${left}%`, backgroundColor: color }}
      />
    )
  }
  return (
    <div
      aria-hidden
      className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-sm"
      style={{
        left: `${left}%`,
        width: size,
        height: size,
        backgroundColor: color,
      }}
    />
  )
}

// -----------------------------------------------------------------------------
// LevelsLadder — vertical price ladder. Long: TPs encima → entry → SL;
// short al revés. Cada nivel con icono que indica si tocó (✓/✗) o no.
// -----------------------------------------------------------------------------

interface Level {
  key: string
  kind: "tp" | "entry" | "sl"
  label: string
  price: number
  hit: boolean
}

function LevelsLadder({ data }: { data: SetupDetailDTO }) {
  const isLong = data.side === "long"

  const levels: Level[] = useMemo(() => {
    const out: Level[] = []
    for (let i = 0; i < data.targets.length; i++) {
      const t = data.targets[i]!
      out.push({
        key: `tp-${i}`,
        kind: "tp",
        label: t.label || `TP${i + 1}`,
        price: t.price,
        hit: Boolean(t.hit_at),
      })
    }
    out.push({
      key: "entry",
      kind: "entry",
      label: "Entry",
      price: data.entry_px,
      hit: Boolean(data.entry_hit_at),
    })
    if (data.stop_loss_px !== null) {
      out.push({
        key: "sl",
        kind: "sl",
        label: "SL",
        price: data.stop_loss_px,
        // Para closed: si exit_px tocó SL (cerca de stop_loss), marcamos hit.
        hit:
          data.status === "closed" &&
          data.exit_px !== null &&
          Math.abs(data.exit_px - data.stop_loss_px) /
            Math.max(Math.abs(data.stop_loss_px), 1e-9) <
            0.005,
      })
    }
    // Long: alto → bajo. Short: alto → bajo (mismo orden — el precio sube
    // siempre hacia arriba; el lector entiende el side por la dirección
    // entre entry y TPs).
    out.sort((a, b) => b.price - a.price)
    return out
  }, [data])

  return (
    <section className="px-3 py-3">
      <p className="eyebrow mb-2">niveles</p>
      <ul className="flex flex-col">
        {levels.map((lvl, i) => (
          <LevelRow
            key={lvl.key}
            level={lvl}
            isLong={isLong}
            withConnector={i < levels.length - 1}
          />
        ))}
      </ul>
    </section>
  )
}

function LevelRow({
  level,
  isLong,
  withConnector,
}: {
  level: Level
  isLong: boolean
  withConnector: boolean
}) {
  const tone =
    level.kind === "tp"
      ? "var(--long)"
      : level.kind === "sl"
        ? "var(--short)"
        : "var(--fg-2)"
  const isWinningLevel = level.kind === "tp"
  const Icon = level.hit
    ? CheckIcon
    : level.kind === "entry"
      ? CircleIcon
      : MinusIcon

  return (
    <li className="relative grid grid-cols-[20px_1fr_auto] items-center gap-2 py-1.5">
      <div
        className="relative flex size-5 items-center justify-center rounded-sm"
        style={{
          backgroundColor: level.hit
            ? `color-mix(in oklch, ${tone} 18%, transparent)`
            : "transparent",
          border: `1px solid color-mix(in oklch, ${tone} ${level.hit ? "50%" : "30%"}, transparent)`,
        }}
      >
        <Icon
          className="size-3"
          style={{ color: tone, opacity: level.hit ? 1 : 0.6 }}
          aria-hidden
        />
        {withConnector && (
          <span
            aria-hidden
            className="absolute left-1/2 top-full h-1.5 w-px -translate-x-1/2"
            style={{
              backgroundColor: `color-mix(in oklch, ${tone} 25%, transparent)`,
            }}
          />
        )}
      </div>
      <div className="flex flex-col leading-tight">
        <span
          className={cn(
            "text-[13px] font-medium tracking-tight",
            level.kind === "entry"
              ? "text-foreground"
              : "text-foreground/85",
          )}
        >
          {level.label}
        </span>
        {level.hit && (
          <span
            className="font-mono text-[9px] uppercase tracking-[0.16em]"
            style={{ color: tone }}
          >
            {isWinningLevel
              ? "tocado"
              : level.kind === "sl"
                ? "stop hit"
                : "activado"}
          </span>
        )}
      </div>
      <span
        className={cn(
          "font-mono text-[12px] tabular-nums",
          level.kind === "entry"
            ? "text-foreground"
            : "text-foreground/90",
        )}
        style={{
          color:
            level.kind === "tp"
              ? "var(--long)"
              : level.kind === "sl"
                ? "var(--short)"
                : undefined,
        }}
      >
        {formatPrice(level.price)}
      </span>
      {/* tiny long/short hint — solo en entry para indicar dirección */}
      {level.kind === "entry" && (
        <span
          aria-hidden
          className="absolute -right-0.5 top-1.5 font-mono text-[8px] uppercase tracking-[0.18em]"
          style={{
            color: isLong ? "var(--long)" : "var(--short)",
            opacity: 0.6,
          }}
        >
          {isLong ? "↑" : "↓"}
        </span>
      )}
    </li>
  )
}

// -----------------------------------------------------------------------------
// StrategyMeta — setup_tag header + régimen / confianza / fuente apilados.
// Mismo patrón que apps/web/components/journal/JournalEntryCard.tsx (label
// mono 11px arriba, valor abajo) para coherencia con la página de estrategias.
// -----------------------------------------------------------------------------

function StrategyMeta({ data }: { data: SetupDetailDTO }) {
  const confidenceTone =
    data.confidence === "high"
      ? "var(--long)"
      : data.confidence === "medium"
        ? "var(--amber)"
        : "var(--fg-3)"
  return (
    <section className="px-3 py-3">
      <p className="eyebrow mb-2">estrategia</p>
      <div className="mb-3 flex flex-col gap-0.5">
        <p className="text-[14px] font-medium text-foreground">
          {formatSetupTag(data.setup_tag)}
        </p>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
          {data.setup_tag}
        </p>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <StrategyField label="régimen" value={data.regime} />
        {data.confidence && (
          <StrategyField
            label="confianza"
            value={data.confidence}
            tone={confidenceTone}
          />
        )}
        <StrategyField label="fuente" value={data.source} />
      </div>
    </section>
  )
}

function StrategyField({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div>
      <p className="mb-1.5 font-mono text-[11px] uppercase tracking-widest text-[var(--fg-3)]">
        {label}
      </p>
      <p
        className="font-mono text-xs text-foreground"
        style={tone ? { color: tone } : undefined}
      >
        {value}
      </p>
    </div>
  )
}

// -----------------------------------------------------------------------------
// Timeline — visual list con conector vertical.
// -----------------------------------------------------------------------------

/** Resuelve label y tono para un event kind. Si backend introduce un kind
 *  nuevo y este file no se actualiza, mostramos el nombre raw + tono neutro
 *  en lugar de crashear con undefined (CSS color invalid → render glitch). */
function eventVisuals(event: SetupEventDTO["event"]): {
  label: string
  tone: string
} {
  return {
    label: EVENT_LABEL[event] ?? event,
    tone: EVENT_TONE[event] ?? "var(--fg-3)",
  }
}

function Timeline({ data }: { data: SetupDetailDTO }) {
  const router = useRouter()
  const events = data.events

  const openReviewInChat = (e: SetupEventDTO) => {
    const review = buildTradeReviewFromEvent(data, e)
    if (!review) return
    const setupLabel = formatSetupTag(data.setup_tag)
    const seed: ChatSeedReview = {
      review,
      suggested_message: `Cuéntame más sobre esta revisión de ${data.symbol} ${data.timeframe} (${setupLabel}). ¿Qué señales conviene vigilar ahora?`,
    }
    try {
      window.sessionStorage.setItem(
        CHAT_SEED_REVIEW_KEY,
        JSON.stringify(seed),
      )
    } catch {
      // sessionStorage puede no estar disponible (modo incógnito estricto);
      // navegamos igual — la tarjeta no se inyectará pero el chat abre limpio.
    }
    router.push("/")
  }

  return (
    <ol className="flex flex-col">
      {events.map((e, i) => {
        const isReview = e.event === "review_generated"
        const { label: eventLabel, tone: eventTone } = eventVisuals(e.event)
        const headerLabel = (
          <span
            className={cn(
              "font-mono text-[11px]",
              isReview && "transition-colors hover:underline focus-visible:underline focus-visible:outline-none",
            )}
            style={{ color: eventTone }}
          >
            {eventLabel}
            {isReview && (
              <span
                aria-hidden
                className="ml-1 text-[10px] opacity-60"
              >
                ↗
              </span>
            )}
          </span>
        )
        return (
          <li
            key={e.id}
            className="relative grid grid-cols-[16px_1fr] gap-2 pb-3 last:pb-0"
          >
            <div className="flex flex-col items-center">
              <span
                aria-hidden
                className="mt-1 size-2 rounded-full"
                style={{ backgroundColor: eventTone }}
              />
              {i < events.length - 1 && (
                <span
                  aria-hidden
                  className="mt-0.5 w-px flex-1"
                  style={{
                    backgroundColor: `color-mix(in oklch, ${eventTone} 25%, transparent)`,
                  }}
                />
              )}
            </div>
            <div className="flex flex-col gap-0.5 pb-1">
              <div className="flex items-baseline justify-between gap-2">
                {isReview ? (
                  <button
                    type="button"
                    onClick={() => openReviewInChat(e)}
                    className="cursor-pointer bg-transparent p-0 text-left"
                    title="abrir revisión completa en el chat"
                  >
                    {headerLabel}
                  </button>
                ) : (
                  headerLabel
                )}
                <span className="font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                  {formatDateTime(e.candle_ts)}
                </span>
              </div>
              {isReview ? (
                <ReviewEventPayload payload={e.payload} />
              ) : (
                Object.keys(e.payload).length > 0 && (
                  <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-sm bg-[var(--bg-2)]/40 px-1.5 py-1 font-mono text-[9px] leading-relaxed text-[var(--fg-3)]">
                    {JSON.stringify(e.payload)}
                  </pre>
                )
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** Construye un TradeReviewPayload (shape consumido por TradeReviewCard) a
 * partir del setup actual + el evento `review_generated` persistido. Devuelve
 * null si el payload del evento está incompleto (no expandido por backend
 * pre-rv2). */
function buildTradeReviewFromEvent(
  data: SetupDetailDTO,
  e: SetupEventDTO,
): import("@/lib/core/ws").TradeReviewPayload | null {
  const p = e.payload as Record<string, unknown>
  const reviewId = typeof p.review_id === "string" ? p.review_id : null
  const summary = typeof p.summary === "string" ? p.summary : null
  const rationale = typeof p.rationale === "string" ? p.rationale : null
  const currentState = p.current_state as
    | "on_track"
    | "at_risk"
    | "reversing"
    | undefined
  const recommendation = p.recommendation as
    | "hold"
    | "tighten_sl"
    | "partial_close"
    | "exit_now"
    | undefined
  const triggerKind = p.trigger_kind as
    | "entry_hit"
    | "tp_partial"
    | "time_elapsed"
    | "price_move"
    | "approaching_sl"
    | "regime_change"
    | undefined
  const price = typeof p.price_at_review === "number" ? p.price_at_review : null
  const citations = Array.isArray(p.citations)
    ? (p.citations as { tool_name: string; snapshot: Record<string, unknown> }[])
    : []

  // rationale + price son los campos nuevos persistidos en rv2; sin ellos no
  // podemos construir una TradeReviewCard útil. En ese caso devolvemos null
  // y el caller deja el click sin efecto visible (el panel ya muestra summary).
  if (!reviewId || !summary || !rationale || price === null) return null
  if (!currentState || !recommendation || !triggerKind) return null

  return {
    review_id: reviewId,
    setup_id: data.id,
    symbol: data.symbol,
    timeframe: data.timeframe,
    side: data.side as "long" | "short",
    trigger_kind: triggerKind,
    trigger_payload: {},
    current_state: currentState,
    recommendation,
    summary,
    rationale,
    citations,
    price_at_review: price,
    created_at: e.candle_ts,
  }
}

// -----------------------------------------------------------------------------
// review_generated payload renderer (timeline)
// -----------------------------------------------------------------------------

const REVIEW_REC_LABEL: Record<string, string> = {
  hold: "mantener",
  tighten_sl: "ajustar SL",
  partial_close: "cerrar parcial",
  exit_now: "salir ya",
}

const REVIEW_REC_TONE: Record<string, string> = {
  hold: "var(--fg-3)",
  tighten_sl: "var(--violet)",
  partial_close: "var(--amber)",
  exit_now: "var(--short)",
}

const REVIEW_STATE_LABEL: Record<string, string> = {
  on_track: "en rumbo",
  at_risk: "en riesgo",
  reversing: "revirtiendo",
}

function ReviewEventPayload({ payload }: { payload: Record<string, unknown> }) {
  const summary = typeof payload.summary === "string" ? payload.summary : null
  const recommendation =
    typeof payload.recommendation === "string" ? payload.recommendation : null
  const currentState =
    typeof payload.current_state === "string" ? payload.current_state : null
  const triggerKind =
    typeof payload.trigger_kind === "string" ? payload.trigger_kind : null
  if (!summary) {
    // Fallback al JSON crudo si el payload no es shape esperado.
    return (
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-sm bg-[var(--bg-2)]/40 px-1.5 py-1 font-mono text-[9px] leading-relaxed text-[var(--fg-3)]">
        {JSON.stringify(payload)}
      </pre>
    )
  }
  return (
    <div className="flex flex-col gap-1 rounded-sm bg-[var(--bg-2)]/40 px-1.5 py-1">
      <div className="flex flex-wrap items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider">
        {currentState && (
          <span style={{ color: "var(--fg-3)" }}>
            {REVIEW_STATE_LABEL[currentState] ?? currentState}
          </span>
        )}
        {recommendation && (
          <>
            <span style={{ color: "var(--fg-4)" }}>·</span>
            <span style={{ color: REVIEW_REC_TONE[recommendation] ?? "var(--fg-2)" }}>
              {REVIEW_REC_LABEL[recommendation] ?? recommendation}
            </span>
          </>
        )}
        {triggerKind && (
          <>
            <span style={{ color: "var(--fg-4)" }}>·</span>
            <span style={{ color: "var(--fg-4)" }}>{triggerKind}</span>
          </>
        )}
      </div>
      <p className="text-[11px] leading-snug text-[var(--fg-1)]">{summary}</p>
    </div>
  )
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

/** Para status='cancelled', diferenciamos entre cancel manual, auto-
 *  invalidación por condición DSL y expiración wall-clock por expires_at,
 *  inspeccionando el último event terminal. Devuelve null si no es un
 *  cancel o no podemos discriminar.
 *
 *  Las tres causas son legalmente distintas (cancelled = user action,
 *  invalidated = DSL condition fired, expired = expires_at fence) y la UI
 *  debe reflejar la diferencia para el post-mortem analysis del usuario. */
/** Helper para decidir el footer del pending: si el setup es scout y no
 *  tiene aún un evento `approved`, mostramos los botones Approve/Reject;
 *  si ya está aprobado (o es agent_proposal), el footer normal de cancelar. */
function hasApprovalEvent(events: SetupEventDTO[]): boolean {
  return events.some((e) => e.event === "approved")
}

function cancelledFlavorLabel(data: SetupDetailDTO): string | null {
  if (data.status !== "cancelled") return null
  // El último event determina la causa. Buscamos en reverso por si hay
  // eventos `review_generated` posteriores al cierre que ocluyan el terminal.
  for (let i = data.events.length - 1; i >= 0; i--) {
    const e = data.events[i]!
    if (e.event === "invalidated") return "auto-invalidado"
    if (e.event === "expired") return "expirado"
    if (e.event === "cancelled") return "cancelado"
  }
  return null
}

function targetRMultiples(
  entry: number,
  stopLoss: number | null,
  side: "long" | "short",
  targets: SetupTargetDTO[],
): number[] {
  if (stopLoss === null) return []
  const risk = Math.abs(entry - stopLoss)
  if (risk === 0) return []
  return targets.map((t) => {
    const reward = side === "long" ? t.price - entry : entry - t.price
    return reward / risk
  })
}

function accordionDefaults({
  hasEvents,
  isAgent,
}: {
  hasEvents: boolean
  isAgent: boolean
}): string[] {
  const out: string[] = []
  // Resumen abierto por defecto solo para setups del agente (su summary
  // tiene info útil); en csv_import es texto plantilla redundante.
  if (isAgent) out.push("summary")
  if (hasEvents) out.push("events")
  return out
}

function formatPrice(price: number | null | undefined): string {
  if (price == null || !Number.isFinite(price)) return "—"
  if (price >= 1000)
    return price.toLocaleString(undefined, { maximumFractionDigits: 1 })
  if (price >= 1)
    return price.toLocaleString(undefined, { maximumFractionDigits: 3 })
  return price.toLocaleString(undefined, { maximumFractionDigits: 6 })
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatRelative(iso: string): string {
  const dt = new Date(iso)
  const diffMs = Date.now() - dt.getTime()
  const min = Math.round(diffMs / 60_000)
  if (min < 1) return "ahora"
  if (min < 60) return `hace ${min}m`
  const h = Math.round(min / 60)
  if (h < 24) return `hace ${h}h`
  const d = Math.round(h / 24)
  return `hace ${d}d`
}
