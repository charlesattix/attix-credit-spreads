'use client'

import { Zap } from 'lucide-react'

export function UpsellCard() {
  return (
    <div className="bg-gradient-brand rounded-lg p-6 text-white">
      <div className="flex items-center gap-2 mb-2">
        <Zap className="w-5 h-5" />
        <h3 className="font-bold">Unlock Premium</h3>
      </div>
      <p className="text-sm opacity-90 mb-4">
        Get unlimited alerts, advanced AI analysis, and priority support.
      </p>
      <a href="https://attix.com" target="_blank" rel="noopener noreferrer" className="block w-full px-4 py-2 bg-white text-brand-purple font-medium rounded-lg hover:bg-white/90 transition-colors text-center">
        Learn More →
      </a>
    </div>
  )
}
