"use client"

import Link from "next/link"

import { AlertList } from "@/components/alerts/AlertList"

export default function AlertsPage() {
  return (
    <main className="overflow-x-hidden p-4 sm:p-6">
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="font-mono text-sm uppercase tracking-widest text-foreground">
            alertas
          </h1>
          <p className="text-xs text-muted-foreground">
            Las reglas disparan cuando una vela cierra cumpliendo tus condiciones;
            los eventos llegan en vivo a la campana. Crea nuevas desde el{" "}
            <Link
              href="/"
              className="text-foreground underline-offset-2 hover:underline"
            >
              chat
            </Link>{" "}
            (ej.{" "}
            &ldquo;alértame cuando BTCUSDT 4h cierre con RSI(14)&le;30&rdquo;).
          </p>
        </div>
        <AlertList />
      </div>
    </main>
  )
}
