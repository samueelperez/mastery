// Public env vars exposed to the browser. Never put secrets here.
export const env = {
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  wsUrl: process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000",
} as const
