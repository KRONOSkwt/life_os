import { SyncError } from './sync.ts'

async function session(method: 'POST' | 'DELETE', token?: string): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/v1/browser/session', {
      method, credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000),
      ...(method === 'POST' ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) } : {}),
    })
  } catch { throw new SyncError('connection', 'No se pudo contactar con Life OS. Inténtalo de nuevo.') }
  if (response.status === 401) throw new SyncError('credentials', 'La clave de acceso no es válida. Revísala e inténtalo de nuevo.')
  if (response.status !== 204) throw new SyncError('request', 'No se pudo completar el acceso. Inténtalo de nuevo.')
}

export const signIn = (token: string) => session('POST', token)
export const signOut = () => session('DELETE')
