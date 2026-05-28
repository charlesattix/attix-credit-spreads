/**
 * GET /api/experiments
 *
 * Proxies live data from the Attix production API and maps it to the
 * ExperimentsExport shape consumed by the paper-trading dashboard.
 *
 * Sources:
 *   - /api/v1/summary     → portfolio totals + per-experiment summary stats
 *   - /api/v1/experiments → full experiment list with live Alpaca equity data
 */
import { NextResponse } from 'next/server'

const ATTIX_BASE    = 'https://attix-production.up.railway.app/api/v1'
const ATTIX_API_KEY = process.env.ATTIX_API_KEY ?? 'dev-attix-2026'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

async function attixGet(path: string) {
  const res = await fetch(`${ATTIX_BASE}${path}`, {
    headers: { 'X-API-Key': ATTIX_API_KEY },
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`Attix ${path} → HTTP ${res.status}`)
  return res.json()
}

export async function GET() {
  // ── Fetch both endpoints in parallel ───────────────────────────────────
  let summary: Record<string, unknown>, experimentsPayload: Record<string, unknown>
  try {
    ;[summary, experimentsPayload] = await Promise.all([
      attixGet('/summary'),
      attixGet('/experiments'),
    ])
  } catch (err) {
    console.error('[experiments] Attix fetch failed:', err)
    return NextResponse.json(
      { error: 'Failed to fetch live data from Attix', detail: String(err) },
      { status: 502 }
    )
  }

  // ── Index summary detail by experiment id ──────────────────────────────
  const summaryById: Record<string, Record<string, unknown>> = {}
  for (const d of (summary.experiments_detail as Record<string, unknown>[]) ?? []) {
    summaryById[d.id as string] = d
  }

  // ── Map active experiments only ────────────────────────────────────────
  const allExps = (experimentsPayload.experiments as Record<string, unknown>[]) ?? []
  const activeExps = allExps.filter(e => e.status === 'active')

  const experiments = activeExps.map(e => {
    const sd = summaryById[e.id as string] ?? {}

    const totalClosed = (sd.total_closed as number) ?? 0
    const winRate     = (sd.win_rate as number) ?? 0
    const wins        = Math.round((winRate / 100) * totalClosed)
    const losses      = totalClosed - wins
    const totalPnl    = (sd.total_pnl as number) ?? 0

    const be = e.backtest_expectations as Record<string, unknown> | null | undefined

    // Build AlpacaAccount from live_ fields (day_pl and positions not available
    // from this endpoint — they show as null / [] until a richer endpoint exists)
    const hasLiveData = e.live_equity != null
    const alpaca = hasLiveData ? {
      equity:          e.live_equity as number,
      last_equity:     null,
      unrealized_pl:   (e.live_unrealized_pl as number | null) ?? null,
      portfolio_value: e.live_equity as number,
      cash:            (e.live_cash as number | null) ?? null,
      buying_power:    null,
      day_pl:          null,
      positions:       [] as unknown[],
      error:           (sd.error as string | null) ?? null,
      fetched_at:      (e.alpaca_fetched_at as string) ?? new Date().toISOString(),
    } : null

    return {
      id:         e.id as string,
      name:       (e.name as string) ?? (e.id as string),
      ticker:     (e.ticker as string) ?? 'SPY',
      creator:    (e.created_by as string) ?? '',
      live_since: (e.live_since as string) ?? (e.created_date as string) ?? '',
      account_id: (e.alpaca_account_id as string) ?? (e.account_id as string) ?? '',
      notes:      (e.notes as string) ?? '',
      backtest: {
        avg_return: be?.avg_return as number | undefined,
        max_dd:     be?.max_dd    as number | undefined,
        robust:     be?.robust    as number | undefined,
      },
      error: (sd.error as string | null) ?? null,
      alpaca,
      stats: {
        total_closed:     totalClosed,
        wins,
        losses,
        win_rate:         winRate,
        total_pnl:        totalPnl,
        total_return_pct: totalClosed > 0 ? (totalPnl / 100_000) * 100 : 0,
        max_dd_pct:       (sd.max_dd as number) ?? 0,
        max_dd_dollars:   0,
        open_count:       (sd.open_count as number) ?? 0,
        avg_pnl:          totalClosed > 0 ? totalPnl / totalClosed : 0,
        trades_week:      0,
        last_trade_date:  null,
        profit_factor:    null,
      },
      equity_curve:   [],
      open_positions: [],
      recent_trades:  [],
    }
  })

  // ── Build ExperimentsExport payload ────────────────────────────────────
  const generatedAt = (summary.generated_at as string) ?? new Date().toISOString()

  const payload = {
    schema_version:  (experimentsPayload.schema_version as string) ?? '3.0',
    generated_at:    generatedAt,
    generated_epoch: Math.floor(new Date(generatedAt).getTime() / 1000),
    report_date:     generatedAt.slice(0, 10),
    starting_equity: 100_000,
    experiments,
    summary: {
      total_experiments:    experiments.length,
      with_trades:          experiments.filter(e => e.stats.total_closed > 0).length,
      total_open:           (summary.total_open as number) ?? 0,
      total_closed:         (summary.total_closed as number) ?? 0,
      combined_pnl:         (summary.total_pnl as number) ?? 0,
      combined_equity:      (summary.total_equity as number) ?? 0,
      combined_unrealized_pl: (summary.total_unrealized_pl as number) ?? 0,
    },
    _meta: {
      served_at:    new Date().toISOString(),
      stale:        false,
      stale_minutes: 0,
    },
  }

  const response = NextResponse.json(payload)
  response.headers.set('Cache-Control', 'public, max-age=60, must-revalidate')
  return response
}
