import { NextResponse } from 'next/server'
import { jwtVerify } from 'jose'
import crypto from 'crypto'

/**
 * Per-route auth helper (runs in Node.js runtime, NOT Edge).
 *
 * Reads the `session` cookie, verifies the JWT with the same key
 * derivation used in `api/auth/route.ts`.
 *
 * Returns `null` if auth passes, or a 401 NextResponse if it fails.
 * When `API_AUTH_TOKEN` is not set (single-user mode), returns `null`.
 */
export async function verifyAuth(request: Request): Promise<NextResponse | null> {
  const token = process.env.API_AUTH_TOKEN
  if (!token) {
    // Single-user mode — no auth required
    return null
  }

  try {
    const cookieHeader = request.headers.get('cookie') || ''
    const sessionMatch = cookieHeader.match(/(?:^|;\s*)session=([^;]+)/)
    const jwt = sessionMatch?.[1]

    if (!jwt) {
      return NextResponse.json({ error: 'Authentication required' }, { status: 401 })
    }

    // Derive the same key used in api/auth/route.ts for JWT signing
    const secret = crypto.createHmac('sha256', 'attix-jwt-signing-v1').update(token).digest()
    await jwtVerify(jwt, secret)

    return null
  } catch {
    return NextResponse.json({ error: 'Invalid or expired session' }, { status: 401 })
  }
}
