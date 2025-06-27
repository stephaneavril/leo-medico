import { NextRequest, NextResponse } from 'next/server'
import jwt from 'jsonwebtoken'

const JWT_SECRET = process.env.JWT_SECRET!

export function middleware(req: NextRequest) {
  if (!req.nextUrl.pathname.startsWith('/dashboard')) return

  const token = req.nextUrl.searchParams.get('auth')
  if (!token) return

  try {
    const payload: any = jwt.verify(token, JWT_SECRET)

    const res = NextResponse.redirect(new URL('/dashboard', req.url))
    const maxAge = 60 * 60 * 24 * 30

    res.cookies.set('user_name',     payload.name,     { maxAge, sameSite: 'lax' })
    res.cookies.set('user_email',    payload.email,    { maxAge, sameSite: 'lax' })
    res.cookies.set('user_token',    token,            { maxAge, sameSite: 'lax' })
    res.cookies.set('user_scenario', payload.scenario, { maxAge, sameSite: 'lax' })

    return res
  } catch {
    return NextResponse.redirect(new URL('/', req.url))
  }
}

export const config = {
  matcher: ['/dashboard/:path*', '/interactive-session/:path*'],
}
