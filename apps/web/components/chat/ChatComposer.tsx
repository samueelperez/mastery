"use client"

import {
  ArrowUpIcon,
  Eraser,
  PlusIcon,
  SquareIcon,
  TagIcon,
} from "lucide-react"
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  type FormEvent,
  type KeyboardEvent,
} from "react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/core/utils"

export interface ChatComposerHandle {
  focus(): void
  insert(text: string): void
}

interface ChatComposerProps {
  value: string
  onChange: (value: string) => void
  onSubmit: (text: string) => void
  /** Status del useChat de ai-sdk: ready/submitted/streaming/error. */
  status: "ready" | "submitted" | "streaming" | "error"
  onStop?: () => void
  /** Símbolo activo que se inserta vía el menu "Insertar símbolo". */
  activeSymbol: string
  activeTimeframe: string
  /** Acción del menu para limpiar conversación. */
  onClearMessages?: () => void
  /** True si hay mensajes en la conversación (controla disabled del clear). */
  hasMessages?: boolean
  placeholder?: string
  className?: string
}

/** Composer del chat — textarea con auto-grow + footer con menu de acciones,
 *  hints de teclado y botón submit/stop.
 *
 *  Diseño explícito: nada de Provider context, refs cruzados, attachments
 *  o portales como el `<PromptInput>` de ai-elements. Sólo textarea
 *  controlled + dropdown menu nativo de shadcn. */
