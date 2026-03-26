'use client'

import { RefreshCw, TrendingUp, DollarSign, Activity, AlertCircle, CheckCircle } from 'lucide-react'
import { useExperiments, ExperimentData } from '@/lib/hooks'

function fmt$(n: number | null | undefined, decimals = 0) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

function fmtPct(n: number | null | undefined, decimals = 1) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(decimals)}%`
}

function pnlColor(n: number | null | undefined) {
  if (n == null) return 'text-gray-500'
  return n >= 0 ? 'text-green-600' : 'text-red-500'
}

export default function PaperTradingPage() {
  const { data, isLoading, error, mutate } = useExperiments()

  if (isLoading || !data) return (
    <div className="min-h-screen bg-[#FAF9FB] flex items-center justify-center">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[#9B6DFF]" />
    </div>
  )

  if (error) return (
    <div className="min-h-screen bg-[#FAF9FB] flex items-center justify-center">
      <div className="text-center">
        <p className="text-red-500 font-medium">Failed to load experiments</p>
        <p className="text-gray-400 text-sm mt-1">Run sync_dashboard_data.py --push on the Mac</p>
        <button onClick={() => mutate()} className="mt-3 px-4 py-2 rounded-lg text-sm font-medium text-white bg-[#9B6DFF]">
          Retry
        </button>
      </div>
    </div>
  )

  const s = data.summary
  const starting = data.starting_equity ?? 100_000
  const combinedReturn = s.combined_equity
    ? ((s.combined_equity - starting * data.experiments.length) / (starting * data.experiments.length)) * 100
    : null
  const stale = data._meta?.stale

  return (
    <div className="min-h-screen bg-[#FAF9FB]">
      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-gray-900">Paper Trading — Live Experiments</h1>
            <p className="text-xs text-gray-400 mt-0.5">
              {data.experiments.length} experiments · as of {new Date(data.generated_at).toLocaleString()}
              {stale && <span className="ml-2 text-amber-500">(data may be stale)</span>}
            </p>
          </div>
          <button onClick={() => mutate()}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium text-white transition bg-gradient-brand">
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        </div>

        {/* Portfolio Summary Bar */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <SummaryCard
            icon={<DollarSign className="w-5 h-5" />}
            label="Combined Equity"
            value={s.combined_equity ? `$${s.combined_equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
            sub={combinedReturn != null ? fmtPct(combinedReturn) + ' total return' : undefined}
            color="#9B6DFF"
          />
          <SummaryCard
            icon={<TrendingUp className="w-5 h-5" />}
            label="Unrealized P&L"
            value={fmt$(s.combined_unrealized_pl)}
            color={s.combined_unrealized_pl >= 0 ? '#22c55e' : '#ef4444'}
          />
          <SummaryCard
            icon={<Activity className="w-5 h-5" />}
            label="Realized P&L"
            value={fmt$(s.combined_pnl)}
            color={s.combined_pnl >= 0 ? '#22c55e' : '#ef4444'}
          />
          <SummaryCard
            icon={<Activity className="w-5 h-5" />}
            label="Open Positions"
            value={`${s.total_open}`}
            sub={`${s.total_closed} closed trades`}
            color="#F59E42"
          />
        </div>

        {/* Experiment Cards */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {data.experiments.map(exp => (
            <ExperimentCard key={exp.id} exp={exp} starting={starting} />
          ))}
        </div>

      </main>
    </div>
  )
}

function SummaryCard({ icon, label, value, sub, color }: {
  icon: React.ReactNode; label: string; value: string; sub?: string; color: string
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-100 p-4">
      <div className="flex items-center gap-2 mb-1">
        <div style={{ color }}>{icon}</div>
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <p className="text-xl font-bold" style={{ color }}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  )
}

