// Pantalla para las PCs de tiqueo: plano + alertas activas, sonido, reconexión automática.
// Liviana, letra grande, alto contraste. Acceso con token de dispositivo (link) o PIN.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { FloorPlan, labelFontSize } from '../components/FloorPlan'
import { ApiError, apiJson, getToken } from '../lib/api'
import { STATE_STYLE } from '../lib/colors'
import { formatDuration, formatTime } from '../lib/format'
import { ALERT_LABEL, SEVERITY_RANK } from '../lib/labels'
import { elapsedNow, useLive, useNow } from '../lib/live'
import { enableSound, playAlert, soundEnabled } from '../lib/sound'
import type { AlertData, Table } from '../lib/types'

const DEVICE_KEY = 'mesa-monitor.device'

function readDeviceToken(): string | null {
  const fromUrl = new URLSearchParams(location.search).get('token')
  try {
    if (fromUrl) {
      localStorage.setItem(DEVICE_KEY, fromUrl)
      history.replaceState(null, '', location.pathname) // el token no queda a la vista
      return fromUrl
    }
    return localStorage.getItem(DEVICE_KEY) ?? getToken()
  } catch {
    return fromUrl ?? getToken()
  }
}

async function call<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  const r = await fetch(`/api/v1${path}`, { ...init, headers })
  if (!r.ok) throw new ApiError(r.status, r.status === 401 ? 'Dispositivo no autorizado' : `Error ${r.status}`)
  return (await r.json()) as T
}

function PinScreen({ onToken }: { onToken: (t: string) => void }) {
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const r = await apiJson<{ token: string }>('/auth/device-pin', { method: 'POST', body: JSON.stringify({ pin }) })
      try {
        localStorage.setItem(DEVICE_KEY, r.token)
      } catch {
        // sin almacenamiento: dura lo que la pestaña
      }
      onToken(r.token)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }
  return (
    <main className="flex min-h-screen items-center justify-center bg-black p-6 text-white">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 text-center">
        <h1 className="text-3xl font-bold">Alertas de mesas</h1>
        <p className="text-lg text-slate-300">Ingresá el PIN de esta computadora</p>
        <input
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode="numeric"
          autoFocus
          className="w-full rounded-lg border-2 border-slate-600 bg-slate-900 px-4 py-3 text-center text-4xl tracking-[0.5em]"
          aria-label="PIN"
        />
        {error && <p className="text-xl text-red-400">{error}</p>}
        <button disabled={pin.length !== 6} className="w-full rounded-lg bg-sky-600 py-3 text-2xl font-semibold disabled:opacity-40">
          Entrar
        </button>
      </form>
    </main>
  )
}

function sortAlerts(alerts: AlertData[]): AlertData[] {
  return [...alerts].sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.opened_at.localeCompare(b.opened_at),
  )
}