export const ChatComposer = forwardRef<ChatComposerHandle, ChatComposerProps>(
  function ChatComposer(props, ref) {
    const {
      value,
      onChange,
      onSubmit,
      status,
      onStop,
      activeSymbol,
      activeTimeframe,
      onClearMessages,
      hasMessages = false,
      placeholder = "Pregunta al copiloto…",
      className,
    } = props

    const textareaRef = useRef<HTMLTextAreaElement | null>(null)
    const isComposing = useRef(false)
    const submitting = status === "submitted" || status === "streaming"

    useImperativeHandle(
      ref,
      () => ({
        focus: () => textareaRef.current?.focus(),
        insert: (text: string) => {
          const ta = textareaRef.current
          if (!ta) return
          const start = ta.selectionStart ?? value.length
          const end = ta.selectionEnd ?? value.length
          const next = value.slice(0, start) + text + value.slice(end)
          onChange(next)
          // Restaurar el caret al final del texto insertado tras el siguiente
          // render (Date.now hack innecesario; usamos un microtask).
          queueMicrotask(() => {
            ta.focus()
            const pos = start + text.length
            ta.setSelectionRange(pos, pos)
          })
        },
      }),
      [value, onChange],
    )

    // Auto-resize del textarea — el atributo `field-sizing: content` lo hace
    // nativamente en Chromium 123+/Firefox 130+, pero como fallback ajustamos
    // height manualmente cada cambio.
    useEffect(() => {
      const ta = textareaRef.current
      if (!ta) return
      ta.style.height = "auto"
      const max = 192 // 48 * 4 = 12rem cap
      ta.style.height = `${Math.min(ta.scrollHeight, max)}px`
    }, [value])

    const submit = useCallback(() => {
      const trimmed = value.trim()
      if (!trimmed || submitting) return
      onSubmit(trimmed)
    }, [value, submitting, onSubmit])

    const handleSubmit = useCallback(
      (e: FormEvent<HTMLFormElement>) => {
        e.preventDefault()
        submit()
      },
      [submit],
    )

    const handleKeyDown = useCallback(
      (e: KeyboardEvent<HTMLTextAreaElement>) => {
        // Shift+Enter o composing = newline. Enter solo = submit.
        if (e.key === "Enter" && !e.shiftKey && !isComposing.current) {
          e.preventDefault()
          submit()
        }
      },
      [submit],
    )

    const insertActiveSymbol = useCallback(() => {
      const ref = `${activeSymbol} ${activeTimeframe}`
      const ta = textareaRef.current
      if (!ta) {
        // No textarea ref aún: append simple
        onChange(value.length === 0 ? `${ref} ` : `${value.trimEnd()} ${ref} `)
        return
      }
      const start = ta.selectionStart ?? value.length
      const end = ta.selectionEnd ?? value.length
      const before = value.slice(0, start)
      const after = value.slice(end)
      // Smart spacing: si lo estamos pegando a otra palabra, prepend espacio.
      const needsLeading = before.length > 0 && !/\s$/.test(before)
      const needsTrailing = after.length > 0 && !/^\s/.test(after)
      const insertion = `${needsLeading ? " " : ""}${ref}${needsTrailing ? " " : " "}`
      const next = before + insertion + after
      onChange(next)
      queueMicrotask(() => {
        ta.focus()
        const pos = start + insertion.length
        ta.setSelectionRange(pos, pos)
      })
    }, [activeSymbol, activeTimeframe, value, onChange])

    const canSubmit = value.trim().length > 0 && !submitting

    return (
      <form
        onSubmit={handleSubmit}
        className={cn(
          "flex flex-col gap-2 rounded-md border border-border bg-card",
          "focus-within:border-[var(--violet)] focus-within:ring-1 focus-within:ring-[var(--violet)]/40",
          "transition-colors",
          className,
        )}
      >
        <textarea
          ref={textareaRef}
          name="message"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => {
            isComposing.current = true
          }}
          onCompositionEnd={() => {
            isComposing.current = false
          }}
          placeholder={placeholder}
          rows={1}
          className={cn(
            "w-full resize-none bg-transparent px-3 pt-2.5 pb-1",
            "text-[14px] leading-[1.5] text-foreground placeholder:text-[var(--fg-3)]",
            "outline-none ring-0",
            "field-sizing-content max-h-48 min-h-[2.25rem]",
          )}
          aria-label="mensaje al copiloto"
        />

        <div className="flex items-center gap-2 px-2 pb-2">
          {/* Action menu */}
          <DropdownMenu>
            <DropdownMenuTrigger
              type="button"
              aria-label="acciones rápidas"
              title="acciones rápidas"
              className={cn(
                "grid size-7 place-items-center rounded text-[var(--fg-2)]",
                "transition-colors hover:bg-[var(--bg-2)] hover:text-foreground",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1",
              )}
            >
              <PlusIcon className="size-3.5" aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" sideOffset={6}>
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault()
                  insertActiveSymbol()
                }}
                className="text-[13px]"
              >
                <TagIcon className="size-3.5" aria-hidden />
                Insertar{" "}
                <strong className="text-foreground">
                  {activeSymbol} {activeTimeframe}
                </strong>
              </DropdownMenuItem>
              {onClearMessages && (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault()
                    onClearMessages()
                  }}
                  disabled={!hasMessages}
                  className="text-[13px]"
                >
                  <Eraser className="size-3.5" aria-hidden />
                  Limpiar conversación
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>

          <span className="hidden items-center gap-2 sm:inline-flex">
            <span className="font-mono text-[10px] tracking-[0.06em] text-[var(--fg-3)]">
              <kbd className="kbd">↵</kbd>
              {" enviar"}
            </span>
            <span className="font-mono text-[10px] tracking-[0.06em] text-[var(--fg-3)]">
              <kbd className="kbd">⇧↵</kbd>
              {" nueva línea"}
            </span>
          </span>

          {/* Submit / stop button */}
          {submitting ? (
            <button
              type="button"
              onClick={() => onStop?.()}
              aria-label="parar respuesta"
              className={cn(
                "ml-auto grid size-8 place-items-center rounded-md",
                "bg-[var(--bg-2)] text-foreground transition-colors hover:bg-[var(--bg-3)]",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
              )}
            >
              {status === "streaming" ? (
                <SquareIcon className="size-3" fill="currentColor" aria-hidden />
              ) : (
                <Spinner className="size-3.5" aria-hidden />
              )}
            </button>
          ) : (
            <button
              type="submit"
              disabled={!canSubmit}
              aria-label="enviar mensaje"
              className={cn(
                "ml-auto grid size-8 place-items-center rounded-md",
                "bg-[var(--amber)] text-[var(--bg-0)] transition-all",
                "hover:brightness-110",
                "disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:brightness-100",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
              )}
            >
              <ArrowUpIcon className="size-4" aria-hidden />
            </button>
          )}
        </div>
      </form>
    )
  },
)
