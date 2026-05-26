// Anonymous user ID management
// Generates a persistent browser-based user ID for paper trading
// When real auth is connected, replace getUserId() with the real user ID

const STORAGE_KEY = 'attix_user_id'

// Feature flag — set to false to disable anonymous paper trading
export const PAPER_TRADING_ENABLED = true

export function getUserId(): string {
  if (typeof window === 'undefined') return 'server'
  
  let id = localStorage.getItem(STORAGE_KEY)
  if (!id) {
    id = `anon-${crypto.randomUUID()}`
    localStorage.setItem(STORAGE_KEY, id)
  }
  return id
}
