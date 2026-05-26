import { NextRequest, NextResponse } from 'next/server'
import { SignJWT } from 'jose'
import crypto from 'crypto'

function timingSafeCompare(a: string, b: string): boolean {
  const bufA = crypto.createHash('sha256').update(a).digest()
  const bufB = crypto.createHash('sha256').update(b).digest()
  return crypto.timingSafeEqual(bufA, bufB)
}

function getJwtSecret(): Uint8Array {
  const token = process.env.API_AUTH_TOKEN
  if (!token) throw new Error('API_AUTH_TOKEN not configured')
  // Derive a separate key for JWT signing so the raw auth token isn't used as HMAC key
  return crypto.createHmac('sha256', 'attix-jwt-signing-v1').update(token).digest()
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { token } = body

    if (!token || typeof token !== 'string') {
      return NextResponse.json({ error: 'Token required' }, { status: 400 })
    }

    const expectedToken = process.env.API_AUTH_TOKEN
    if (!expectedToken) {
      // No auth configured — single-user mode, auto-grant session
      const response = NextResponse.json({ success: true })
      return response
    }

    if (!timingSafeCompare(token, expectedToken)) {
      return NextResponse.json({ error: 'Invalid token' }, { status: 401 })
    }

    // Derive a stable userId from the token
    const userId = 'user_' + crypto.createHash('sha256').update(token).digest('hex').substring(0, 12)

    // Create JWT with 1-day expiry
    const jwt = await new SignJWT({ userId })
      .setProtectedHeader({ alg: 'HS256' })
      .setIssuedAt()
      .setExpirationTime('1d')
      .sign(getJwtSecret())

    const response = NextResponse.json({ success: true })
    response.cookies.set('session', jwt, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 86400, // 1 day
      path: '/',
    })

    return response
  } catch (error) {
    return NextResponse.json({ error: 'Authentication failed' }, { status: 500 })
  }
}