function ExperimentCard({ exp, starting }: { exp: ExperimentData; starting: number }) {
  const alp = exp.alpaca
  const st = exp.stats
  const hasAlpaca = alp && alp.equity != null
  const totalReturn = hasAlpaca ? ((alp.equity! - starting) / starting) * 100 : null

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
      {/* Card header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-base font-bold text-gray-900">{exp.id}</span>
            <span className="text-xs text-gray-400 font-medium">{exp.ticker}</span>
            {exp.error
              ? <AlertCircle className="w-4 h-4 text-amber-400" />
              : <CheckCircle className="w-4 h-4 text-green-400" />
            }
          </div>
          {exp.name && exp.name !== exp.id && (
            <p className="text-xs text-gray-400 mt-0.5">{exp.name}</p>
          )}
          <p className="text-xs text-gray-400 mt-0.5">Live since {exp.live_since}</p>
        </div>
        {hasAlpaca && (
          <div className="text-right">
            <p className="text-2xl font-bold text-gray-900">
              ${alp.equity!.toLocaleString('en-US', { maximumFractionDigits: 0 })}
            </p>
            <p className={`text-xs font-semibold ${pnlColor(totalReturn)}`}>
              {fmtPct(totalReturn)} since inception
            </p>
          </div>
        )}
      </div>

      {/* Live Alpaca stats */}
      {hasAlpaca ? (
        <div className="grid grid-cols-3 gap-3 mb-4 p-3 bg-gray-50 rounded-lg">
          <AlpacaMetric
            label="Unrealized P&L"
            value={fmt$(alp.unrealized_pl)}
            valueColor={pnlColor(alp.unrealized_pl)}
          />
          <AlpacaMetric
            label="Day P&L"
            value={fmt$(alp.day_pl)}
            valueColor={pnlColor(alp.day_pl)}
          />
          <AlpacaMetric
            label="Cash"
            value={alp.cash != null ? `$${alp.cash.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
            valueColor="text-gray-700"
          />
        </div>
      ) : (
        <div className="mb-4 p-3 bg-gray-50 rounded-lg text-xs text-gray-400">
          {alp?.error ?? 'No Alpaca data'}
        </div>
      )}

      {/* Realized stats from SQLite */}
      <div className="grid grid-cols-4 gap-2 text-center mb-4">
        <div>
          <p className="text-xs text-gray-400">Realized P&L</p>
          <p className={`text-sm font-bold ${pnlColor(st.total_pnl)}`}>{fmt$(st.total_pnl)}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400">Win Rate</p>
          <p className="text-sm font-bold text-gray-700">
            {st.total_closed > 0 ? `${st.win_rate.toFixed(0)}%` : '—'}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400">Open</p>
          <p className="text-sm font-bold text-gray-700">{st.open_count}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400">Closed</p>
          <p className="text-sm font-bold text-gray-700">{st.total_closed}</p>
        </div>
      </div>

      {/* Alpaca open positions */}
      {hasAlpaca && alp.positions.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">
            Alpaca Positions ({alp.positions.length})
          </p>
          <div className="space-y-1.5">
            {alp.positions.map((pos, i) => (
              <div key={i} className="flex items-center justify-between text-xs bg-gray-50 rounded px-3 py-1.5">
                <span className="font-mono font-medium text-gray-700">{pos.symbol}</span>
                <span className="text-gray-500">qty {pos.qty > 0 ? '+' : ''}{pos.qty}</span>
                <span className="text-gray-600">${pos.market_value.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
                <span className={`font-semibold ${pnlColor(pos.unrealized_pl)}`}>
                  {fmt$(pos.unrealized_pl)} ({fmtPct(pos.unrealized_plpc)})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {exp.error && (
        <p className="text-xs text-amber-500 mt-2">{exp.error}</p>
      )}
    </div>
  )
}

function AlpacaMetric({ label, value, valueColor }: { label: string; value: string; valueColor: string }) {
  return (
    <div className="text-center">
      <p className="text-xs text-gray-400">{label}</p>
      <p className={`text-sm font-bold ${valueColor}`}>{value}</p>
    </div>
  )
}
