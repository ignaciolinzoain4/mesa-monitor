import { useEffect, useMemo, useState } from 'react'
import { FloorPlan, labelFontSize } from '../components/FloorPlan'
import { Badge, Button, Card, ErrorBox, PageHeader } from '../components/ui'
import { apiJson, getToken } from '../lib/api'
import { STATE_STYLE } from '../lib/colors'
import { formatDuration, formatTime } from '../lib/format'
import { ALERT_LABEL, SEVERITY_LABEL, SEVERITY_RANK } from '../lib/labels'
import { elapsedNow, useLive, useNow } from '../lib/live'
import type { AlertData, Table } from '../lib/types'

function mediaUrl(path: string): string {
  return `/api/v1${path}${path.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(getToken() ?? '')}`
}

export function LivePage() {
  const token = getToken()
  const live = useLive(token)
  const now = useNow(1000)
  const [tables, setTables] = useState<Table[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [view, setView] = useState<'snapshot' | 'live' | 'debug'>('snapshot')
  const [snapKey, setSnapKey] = useState(() => Date.now())
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    apiJson<Table[]>('/tables').then(setTables).catch(setError)
  }, [])

  const alerts = useMemo(
    () =>
      Object.values(live.alerts).sort(
        (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.opened_at.localeCompare(b.opened_at),
      ),
    [live.alerts],
  )
  const alertByTable = new Map<string, AlertData>()
  for (const a of alerts) if (a.table && !alertByTable.has(a.table.id)) alertByTable.set(a.table.id, a)
  const counts = Object.values(live.tables).reduce<Record<string, number>>((acc, t) => {
    acc[t.state] = (acc[t.state] ?? 0) + 1
    return acc
  }, {})
  const sel = selected ? live.tables[selected] : null
  const selTable = tables.find((t) => t.id === selected)

  async function act(a: AlertData, action: 'ack' | 'snooze') {
    try {
      await apiJson(`/alerts/${a.alert_id}/${action}`, { method: 'POST', body: action === 'snooze' ? JSON.stringify({ minutes: 5 }) : undefined })
    } catch (err) {
      setError(err)
    }
  }

  return (
    <>
      <PageHeader
        title="En vivo"
        subtitle="Estado de las mesas y alertas en tiempo real"
        actions={live.connected ? <Badge tone="green">Conectado</Badge> : <Badge tone="red">Sin conexión — reconectando…</Badge>}
      />
      <ErrorBox error={error} />
      <div className="mb-3 flex flex-wrap gap-3 text-sm">
        {Object.entries(STATE_STYLE).map(([s, st]) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: st.fill }} />
            {st.label}: <strong>{counts[s] ?? 0}</strong>
          </span>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <FloorPlan
            tables={tables}
            selectedId={selected}
            onSelect={(t) => setSelected(t?.id ?? null)}
            fill={(t) => STATE_STYLE[live.tables[t.id]?.state ?? 'LIBRE'].fill}
            stroke={(t) => {
              const a = alertByTable.get(t.id)
              return a ? (a.severity === 'critical' ? '#ef4444' : '#f59e0b') : '#1e293b'
            }}
            content={(t) => {
              const lt = live.tables[t.id]
              const cx = t.plan_x + t.plan_w / 2
              const cy = t.plan_y + t.plan_h / 2
              const fs = labelFontSize(t, 0.3)
              const el = lt ? elapsedNow(lt, now) : null
              const timer = lt && !['LIBRE', 'DESHABILITADA'].includes(lt.state) && el != null
              return (
                <>
                  <text x={cx} y={timer ? cy - t.plan_h * 0.17 : cy} textAnchor="middle" dominantBaseline="central" fontSize={fs} fontWeight={700} fill="#fff">
                    {t.label}
                  </text>
                  {timer && (
                    <text x={cx} y={cy + t.plan_h * 0.2} textAnchor="middle" dominantBaseline="central" fontSize={Math.min(t.plan_w, t.plan_h) * 0.24} fill="#e2e8f0">
                      {formatDuration(el)}
                    </text>
                  )}
                </>
              )
            }}
          />
          {sel && selTable && (
            <Card
              title={`Mesa ${selTable.label} · ${STATE_STYLE[sel.state].label}`}
              actions={
                <Button variant="ghost" onClick={() => setSelected(null)}>
                  Cerrar
                </Button>
              }
            >
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
                <dt className="text-slate-400">En este estado</dt>
                <dd>{sel.elapsed_seconds != null ? formatDuration(elapsedNow(sel, now) ?? 0) : '—'}</dd>
                <dt className="text-slate-400">Personas</dt>
                <dd>{sel.people}</dd>
                <dt className="text-slate-400">Se sentaron</dt>
                <dd>{formatTime(sel.seated_at)}</dd>
                <dt className="text-slate-400">Primera atención</dt>
                <dd>{formatTime(sel.first_attention_at)}</dd>
                <dt className="text-slate-400">Comida en la mesa</dt>
                <dd>{formatTime(sel.served_at ?? null)}</dd>
                <dt className="text-slate-400">Última visita</dt>
                <dd>{formatTime(sel.last_visit_at)}</dd>
                <dt className="text-slate-400">Se fueron</dt>
                <dd>{formatTime(sel.vacated_at)}</dd>
              </dl>
              {selTable.camera_ids.length > 0 && (
                <div className="mt-4 space-y-2">
                  <div className="flex gap-2">
                    <Button variant="secondary" onClick={() => setSnapKey(Date.now())}>
                      Actualizar imagen
                    </Button>
                    <Button variant={view === 'live' ? 'primary' : 'secondary'} onClick={() => setView(view === 'live' ? 'snapshot' : 'live')}>
                      Video en vivo
                    </Button>
                    <Button variant={view === 'debug' ? 'primary' : 'secondary'} onClick={() => setView(view === 'debug' ? 'snapshot' : 'debug')}>
                      Overlay de debug
                    </Button>
                  </div>
                  <div className="grid gap-2 md:grid-cols-2">
                    {selTable.camera_ids.map((cid) => (
                      <figure key={cid}>
                        <img
                          className="max-h-[50vh] w-full rounded bg-black object-contain"
                          alt={`Cámara ${cid}`}
                          src={mediaUrl(
                            view === 'debug' ? `/cameras/${cid}/debug.mjpeg` : view === 'live' ? `/cameras/${cid}/live.mjpeg` : `/cameras/${cid}/snapshot?stream=main&_=${snapKey}`,
                          )}
                        />
                        <figcaption className="text-xs text-slate-500">
                          {live.cameras[cid]?.name ?? cid}
                          {view === 'debug' && ' — overlay (si no se ve, activalo en Configuración → Visión)'}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
        <div className="space-y-4">
          <Card title={`Alertas activas (${alerts.length})`}>
            {alerts.length === 0 && <p className="text-sm text-emerald-400">Sin alertas.</p>}
            <ul className="space-y-2">
              {alerts.map((a) => (
                <li key={a.alert_id} className={`rounded-md border p-2 ${a.severity === 'critical' ? 'border-red-700 bg-red-950/40' : a.severity === 'warning' ? 'border-amber-700 bg-amber-950/40' : 'border-sky-800'}`}>
                  <div className="flex items-baseline justify-between">
                    <button className="font-semibold hover:underline" onClick={() => a.table && setSelected(a.table.id)}>
                      {a.table ? `Mesa ${a.table.label}` : `Cámara ${a.camera_id}`}
                    </button>
                    <span className="tabular-nums">{formatDuration((now - Date.parse(a.state_since ?? a.opened_at)) / 1000)}</span>
                  </div>
                  <div className="text-sm text-slate-300">
                    {ALERT_LABEL[a.alert_type]} · {SEVERITY_LABEL[a.severity]}
                    {a.status === 'acknowledged' && ' · vista'}
                  </div>
                  <div className="mt-1 flex gap-2">
                    <Button variant="secondary" disabled={a.status === 'acknowledged'} onClick={() => act(a, 'ack')}>
                      Visto
                    </Button>
                    <Button variant="ghost" onClick={() => act(a, 'snooze')}>
                      Posponer 5 min
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Cámaras">
            <ul className="space-y-1 text-sm">
              {Object.values(live.cameras).map((c) => (
                <li key={c.camera_id} className="flex justify-between">
                  <span>{c.name}</span>
                  <Badge tone={c.status === 'online' ? 'green' : c.status === 'degraded' ? 'amber' : 'red'}>
                    {c.status === 'online' ? 'En línea' : c.status === 'degraded' ? 'Con problemas' : 'Sin señal'}
                  </Badge>
                </li>
              ))}
            </ul>
          </Card>
          <p className="text-xs text-slate-500">
            Para las PCs de tiqueo usá la pantalla <a className="text-sky-400 underline" href="/alertas">/alertas</a> (Configuración → Accesos → Dispositivos).
          </p>
        </div>
      </div>
    </>
  )
}
