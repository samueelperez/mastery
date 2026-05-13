import type { PostMortemPayload } from "@/lib/core/ws"

export const CHAT_SEED_POST_MORTEM_KEY = "mt.chat_seed_post_mortem"

export interface ChatSeedPostMortem {
  postMortem: PostMortemPayload
  suggested_message: string
}

/** One-shot read — consumes on read (sessionStorage.removeItem). Prevents
 * re-injection on remount. */
export function readChatSeedPostMortem(): ChatSeedPostMortem | null {
  if (typeof window === "undefined") return null
  const raw = window.sessionStorage.getItem(CHAT_SEED_POST_MORTEM_KEY)
  if (!raw) return null
  window.sessionStorage.removeItem(CHAT_SEED_POST_MORTEM_KEY)
  try {
    return JSON.parse(raw) as ChatSeedPostMortem
  } catch {
    return null
  }
}

export function writeChatSeedPostMortem(seed: ChatSeedPostMortem): void {
  if (typeof window === "undefined") return
  window.sessionStorage.setItem(CHAT_SEED_POST_MORTEM_KEY, JSON.stringify(seed))
}
