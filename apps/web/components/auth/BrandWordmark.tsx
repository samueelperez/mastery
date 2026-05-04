/** Shared header for /auth/* pages — replaces the standalone Shield icon
 * from the upstream login clone with something that carries the trading
 * copilot's identity (gold accent bar + mono wordmark + uppercased caption).
 *
 * `caption` is the only knob: login uses "secure session", setup uses
 * "first-user setup", future /auth/error could use "session expired", etc.
 */
export function BrandWordmark({ caption }: { caption: string }) {
  return (
    <div className="flex flex-col items-center gap-2.5">
      <div className="h-0.5 w-10 bg-primary" aria-hidden />
      <span className="font-mono text-base tracking-tight text-foreground">
        trading-copilot
      </span>
      <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
        {caption}
      </span>
    </div>
  )
}
