'use client'

import { useSentinel } from '@/lib/hooks'
import type { SentinelExperiment } from '@/lib/hooks'
import { formatCurrency } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(isoStr: string | undefined): string {
  if (!isoStr) return 'never'
  const diff = Date.now() - new Date(isoStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function formatMinutes(mins: number): string {
  if (mins < 60) return `${Math.round(mins)}m`
  const hrs = mins / 60
  if (hrs < 24) return `${hrs.toFixed(1)}h`
  return `${(hrs / 24).toFixed(1)}d`
}

type Severity = 'pass' | 'ok' | 'warning' | 'critical' | 'halt' | 'watch' | 'error' | 'not_enrolled' | 'drift' | 'resolved' | 'info' | 'fail'

function SeverityPill({ severity, label }: { severity: Severity; label?: string }) {
  const styles: Record<string, string> = {
    pass: 'bg-emerald-100 text-emerald-800',
    ok: 'bg-emerald-100 text-emerald-800',
    resolved: 'bg-gray-100 text-gray-600',
    info: 'bg-blue-100 text-blue-800',
    warning: 'bg-amber-100 text-amber-800',
    watch: 'bg-amber-100 text-amber-800',
    critical: 'bg-red-100 text-red-800',
    halt: 'bg-pink-100 text-pink-800',
    error: 'bg-red-100 text-red-800',
    not_enrolled: 'bg-gray-100 text-gray-500',
    drift: 'bg-red-100 text-red-800',
  }
  const cls = styles[severity] || styles.ok
  const text = label || severity.toUpperCase().replace('_', ' ')
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wide ${cls}`}>
      {text}
    </span>
  )
}

function SectionHeader({ title, what, why }: { title: string; what: string; why: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-lg font-bold text-gray-900 mb-1">{title}</h2>
      <p className="text-sm text-gray-500 leading-relaxed">
        <span className="font-semibold text-gray-700">What: </span>{what}<br />
        <span className="font-semibold text-gray-700">Why: </span>{why}
      </p>
    </div>
  )
}

function Divider() {
  return <hr className="border-t border-gray-200 my-8" />
}

function getOverallStatus(exp: SentinelExperiment): Severity {
  if (exp.status === 'halted') return 'halt'
  const gates = exp.gates
  if (gates.gate7_orphans && !gates.gate7_orphans.passed) return 'critical'
  if (gates.gate8_drift && !gates.gate8_drift.passed) {
    const hasHalt = gates.gate8_drift.alerts?.some(a => a.severity === 'halt')
    if (hasHalt) return 'halt'
    const hasCrit = gates.gate8_drift.alerts?.some(a => a.severity === 'critical')
    if (hasCrit) return 'critical'
    return 'warning'
  }
  if (gates.gate9_lifecycle?.stuck?.some(s => s.severity === 'critical')) return 'critical'
  if (gates.gate9_lifecycle?.stuck?.some(s => s.severity === 'warning')) return 'warning'
  return 'ok'
}

function getIssuesSummary(expId: string, exp: SentinelExperiment): string {
  const issues: string[] = []
  const g = exp.gates
  if (g.gate7_orphans && (g.gate7_orphans.orphans > 0 || g.gate7_orphans.ghosts > 0)) {
    const parts: string[] = []
    if (g.gate7_orphans.orphans > 0) parts.push(`${g.gate7_orphans.orphans} orphan${g.gate7_orphans.orphans > 1 ? 's' : ''}`)
    if (g.gate7_orphans.ghosts > 0) parts.push(`${g.gate7_orphans.ghosts} ghost${g.gate7_orphans.ghosts > 1 ? 's' : ''}`)
    issues.push(parts.join(' + '))
  }
  if (g.gate9_lifecycle?.stuck?.length) {
    issues.push(`${g.gate9_lifecycle.stuck.length} stuck`)
  }
  if (g.gate8_drift?.alerts?.length) {
    const metrics = g.gate8_drift.alerts.map(a => a.metric === 'win_rate' ? 'WR drifting' : a.metric)
    issues.push(...new Set(metrics))
  }
  return issues.length > 0 ? issues.join(' \u00b7 ') : '\u2014'
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function SentinelPage() {
  const { data, isLoading, error } = useSentinel()

  if (isLoading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12 text-center">
        <div className="animate-spin rounded-full h-10 w-10 border-4 border-brand-purple border-t-transparent mx-auto mb-4" />
        <p className="text-muted-foreground text-sm">Loading Sentinel data...</p>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12">
        <div className="bg-white rounded-lg border border-border p-8 text-center">
          <div className="text-3xl mb-3">&#x1f6e1;&#xfe0f;</div>
          <h2 className="text-lg font-bold mb-2">No Sentinel Data</h2>
          <p className="text-muted-foreground text-sm">
            Run <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">python scripts/sync_sentinel_data.py --push</code> to populate.
          </p>
        </div>
      </div>
    )
  }

  if (data.error && !data.experiments) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
          <p className="text-red-800 font-medium">{data.error}</p>
        </div>
      </div>
    )
  }

  const experiments = data.experiments || {}
  const expIds = Object.keys(experiments).sort()
  const alerts = data.alerts || []
  const configIntegrity = data.config_integrity || []

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-8 pb-4 border-b-2 border-gray-900">
        <h1 className="text-2xl font-extrabold text-gray-900">SENTINEL</h1>
        <p className="text-sm text-gray-400">
          Last sync: {timeAgo(data.pushed_at || data.generated_at)} &middot; {expIds.length} experiment{expIds.length !== 1 ? 's' : ''} active
        </p>
      </div>

      {/* Section 1: Overview */}
      <section className="mb-8">
        <SectionHeader
          title="Overview"
          what="The health of the entire system in one row per experiment. If something is wrong, you see it here first."
          why="Single glance to know if any experiment needs attention."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-gray-200">
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Experiment</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Win Rate</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">vs Backtest</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">P&L</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Issues</th>
              </tr>
            </thead>
            <tbody>
              {expIds.map(id => {
                const exp = experiments[id]
                const m = exp.metrics
                const bl = exp.baseline
                const status = getOverallStatus(exp)
                const wr = m?.win_rate
                const blWr = bl?.win_rate
                const delta = wr != null && blWr != null ? wr - blWr : null
                const pnl = m?.total_pnl ?? 0

                return (
                  <tr key={id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-2 font-bold text-gray-900">{id}</td>
                    <td className="py-2 px-2"><SeverityPill severity={status} /></td>
                    <td className="py-2 px-2">
                      {wr != null ? `${wr}%` : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2">
                      {delta != null ? (
                        <span className={delta >= 0 ? 'text-emerald-600 font-semibold' : delta <= -10 ? 'text-red-600 font-semibold' : 'text-gray-500'}>
                          {delta >= 0 ? '+' : ''}{delta.toFixed(0)} pts
                        </span>
                      ) : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2">
                      <span className={pnl >= 0 ? 'text-emerald-600 font-semibold' : 'text-red-600 font-semibold'}>
                        {pnl !== 0 ? formatCurrency(pnl) : <span className="text-gray-300">$0</span>}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-gray-500 text-xs">{getIssuesSummary(id, exp)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      <Divider />

      {/* Section 2: Config Integrity */}
      <section className="mb-8">
        <SectionHeader
          title="Config Integrity"
          what="Before any experiment trades, we verify config files haven't changed since certification. SHA-256 fingerprints must match."
          why="Bad config = bad trades. These checks catch typos, unauthorized changes, and config drift before they touch real money."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-gray-200">
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Check</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Detail</th>
              </tr>
            </thead>
            <tbody>
              {configIntegrity.map((ci, i) => {
                const check = ('check' in ci ? ci.check : null) || ('exp_id' in ci ? ci.exp_id : null) || `Check ${i + 1}`
                const detail = 'detail' in ci ? ci.detail : ('paper_config' in ci ? ci.paper_config : null)
                const status = ci.status as Severity
                return (
                  <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-2 font-medium text-gray-900">{check}</td>
                    <td className="py-2 px-2">
                      <SeverityPill severity={status === 'fail' ? 'critical' : status} label={status === 'pass' ? 'PASS' : status === 'fail' ? 'FAIL' : status.toUpperCase()} />
                    </td>
                    <td className="py-2 px-2 text-gray-500 text-xs">{detail || '\u2014'}</td>
                  </tr>
                )
              })}
              {configIntegrity.length === 0 && (
                <tr><td colSpan={3} className="py-4 text-center text-gray-400">No config data available</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <Divider />

      {/* Section 3: Trade Sizing (Gate 6) */}
      <section className="mb-8">
        <SectionHeader
          title="Trade Sizing"
          what="Compares the number of contracts actually placed vs what the backtest formula says we should place, given current account equity."
          why="Over-sizing means more risk than the backtest assumed. Under-sizing means leaving returns on the table. Either way, live results won't match expectations."
        />
        {(() => {
          const sizingRows = expIds
            .map(id => ({ id, gate: experiments[id].gates.gate6_sizing }))
            .filter(r => r.gate && r.gate.deviations && r.gate.deviations.length > 0)

          if (sizingRows.length === 0) {
            return (
              <div className="text-center py-6 text-gray-400 text-sm">
                Gate 6 data collection not yet implemented. Sizing deviations will appear here once <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">sync_sentinel_data.py</code> includes Gate 6 results.
              </div>
            )
          }

          return (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b-2 border-gray-200">
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Experiment</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Actual</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Expected</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Deviation</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sizingRows.map(({ id, gate }) =>
                    gate!.deviations!.map((d, i) => (
                      <tr key={`${id}-${i}`} className="border-b border-gray-100 hover:bg-gray-50">
                        <td className="py-2 px-2 font-bold text-gray-900">{id}</td>
                        <td className="py-2 px-2">{d.actual} contracts</td>
                        <td className="py-2 px-2">{d.expected}</td>
                        <td className="py-2 px-2">
                          <span className={d.deviation_pct > 0.35 ? 'text-red-600 font-semibold' : d.deviation_pct > 0.15 ? 'text-amber-600 font-semibold' : 'text-gray-500'}>
                            {d.deviation_pct > 0 ? '+' : ''}{(d.deviation_pct * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td className="py-2 px-2"><SeverityPill severity={d.severity as Severity} /></td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )
        })()}
      </section>

      <Divider />

      {/* Section 4: Orphan & Ghost Positions (Gate 7) */}
      <section className="mb-8">
        <SectionHeader
          title="Orphan & Ghost Positions"
          what="Compares positions at the broker (Alpaca) vs positions in our database. Orphans exist at the broker but we don't know about them. Ghosts exist in our DB but the broker closed them."
          why="Orphan positions are invisible &mdash; no stop-loss, no profit-target, no management. They accumulate risk silently."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-gray-200">
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Experiment</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Orphans</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Ghosts</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Unresolved Scans</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Status</th>
              </tr>
            </thead>
            <tbody>
              {expIds.map(id => {
                const g7 = experiments[id].gates.gate7_orphans
                if (!g7) return (
                  <tr key={id} className="border-b border-gray-100">
                    <td className="py-2 px-2 font-bold text-gray-900">{id}</td>
                    <td colSpan={4} className="py-2 px-2 text-gray-400 text-xs">No data</td>
                  </tr>
                )
                const severity: Severity = !g7.passed
                  ? (g7.orphans >= 5 ? 'halt' : g7.consecutive_scans >= 3 ? 'critical' : 'warning')
                  : 'ok'
                return (
                  <tr key={id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-2 font-bold text-gray-900">{id}</td>
                    <td className={`py-2 px-2 ${g7.orphans > 0 ? 'text-red-600 font-semibold' : ''}`}>{g7.orphans}</td>
                    <td className={`py-2 px-2 ${g7.ghosts > 0 ? 'text-red-600 font-semibold' : ''}`}>{g7.ghosts}</td>
                    <td className="py-2 px-2">
                      {g7.consecutive_scans > 0 ? (
                        <span className="text-red-600 font-semibold">{g7.consecutive_scans} scans</span>
                      ) : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2"><SeverityPill severity={severity} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      <Divider />

      {/* Section 5: Performance vs Backtest (Gate 8) */}
      <section className="mb-8">
        <SectionHeader
          title="Performance vs Backtest"
          what="Tracks rolling win rate, average loss per trade, and drawdown &mdash; then compares them to what the backtest predicted."
          why="A strategy can silently degrade. This section catches drift early and halts the experiment before losses compound."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-gray-200">
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Experiment</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Live WR</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Backtest WR</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Drift</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Avg Loss</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Trades</th>
                <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Status</th>
              </tr>
            </thead>
            <tbody>
              {expIds.map(id => {
                const exp = experiments[id]
                const m = exp.metrics
                const bl = exp.baseline
                const g8 = exp.gates.gate8_drift
                const liveWr = m?.win_rate
                const blWr = bl?.win_rate
                const delta = liveWr != null && blWr != null ? liveWr - blWr : null
                const avgLoss = m?.avg_loss
                const window = m?.window_size || m?.total_trades || 0

                let severity: Severity = 'ok'
                if (g8 && !g8.passed) {
                  const maxSev = g8.alerts?.reduce((acc, a) =>
                    a.severity === 'halt' ? 'halt' : a.severity === 'critical' && acc !== 'halt' ? 'critical' : acc, 'warning' as string
                  )
                  severity = (maxSev as Severity) || 'warning'
                }

                return (
                  <tr key={id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-2 font-bold text-gray-900">{id}</td>
                    <td className="py-2 px-2">
                      {liveWr != null ? `${liveWr}%` : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2">
                      {blWr != null ? `${blWr}%` : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2">
                      {delta != null ? (
                        <span className={delta >= 0 ? 'text-emerald-600 font-semibold' : delta <= -15 ? 'text-red-600 font-semibold' : delta <= -10 ? 'text-amber-600 font-semibold' : 'text-gray-500'}>
                          {delta >= 0 ? '+' : ''}{delta.toFixed(0)} pts
                        </span>
                      ) : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2">
                      {avgLoss != null ? (
                        <span className={avgLoss > (bl?.avg_loss || Infinity) * 1.5 ? 'text-red-600 font-semibold' : ''}>
                          {formatCurrency(avgLoss)}
                        </span>
                      ) : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2 text-gray-500">
                      {window > 0 ? (
                        <span>{window}{window < 20 ? <span className="text-amber-500 ml-0.5" title="Low confidence: fewer than 20 trades">*</span> : ''}</span>
                      ) : <span className="text-gray-300">&mdash;</span>}
                    </td>
                    <td className="py-2 px-2"><SeverityPill severity={severity} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      <Divider />

      {/* Section 6: Position Lifecycle (Gate 9) */}
      <section className="mb-8">
        <SectionHeader
          title="Position Lifecycle"
          what="Monitors how long each position has been in its current state &mdash; open, pending close, needs investigation."
          why="A position stuck in &ldquo;pending_close&rdquo; means an order failed silently. The longer it sits, the more unmanaged risk."
        />
        {(() => {
          const allStuck = expIds.flatMap(id => {
            const g9 = experiments[id].gates.gate9_lifecycle
            return (g9?.stuck || []).map(s => ({ ...s, exp_id: id }))
          })

          if (allStuck.length === 0) {
            return (
              <div className="text-center py-6 text-gray-400 text-sm">
                No stuck positions across all experiments.
                <div className="mt-2 text-xs">
                  {expIds.map(id => {
                    const g9 = experiments[id].gates.gate9_lifecycle
                    return g9 ? (
                      <span key={id} className="mr-3">
                        {id}: {g9.total_open} open, {g9.total_pending} pending
                      </span>
                    ) : null
                  })}
                </div>
              </div>
            )
          }

          return (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b-2 border-gray-200">
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Experiment</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Trade</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">State</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Time Stuck</th>
                    <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {allStuck.map((s, i) => (
                    <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-2 px-2 font-bold text-gray-900">{s.exp_id}</td>
                      <td className="py-2 px-2 font-mono text-xs text-gray-600">{s.trade_id}</td>
                      <td className="py-2 px-2 text-gray-700">{s.status}</td>
                      <td className={`py-2 px-2 font-semibold ${s.severity === 'critical' ? 'text-red-600' : 'text-amber-600'}`}>
                        {formatMinutes(s.minutes)}
                      </td>
                      <td className="py-2 px-2"><SeverityPill severity={s.severity as Severity} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        })()}
      </section>

      <Divider />

      {/* Section 7: Recent Alerts */}
      <section className="mb-8">
        <SectionHeader
          title="Recent Alerts"
          what="A chronological feed of every alert Sentinel has fired, with severity and resolution status."
          why="Single place to see what happened, when, and whether it's been addressed. No digging through logs."
        />
        {alerts.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-gray-200">
                  <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Time</th>
                  <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Severity</th>
                  <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Experiment</th>
                  <th className="text-left py-2 px-2 text-[11px] font-bold uppercase tracking-wider text-gray-400">Alert</th>
                </tr>
              </thead>
              <tbody>
                {alerts.slice(0, 20).map((a, i) => {
                  const alertDate = a.time ? new Date(a.time) : null
                  const isOld = alertDate ? (Date.now() - alertDate.getTime()) > 86_400_000 : false
                  const time = alertDate
                    ? isOld
                      ? alertDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + alertDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
                      : alertDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
                    : '??:??'
                  const sev = a.resolved ? 'resolved' : (a.severity as Severity)
                  return (
                    <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-2 px-2 font-mono text-xs text-gray-400">{time}</td>
                      <td className="py-2 px-2">
                        <SeverityPill severity={sev} label={a.resolved ? 'RESOLVED' : a.severity?.toUpperCase()} />
                      </td>
                      <td className="py-2 px-2 font-bold text-gray-900">{a.exp_id}</td>
                      <td className="py-2 px-2 text-gray-600 text-xs">{a.message}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-6 text-gray-400 text-sm">
            No alerts recorded yet. Alerts will appear here after running <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">run_sentinel.py --daily</code>.
          </div>
        )}
      </section>

      {/* Footer */}
      <div className="text-xs text-gray-300 mt-8 pt-4 border-t border-gray-100">
        SENTINEL v{data.sentinel_version || '2.0'} &middot; Generated {data.generated_at ? new Date(data.generated_at).toLocaleString() : 'unknown'}
      </div>
    </div>
  )
}