export function AlertasPage() {
  const [token, setToken] = useState<string | null>(readDeviceToken)
  const live = useLive(token)
  const now = useNow(1000)
  const [tables, setTables] = useState<Table[]>([])
  const [sound, setSound] = useState(soundEnabled())
  const [notify, setNotify] = useState(() => typeof Notification !== 'undefined' && Notification.permission === 'granted')
  const [authError, setAuthError] = useState<string | null>(null)
  const [repeatMs, setRepeatMs] = useState(60_000)
  const lastPlayed = useRef<Record<string, number>>({})
  const seen = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!token) return
    call<{ sound_repeat_s: number }>(token, '/alerts/screen-settings')
      .then((s) => setRepeatMs(Math.max(10, s.sound_repeat_s) * 1000))
      .catch(() => undefined)
    call<Table[]>(token, '/tables')
      .then(setTables)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) {
          setAuthError('Este dispositivo no está autorizado o fue revocado.')
          setToken(null)
          try {
            localStorage.removeItem(DEVICE_KEY)
          } catch {
            // nada
          }
        }
      })
  }, [token, live.connected])

  const alerts = useMemo(() => sortAlerts(Object.values(live.alerts)), [live.alerts])
  const needsAttention = (a: AlertData) =>
    a.status === 'open' && !(a.snoozed_until && Date.parse(a.snoozed_until) > now)

  // Sonido: al aparecer una alerta y cada `repeatMs` (configurable) mientras no se reconozca ni posponga.
  useEffect(() => {
    for (const a of alerts) {
      if (!needsAttention(a)) continue
      const last = lastPlayed.current[a.alert_id] ?? 0
      if (now - last >= repeatMs) {
        playAlert(a.severity)
        lastPlayed.current[a.alert_id] = now
      }
      if (!seen.current.has(a.alert_id)) {
        seen.current.add(a.alert_id)
        if (notify && document.hidden) {
          new Notification(`Mesa ${a.table?.label ?? a.camera_id}: ${ALERT_LABEL[a.alert_type]}`, { tag: a.alert_id })
        }
      }
    }
  })

  const act = useCallback(
    async (a: AlertData, action: 'ack' | 'snooze') => {
      if (!token) return
      await call(token, `/alerts/${a.alert_id}/${action}`, {
        method: 'POST',
        body: action === 'snooze' ? JSON.stringify({ minutes: 5 }) : undefined,
      }).catch(() => undefined)
    },
    [token],
  )

  if (!token) {
    return (
      <>
        {authError && <p className="bg-red-800 p-3 text-center text-xl text-white">{authError}</p>}
        <PinScreen onToken={setToken} />
      </>
    )
  }

  const alertByTable = new Map<string, AlertData>()
  for (const a of alerts) if (a.table && !alertByTable.has(a.table.id)) alertByTable.set(a.table.id, a)
  const stale = live.lastMessageAt != null && now - live.lastMessageAt > 40_000

  return (
    <main className="flex min-h-screen flex-col bg-black text-white">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-800 px-4 py-2">
        <h1 className="text-2xl font-bold">Mesas</h1>
        <span className="text-2xl tabular-nums text-slate-300">{formatTime(new Date(now).toISOString())}</span>
        <span className="ml-auto" />
        {!sound && (
          <button onClick={async () => setSound(await enableSound())} className="rounded-lg bg-amber-500 px-4 py-2 text-lg font-bold text-black">
            🔊 Activar sonido
          </button>
        )}
        {typeof Notification !== 'undefined' && !notify && (
          <button onClick={async () => setNotify((await Notification.requestPermission()) === 'granted')} className="rounded-lg bg-slate-700 px-3 py-2 text-base">
            Notificaciones
          </button>
        )}
        <button
          onClick={() => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen())}
          className="rounded-lg bg-slate-700 px-3 py-2 text-base"
        >
          Pantalla completa
        </button>
      </header>
      {(!live.connected || stale) && (
        <div role="alert" className="bg-red-700 px-4 py-2 text-center text-xl font-bold">
          Sin conexión con el servidor — reconectando… (los datos pueden estar desactualizados)
        </div>
      )}
      <div className="grid flex-1 gap-4 p-4 lg:grid-cols-[1fr_420px]">
        <section aria-label="Plano del salón">
          <FloorPlan
            tables={tables.filter((t) => t.enabled)}
            fill={(t) => STATE_STYLE[live.tables[t.id]?.state ?? 'LIBRE'].fill}
            stroke={(t) => {
              const a = alertByTable.get(t.id)
              return a ? (a.severity === 'critical' ? '#ef4444' : '#f59e0b') : '#0f172a'
            }}
            content={(t) => {
              const lt = live.tables[t.id]
              const cx = t.plan_x + t.plan_w / 2
              const cy = t.plan_y + t.plan_h / 2
              const fs = labelFontSize(t, 0.32)
              const el = lt ? elapsedNow(lt, now) : null
              const showTimer = lt && lt.state !== 'LIBRE' && lt.state !== 'DESHABILITADA' && el != null
              return (
                <>
                  <text x={cx} y={showTimer ? cy - t.plan_h * 0.17 : cy} textAnchor="middle" dominantBaseline="central" fontSize={fs} fontWeight={700} fill="#fff">
                    {t.label}
                  </text>
                  {showTimer && (
                    <text x={cx} y={cy + t.plan_h * 0.2} textAnchor="middle" dominantBaseline="central" fontSize={Math.min(t.plan_w, t.plan_h) * 0.26} fill="#e2e8f0" className="tabular-nums">
                      {formatDuration(el)}
                    </text>
                  )}
                </>
              )
            }}
          />
          <div className="mt-2 flex flex-wrap gap-4 text-base">
            {(['LIBRE', 'OCUPADA_SIN_ATENDER', 'ATENDIDA', 'DESOCUPADA_SUCIA', 'SIN_VISION'] as const).map((s) => (
              <span key={s} className="flex items-center gap-2">
                <span className="inline-block h-4 w-4 rounded" style={{ background: STATE_STYLE[s].fill }} />
                {STATE_STYLE[s].label}
              </span>
            ))}
          </div>
        </section>
        <section aria-label="Alertas activas" className="space-y-3">
          <h2 className="text-xl font-semibold text-slate-300">Alertas ({alerts.length})</h2>
          {alerts.length === 0 && <p className="rounded-lg border border-slate-800 p-6 text-center text-2xl text-emerald-400">Todo en orden</p>}
          {alerts.map((a) => {
            const pending = needsAttention(a)
            const elapsed = (now - Date.parse(a.state_since ?? a.opened_at)) / 1000
            const color = a.severity === 'critical' ? 'border-red-500 bg-red-950' : a.severity === 'warning' ? 'border-amber-400 bg-amber-950' : 'border-sky-500 bg-sky-950'
            return (
              <article key={a.alert_id} className={`rounded-xl border-4 p-4 ${color} ${pending ? '' : 'opacity-60'}`}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-4xl font-black">{a.table ? `Mesa ${a.table.label}` : `Cámara ${a.camera_id}`}</span>
                  <span className="text-3xl font-bold tabular-nums">{formatDuration(elapsed)}</span>
                </div>
                <p className="text-2xl">{ALERT_LABEL[a.alert_type]}</p>
                {a.status === 'acknowledged' && <p className="text-lg text-slate-300">Visto</p>}
                {a.snoozed_until && Date.parse(a.snoozed_until) > now && <p className="text-lg text-slate-300">Pospuesta hasta {formatTime(a.snoozed_until)}</p>}
                <div className="mt-3 flex gap-3">
                  <button onClick={() => act(a, 'ack')} disabled={a.status === 'acknowledged'} className="flex-1 rounded-lg bg-white py-3 text-2xl font-bold text-black disabled:opacity-40">
                    Visto
                  </button>
                  <button onClick={() => act(a, 'snooze')} className="flex-1 rounded-lg bg-slate-700 py-3 text-xl font-semibold">
                    Posponer 5 min
                  </button>
                </div>
              </article>
            )
          })}
        </section>
      </div>
    </main>
  )
}
