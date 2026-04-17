import { NextResponse } from 'next/server'
import { verifyAuth } from '@/lib/auth'
import { logger } from '@/lib/logger'
import fs from 'fs'
import path from 'path'

export const dynamic = 'force-dynamic'

// Sentinel data is pushed from the Mac via sync_sentinel_data.py
// and written to data/sentinel_dashboard.json by the FastAPI endpoint.
// This route reads that file and serves it to the frontend.

function getSentinelDataPath(): string {
  // In Railway deployment: data/ is at project root
  // Locally: same structure
  const candidates = [
    path.join(process.cwd(), 'data', 'sentinel_dashboard.json'),
    path.join(process.cwd(), '..', 'data', 'sentinel_dashboard.json'),
  ]
  for (const p of candidates) {
    if (fs.existsSync(p)) return p
  }
  return candidates[0] // default path even if not found
}

export async function GET(request: Request) {
  const authErr = await verifyAuth(request)
  if (authErr) return authErr

  try {
    const dataPath = getSentinelDataPath()
    if (!fs.existsSync(dataPath)) {
      return NextResponse.json({
        error: 'No sentinel data available. Run sync_sentinel_data.py --push to populate.',
        experiments: {},
        alerts: [],
        config_integrity: [],
      })
    }

    const raw = fs.readFileSync(dataPath, 'utf-8')
    const data = JSON.parse(raw)
    return NextResponse.json(data)
  } catch (error) {
    logger.error('Failed to read sentinel data', { error: String(error) })
    return NextResponse.json(
      { error: 'Failed to read sentinel data', experiments: {}, alerts: [] },
      { status: 500 }
    )
  }
}
