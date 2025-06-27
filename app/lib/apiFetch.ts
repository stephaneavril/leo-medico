import { cookies } from 'next/headers'
import { BACKEND_API } from '@/app/lib/constants'   // si ya expones la URL aquí

export async function apiFetch (
  path: string,
  init: RequestInit = {},
) {
  // lee la cookie que el middleware guardó
  const jwt = cookies().get('jwt')?.value

  return fetch(`${BACKEND_API}${path}`, {
    ...init,
    headers: {
      ...(init.headers || {}),
      ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
    },
  })
}
