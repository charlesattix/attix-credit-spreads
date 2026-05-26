'use client'

import { useState, useEffect } from 'react'
import { apiFetch } from '@/lib/api'
import type { Config } from '@/lib/types'
import { Save } from 'lucide-react'
import { toast } from 'sonner'
import { logger } from '@/lib/logger'
import { useConfig } from '@/lib/hooks'

export default function SettingsPage() {
  const { config: fetchedConfig, error, isLoading, mutate } = useConfig()
  const [config, setConfig] = useState<Config | null>(null)
  const [saving, setSaving] = useState(false)

  // Sync SWR data into local form state when it loads or refreshes
  useEffect(() => {
    if (fetchedConfig) {
      setConfig(fetchedConfig)
    }
  }, [fetchedConfig])

  // Show error toast only once when error state changes
  useEffect(() => {
    if (error) {
      toast.error('Failed to load configuration')
    }
  }, [error])

  const saveConfig = async () => {
    if (!config) return

    setSaving(true)
    try {
      await apiFetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      toast.success('Configuration saved successfully!')
      mutate()
    } catch (error) {
      logger.error('Failed to save config', { error: String(error) })
      toast.error('Failed to save configuration')
    } finally {
      setSaving(false)
    }
  }

  const updateConfig = (path: string[], value: unknown) => {
    setConfig(prev => {
      if (!prev) return prev
      const newConfig = JSON.parse(JSON.stringify(prev)); // deep clone
      let current: Record<string, unknown> = newConfig;
      for (let i = 0; i < path.length - 1; i++) {
        current = current[path[i]] as Record<string, unknown>;
      }
      current[path[path.length - 1]] = value;
      return newConfig;
    });
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-200px)]">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-brand-purple border-t-transparent"></div>
      </div>
    )
  }

  if (!config) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="bg-white rounded-lg border border-border p-12 text-center">
          <p className="text-muted-foreground">Failed to load configuration</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold mb-2">Learn & Settings</h1>
          <p className="text-muted-foreground">Configure your trading system</p>
        </div>
        <button
          onClick={saveConfig}
          disabled={saving}
          className="flex items-center gap-2 px-6 py-3 bg-gradient-brand text-white font-medium rounded-lg hover:opacity-90 transition-opacity"
        >
          <Save className="w-5 h-5" />
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      <div className="space-y-6">
        <div className="bg-white rounded-lg border border-border p-6">
          <h2 className="text-xl font-semibold mb-4">Strategy Parameters</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="min-dte" className="block text-sm font-medium mb-2">Min DTE</label>
              <input
                id="min-dte"
                type="number"
                min={1}
                max={365}
                value={config.strategy.min_dte}
                onChange={(e) => updateConfig(['strategy', 'min_dte'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
            <div>
              <label htmlFor="max-dte" className="block text-sm font-medium mb-2">Max DTE</label>
              <input
                id="max-dte"
                type="number"
                min={1}
                max={365}
                value={config.strategy.max_dte}
                onChange={(e) => updateConfig(['strategy', 'max_dte'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
            <div>
              <label htmlFor="min-delta" className="block text-sm font-medium mb-2">Min Delta</label>
              <input
                id="min-delta"
                type="number"
                step="0.01"
                min={0}
                max={1}
                value={config.strategy.min_delta}
                onChange={(e) => updateConfig(['strategy', 'min_delta'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
            <div>
              <label htmlFor="max-delta" className="block text-sm font-medium mb-2">Max Delta</label>
              <input
                id="max-delta"
                type="number"
                step="0.01"
                min={0}
                max={1}
                value={config.strategy.max_delta}
                onChange={(e) => updateConfig(['strategy', 'max_delta'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
          </div>
        </div>

        <div className="bg-white rounded-lg border border-border p-6">
          <h2 className="text-xl font-semibold mb-4">Risk Management</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="account-size" className="block text-sm font-medium mb-2">Account Size ($)</label>
              <input
                id="account-size"
                type="number"
                min={1}
                value={config.risk.account_size}
                onChange={(e) => updateConfig(['risk', 'account_size'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
            <div>
              <label htmlFor="max-risk-per-trade" className="block text-sm font-medium mb-2">Max Risk Per Trade (%)</label>
              <input
                id="max-risk-per-trade"
                type="number"
                step="0.1"
                min={0}
                max={100}
                value={config.risk.max_risk_per_trade}
                onChange={(e) => updateConfig(['risk', 'max_risk_per_trade'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
            <div>
              <label htmlFor="profit-target" className="block text-sm font-medium mb-2">Profit Target (%)</label>
              <input
                id="profit-target"
                type="number"
                min={0}
                max={100}
                value={config.risk.profit_target}
                onChange={(e) => updateConfig(['risk', 'profit_target'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
            <div>
              <label htmlFor="max-positions" className="block text-sm font-medium mb-2">Max Positions</label>
              <input
                id="max-positions"
                type="number"
                min={1}
                max={100}
                value={config.risk.max_positions}
                onChange={(e) => updateConfig(['risk', 'max_positions'], Number(e.target.value))}
                className="w-full px-4 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-purple"
              />
            </div>
          </div>
        </div>

        <div className="bg-gradient-brand rounded-lg p-6 text-white">
          <h2 className="text-xl font-semibold mb-2">📚 Learning Resources</h2>
          <p className="mb-4 opacity-90">
            Unlock premium educational content, video tutorials, and live trading sessions.
          </p>
          <a href="https://attix.com" target="_blank" rel="noopener noreferrer" className="inline-block px-6 py-2 bg-white text-brand-purple font-medium rounded-lg hover:bg-white/90 transition-colors">
            Learn More at Attix →
          </a>
        </div>
      </div>
    </div>
  )
}
