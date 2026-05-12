"use client"

import Image from "next/image"
import { useState } from "react"

import { cn } from "@/lib/core/utils"

/** Mapeo símbolo → ruta del SVG. Coincide con `apps/web/public/crypto/*.svg`.
 * Símbolos no listados caen al fallback (círculo + letra). */
const LOGO_PATHS: Record<string, string> = {
  BTCUSDT: "/crypto/btc.svg",
  ETHUSDT: "/crypto/eth.svg",
  SOLUSDT: "/crypto/sol.svg",
  BNBUSDT: "/crypto/bnb.svg",
}

interface CryptoLogoProps {
  symbol: string
  size?: number
  className?: string
}

export function CryptoLogo({ symbol, size = 24, className }: CryptoLogoProps) {
  const [errored, setErrored] = useState(false)
  const path = LOGO_PATHS[symbol]
  const baseSym = symbol.replace(/USDT$/, "")

  if (!path || errored) {
    return (
      <span
        aria-hidden
        className={cn(
          "grid place-items-center rounded-full border border-border bg-[var(--bg-2)]",
          "font-mono text-[10px] font-semibold text-[var(--fg-2)]",
          className,
        )}
        style={{ width: size, height: size }}
      >
        {baseSym.slice(0, 1)}
      </span>
    )
  }

  return (
    <Image
      src={path}
      alt=""
      aria-hidden
      width={size}
      height={size}
      className={cn("rounded-full", className)}
      onError={() => setErrored(true)}
      unoptimized
    />
  )
}
