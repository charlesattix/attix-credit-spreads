/**
 * POST /api/admin/push-sentinel
 *
 * Receives a sentinel_dashboard.json payload from the local Mac sync script
 * (scripts/sync_sentinel_data.py --push) and writes it to the Railway volume.
 *
 * Auth: Bearer token matching API_AUTH_TOKEN (same key used by upload-db
 *       and push-experiments).
 * Size limit: 10 MB (sentinel payload is typically <100 KB).
 *
 * The written file is read back by /api/sentinel/route.ts to render the
 * dashboard. We stamp `pushed_at` (server clock) into the payload so the
 * frontend can compute push freshness without trusting the client.
 */
import { NextRequest, NextResponse } from 'next/server'
import { writeFile } from 'fs/promises'
import { timingSafeEqual } from 'crypto'
import path from 'path'
import { DATA_DIR } from '@/lib/paths'

const SENTINEL_PATH = path.join(DATA_DIR, 'sentinel_dashboard.json')
const MAX_BODY_BYTES = 10 * 1024 * 1024 // 10 MB

const REQUIRED_FIELDS = [
  'generated_at',
  'sentinel_version',
  'experiment_count',
  'experiments',
] as const

function timingSafeCompare(a: string, b: string): boolean {
  if (a.length !== b.length) {
    const buf = Buffer.from(a)
    timingSafeEqual(buf, buf) // burn constant time
    return false
  }
  return timingSafeEqual(Buffer.from(a), Buffer.from(b))
}

export async function POST(request: NextRequest) {
  // ── Auth ────────────────────────────────────────────────────────────────
  const expectedToken = process.env.API_AUTH_TOKEN || process.env.RAILWAY_ADMIN_TOKEN
  if (!expectedToken) {
    return NextResponse.json({ error: 'Admin endpoint not configured' }, { status: 500 })
  }

  const authHeader = request.headers.get('authorization') || ''
  const providedToken = authHeader.replace('Bearer ', '').trim()
  if (!providedToken || !timingSafeCompare(providedToken, expectedToken)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // ── Size guard ──────────────────────────────────────────────────────────
  const contentLength = parseInt(request.headers.get('content-length') || '0', 10)
  if (contentLength > MAX_BODY_BYTES) {
    return NextResponse.json({ error: 'Payload too large' }, { status: 413 })
  }

  // ── Parse + validate ────────────────────────────────────────────────────
  let payload: Record<string, unknown>
  try {
    payload = await request.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  for (const field of REQUIRED_FIELDS) {
    if (!(field in payload)) {
      return NextResponse.json(
        { error: `Missing required field: ${field}` },
        { status: 400 }
      )
    }
  }

  if (typeof payload.experiments !== 'object' || payload.experiments === null) {
    return NextResponse.json(
      { error: 'experiments must be an object' },
      { status: 400 }
    )
  }

  // ── Stamp server-side push timestamp ────────────────────────────────────
  const pushedAt = new Date().toISOString()
  const stamped = { ...payload, pushed_at: pushedAt }

  // ── Write to volume ─────────────────────────────────────────────────────
  try {
    const json = JSON.stringify(stamped, null, 2)
    await writeFile(SENTINEL_PATH, json, 'utf-8')

    const expCount = payload.experiment_count
    console.log(
      `[push-sentinel] Wrote ${json.length} bytes — ` +
      `${expCount} experiments, ` +
      `generated_at=${payload.generated_at}, ` +
      `pushed_at=${pushedAt}`
    )

    return NextResponse.json({
      success: true,
      pushed_at: pushedAt,
      experiments: expCount,
      bytes_written: json.length,
    })
  } catch (err) {
    console.error('[push-sentinel] Write failed:', err)
    return NextResponse.json({ error: 'Failed to write sentinel file' }, { status: 500 })
  }
}

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'
