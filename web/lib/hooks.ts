import useSWR from 'swr'
import { useEffect, useRef } from 'react'
import { apiFetch } from '@/lib/api'
import type { AlertsResponse, Config, PaperTrade, PositionsSummary } from '@/lib/types'

function fetcher<T>(url: string): Promise<T> {
  return apiFetch<T>(url)
}

export function useAlerts() {
  return useSWR<AlertsResponse>('/api/alerts', fetcher<AlertsResponse>, {
    refreshInterval: 300000,
    dedupingInterval: 60000,
    revalidateOnFocus: true,
  })
}

export function usePositions() {
  return useSWR<PositionsSummary>('/api/positions', fetcher<PositionsSummary>, {
    refreshInterval: 60000,
    dedupingInterval: 15000,
    revalidateOnFocus: true,
  })
}

interface PaperTradesResponse {
  trades: PaperTrade[]
  stats: {
    total_trades: number
    open_trades: number
    closed_trades: number
    winners: number
    losers: number
    win_rate: number
    total_realized_pnl: number
    total_unrealized_pnl: number
    total_pnl: number
    balance: number
    starting_balance: number
    disabled?: boolean
  }
  message?: string
}

export function usePaperTrades(userId: string = 'default') {
  const encodedUserId = encodeURIComponent(userId)
  const result = useSWR<PaperTradesResponse>(`/api/paper-trades?userId=${encodedUserId}`, fetcher<PaperTradesResponse>, {
    refreshInterval: 120000,
    dedupingInterval: 30000,
    revalidateOnFocus: true,
  })

  // Trigger auto-close of expired/target-hit trades on each fetch, then re-fetch
  const hasRunAutoClose = useRef(false)
  useEffect(() => {
    if (!result.data || hasRunAutoClose.current) return
    const hasOpenTrades = result.data.trades?.some(t => t.status === 'open')
    if (!hasOpenTrades) return
    hasRunAutoClose.current = true
    apiFetch<{ closed: number }>(`/api/paper-trades?userId=${encodedUserId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-user-id': userId },
      body: JSON.stringify({ action: 'auto-close' }),
    }).then(res => {
      if (res.closed > 0) result.mutate()
    }).catch(() => {
      // Auto-close is best-effort
    }).finally(() => {
      // Allow re-run on next mount cycle
      setTimeout(() => { hasRunAutoClose.current = false }, 60000)
    })
  }, [result.data, encodedUserId, userId, result])

  return result
}

// ---------------------------------------------------------------------------
// Experiments dashboard (multi-account, from sync_dashboard_data.py export)
// ---------------------------------------------------------------------------

export interface AlpacaAccount {
  equity: number | null
  last_equity: number | null
  unrealized_pl: number | null
  portfolio_value: number | null
  cash: number | null
  buying_power: number | null
  day_pl: number | null
  positions: Array<{
    symbol: string
    qty: number
    market_value: number
    cost_basis: number
    unrealized_pl: number
    unrealized_plpc: number
    current_price: number
    avg_entry_price: number
    side: string
  }>
  error: string | null
  fetched_at: string
}

export interface ExperimentStats {
  total_closed: number
  wins: number
  losses: number
  win_rate: number
  total_pnl: number
  total_return_pct: number
  max_dd_pct: number
  max_dd_dollars: number
  open_count: number
  avg_pnl: number
  trades_week: number
  last_trade_date: string | null
  profit_factor: number | null
}

export interface ExperimentData {
  id: string
  name: string
  ticker: string
  creator: string
  live_since: string
  account_id: string
  notes: string
  backtest: { avg_return?: number; max_dd?: number; robust?: number }
  error: string | null
  alpaca: AlpacaAccount | null
  stats: ExperimentStats
  equity_curve: Array<{ date: string; cumulative_pnl: number; cumulative_pnl_pct: number }>
  open_positions: Array<{
    id: string; ticker: string; strategy_type: string
    entry_date: string; expiration: string
    short_strike: number; long_strike: number; contracts: number; credit: number
  }>
  recent_trades: Array<{
    id: string; ticker: string; strategy_type: string
    entry_date: string; exit_date: string
    short_strike: number; long_strike: number; contracts: number; credit: number
    pnl: number; exit_reason: string
  }>
}

export interface ExperimentsExport {
  schema_version: string
  generated_at: string
  generated_epoch: number
  report_date: string
  starting_equity: number
  experiments: ExperimentData[]
  summary: {
    total_experiments: number
    with_trades: number
    total_open: number
    total_closed: number
    combined_pnl: number
    combined_equity: number
    combined_unrealized_pl: number
  }
  _meta?: { served_at: string; stale: boolean; stale_minutes: number }
}

export function useExperiments() {
  return useSWR<ExperimentsExport>('/api/experiments', fetcher<ExperimentsExport>, {
    refreshInterval: 120_000,
    dedupingInterval: 30_000,
    revalidateOnFocus: true,
  })
}

export function useConfig() {
  const { data, error, isLoading, mutate } = useSWR<Config>(
    '/api/config',
    fetcher<Config>,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    }
  )
  return { config: data, error, isLoading, mutate }
}
