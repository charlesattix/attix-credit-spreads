'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Menu, X } from 'lucide-react'
import { cn } from '@/lib/utils'

function useMarketOpen() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const etFormat = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric', minute: 'numeric', hour12: false,
      weekday: 'short',
    })
    const check = () => {
      const parts = etFormat.formatToParts(new Date())
      const weekday = parts.find(p => p.type === 'weekday')?.value ?? ''
      const h = parseInt(parts.find(p => p.type === 'hour')?.value ?? '0', 10)
      const m = parseInt(parts.find(p => p.type === 'minute')?.value ?? '0', 10)
      const mins = h * 60 + m
      const isWeekday = !['Sat', 'Sun'].includes(weekday)
      setOpen(isWeekday && mins >= 570 && mins < 960)
    }
    check()
    const id = setInterval(check, 60000)
    return () => clearInterval(id)
  }, [])
  return open
}

const navItems = [
  { name: "Today's Alerts", href: '/' },
  { name: 'My Trades', href: '/my-trades' },
  { name: 'Backtest', href: '/backtest' },
  { name: 'System Results', href: '/paper-trading' },
  { name: 'Sentinel', href: '/sentinel' },
  { name: 'Learn', href: '/settings' },
]

export function Navbar() {
  const pathname = usePathname()
  const marketOpen = useMarketOpen()
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <nav className="sticky top-0 z-50 bg-white border-b border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14 sm:h-16">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 sm:w-8 sm:h-8 bg-gradient-brand rounded-lg flex items-center justify-center rotate-45">
              <div className="w-3.5 h-3.5 sm:w-4 sm:h-4 bg-white rounded-sm"></div>
            </div>
            <span className="text-lg sm:text-xl font-bold bg-gradient-brand bg-clip-text text-transparent">
              Alerts by PilotAI
            </span>
          </Link>

          {/* Desktop Nav Links */}
          <div className="hidden md:flex items-center gap-6">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "text-sm font-medium transition-colors hover:text-brand-purple",
                  pathname === item.href ? "text-brand-purple" : "text-muted-foreground"
                )}
              >
                {item.name}
              </Link>
            ))}
          </div>

          {/* Right Side */}
          <div className="flex items-center gap-2 sm:gap-4">
            {/* Live Markets Indicator */}
            <div className={cn("hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full", marketOpen ? "bg-profit-light" : "bg-gray-100")}>
              <div className={cn("w-2 h-2 rounded-full", marketOpen ? "bg-profit animate-pulse-dot" : "bg-gray-400")}></div>
              <span className={cn("text-xs font-medium", marketOpen ? "text-profit" : "text-gray-500")}>{marketOpen ? 'Markets Open' : 'Markets Closed'}</span>
            </div>

            {/* Mobile: small market dot */}
            <div className={cn("flex sm:hidden items-center gap-1.5 px-2 py-1 rounded-full", marketOpen ? "bg-profit-light" : "bg-gray-100")}>
              <div className={cn("w-2 h-2 rounded-full", marketOpen ? "bg-profit animate-pulse-dot" : "bg-gray-400")}></div>
              <span className={cn("text-[10px] font-medium", marketOpen ? "text-profit" : "text-gray-500")}>{marketOpen ? 'Open' : 'Closed'}</span>
            </div>

            {/* CTA - hidden on smallest screens */}
            <a href="https://pilotai.com" target="_blank" rel="noopener noreferrer" className="hidden xs:inline-flex px-3 sm:px-4 py-1.5 sm:py-2 bg-gradient-brand text-white text-xs sm:text-sm font-medium rounded-lg hover:opacity-90 transition-opacity">
              Try PilotAI →
            </a>

            {/* Hamburger */}
            <button
              onClick={() => setMobileOpen(!mobileOpen)}
              className="md:hidden p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
            >
              {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile Nav Dropdown */}
      {mobileOpen && (
        <div className="md:hidden border-t border-border bg-white">
          <div className="px-4 py-3 space-y-1">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMobileOpen(false)}
                className={cn(
                  "block px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                  pathname === item.href
                    ? "bg-brand-purple/10 text-brand-purple"
                    : "text-muted-foreground hover:bg-gray-50"
                )}
              >
                {item.name}
              </Link>
            ))}
            <a
              href="https://pilotai.com"
              target="_blank"
              rel="noopener noreferrer"
              className="block px-3 py-2.5 mt-2 text-center bg-gradient-brand text-white text-sm font-medium rounded-lg xs:hidden"
            >
              Try PilotAI →
            </a>
          </div>
        </div>
      )}
    </nav>
  )
}
