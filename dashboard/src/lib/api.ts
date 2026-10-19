// Cliente HTTP del dashboard. Agrega el token y traduce errores a mensajes en español.

const TOKEN_KEY = 'mesa-monitor.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // almacenamiento bloqueado: la sesión dura lo que dure la pestaña
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn)
  return () => unauthorizedListeners.delete(fn)
}

async function errorFrom(resp: Response): Promise<ApiError> {
  let detail = `Error ${resp.status}`
  try {
    const body = (await resp.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') detail = body.detail
    else if (Array.isArray(body.detail)) detail = 'Datos inválidos'
  } catch {
    // respuesta sin JSON
  }
  return new ApiError(resp.status, detail)
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  let resp: Response
  try {
    resp = await fetch(`/api/v1${path}`, { ...init, headers })
  } catch {
    throw new ApiError(0, 'No se pudo contactar al servidor')
  }
  if (resp.status === 401) {
    setToken(null)
    unauthorizedListeners.forEach((fn) => fn())
  }
  if (!resp.ok) throw await errorFrom(resp)
  return resp
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await apiFetch(path, init)
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

export async function apiBlobUrl(path: string): Promise<string> {
  const resp = await apiFetch(path)
  return URL.createObjectURL(await resp.blob())
}
