"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useRef } from "react"

import {
  isTimeframe,
  isWatchSymbol,
  useActiveSymbol,
} from "./active-symbol"

/** Sync entre `?symbol=&tf=` y el store useActiveSymbol.
 *
 * Diseño one-way con hidratación inicial:
 *  - **En mount**: si la URL trae `?symbol=` válido, lo lee UNA VEZ y empuja
 *    al store (deep-linking gana sobre lo persistido en localStorage).
 *  - **Después**: cualquier cambio del store (click en sidebar, bridge del
 *    chat) escribe la URL con `router.replace`. Cambios manuales de la URL
 *    via address bar se ignoran tras la hidratación inicial.
 *
 *  Por qué no two-way reactivo: con dos effects bidireccionales se forma
 *  un loop — el effect URL→Store ve la URL aún sin actualizar tras un click,
 *  y revierte el store al valor previo.
 */
export function useActiveSymbolUrlSync() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const setBoth = useActiveSymbol((s) => s.setBoth)
  const symbol = useActiveSymbol((s) => s.symbol)
  const timeframe = useActiveSymbol((s) => s.timeframe)
  const hydrated = useRef(false)

  // 1) Mount: hidratar store desde la URL si trae query params válidos.
  useEffect(() => {
    if (hydrated.current) return
    hydrated.current = true
    const urlSymbol = searchParams.get("symbol")
    const urlTf = searchParams.get("tf")
    if (urlSymbol && isWatchSymbol(urlSymbol)) {
      const tfValid = urlTf && isTimeframe(urlTf) ? urlTf : undefined
      setBoth(urlSymbol, tfValid)
    }
    // searchParams sólo se lee una vez al montar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 2) Store → URL: cada cambio del store actualiza los query params.
  useEffect(() => {
    if (!hydrated.current) return
    if (typeof window === "undefined") return
    const desired = `symbol=${symbol}&tf=${timeframe}`
    const current = window.location.search.replace(/^\?/, "")
    if (current === desired) return
    router.replace(`${pathname}?${desired}`, { scroll: false })
  }, [symbol, timeframe, pathname, router])
}
