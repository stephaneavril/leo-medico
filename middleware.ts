import { NextRequest, NextResponse } from 'next/server'

export function middleware(req: NextRequest) {
  const url = new URL(req.url)
  const token = url.searchParams.get('auth')
  if (token) {
    const res = NextResponse.redirect(new URL('/dashboard', url))
    res.cookies.set('jwt', token, {
      httpOnly: true,
      sameSite: 'lax',
      secure: true,
      maxAge: 60 * 60, // 1 h
      path: '/',
    })
    return res
  }
  return NextResponse.next()
}

export const config = {
  matcher: ['/dashboard', '/dashboard/:path*'],
}
