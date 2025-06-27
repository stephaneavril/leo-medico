// middleware.ts
import { NextRequest, NextResponse } from 'next/server'

const decodeJWT = (token: string) => {
  try {
    const base64 = token.split('.')[1]                       // sólo payload
      .replace(/-/g, '+')
      .replace(/_/g, '/');                                   // base64url → base64

    const json = atob(base64);                               // Edge runtime: atob
    return JSON.parse(json) as {
      name: string; email: string; scenario: string
    }
  } catch {
    return null
  }
}

export function middleware(req: NextRequest) {
  if (req.nextUrl.pathname !== '/dashboard') return

  const urlToken = req.nextUrl.searchParams.get('auth')
  const c = req.cookies

  if (c.get('user_name')?.value) return      // ya teníamos cookies

  if (urlToken) {
    const data = decodeJWT(urlToken)
    if (data) {
      const res = NextResponse.redirect(new URL('/dashboard', req.url))
      const opts = { path: '/', maxAge: 60 * 60 * 24 * 30 }

      res.cookies.set('user_name',     data.name,     opts)
      res.cookies.set('user_email',    data.email,    opts)
      res.cookies.set('user_scenario', data.scenario, opts)
      res.cookies.set('user_token',    urlToken,      opts) // opcional
      return res
    }
  }

  // nada válido → volver al login (backend)
  return NextResponse.redirect('https://leo-backend-flask.onrender.com/')
}
