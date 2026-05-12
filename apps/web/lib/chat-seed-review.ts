import type { TradeReviewPayload } from "@/lib/ws"

export const CHAT_SEED_REVIEW_KEY = "mt.chat_seed_review"

export interface ChatSeedReview {
  review: TradeReviewPayload
  suggested_message: string
}

export function readChatSeedReview(): ChatSeedReview | null {
  if (typeof window === "undefined") return null
  const raw = window.sessionStorage.getItem(CHAT_SEED_REVIEW_KEY)
  if (!raw) return null
  window.sessionStorage.removeItem(CHAT_SEED_REVIEW_KEY)
  try {
    return JSON.parse(raw) as ChatSeedReview
  } catch {
    return null
  }
}

export function writeChatSeedReview(seed: ChatSeedReview): void {
  if (typeof window === "undefined") return
  window.sessionStorage.setItem(CHAT_SEED_REVIEW_KEY, JSON.stringify(seed))
}
