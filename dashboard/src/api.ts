export type Health = {
  status: 'ok' | 'degraded'
  version: string
  database: 'ok' | 'error'
  ts: string
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const resp = await fetch('/api/v1/health', { signal })
  if (!resp.ok && resp.status !== 503) {
    throw new Error(`La API respondió ${resp.status}`)
  }
  return (await resp.json()) as Health
}
