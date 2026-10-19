// Análisis de atención de mesas (SPEC §9, §11.2). Solo por mesa, sector, hora, día y turno:
// nunca por persona (SPEC Anexo A).
import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Button, Card, ErrorBox, Field, PageHeader, inputClass } from '../components/ui'
import { apiFetch, apiJson } from '../lib/api'
import type { Table } from '../lib/types'

// Paleta validada (dataviz, modo oscuro, superficie #0f172a): slot 1 azul, slot 2 naranja.
const C = {
  s1: '#3987e5',
  s2: '#d95926',
  grid: '#1e293b',
  axis: '#64748b',
  ink: '#e2e8f0',
  muted: '#94a3b8',
  seq: ['#104281', '#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#86b6ef', '#b7d3f6'],
}

type Stat = { n: number; avg: number | null; median: number | null; p90: number | null }
type Group = {
  key: string
  label: string
  n_sessions: number
  n_in_progress: number
  n_excluded: number
  n_unattended: number
  sla_pct: number | null
  visits_avg: number | null
  rotation: number | null
  occupancy_pct: number | null
  t_esp: Stat
  t_est: Stat
  t_lim: Stat
  g_max: Stat
}
type Summary = { groups: Group[]; operating_hours: number; n_tables: number; sla_threshold_seconds: number }
type Cell = { weekday: number; hour: number; n: number; avg: number | null; p90: number | null }
type Bin = { from_s: number; to_s: number; n: number }

const PRESETS = { hoy: 'Hoy', ayer: 'Ayer', '7d': 'Últimos 7 días', '30d': 'Últimos 30 días', '90d': 'Últimos 90 días' } as const
type Preset = keyof typeof PRESETS | 'custom'
const DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']

function range(p: Preset, custom: { from: string; to: string }): { from: Date; to: Date } {
  const now = new Date()
  const today = new Date(now)
  today.setHours(6, 0, 0, 0) // día de negocio desde las 06:00
  if (now < today) today.setDate(today.getDate() - 1)
  switch (p) {
    case 'hoy':
      return { from: today, to: now }
    case 'ayer': {
      const y = new Date(today)
      y.setDate(y.getDate() - 1)
      return { from: y, to: today }
    }
    case '7d':
    case '30d':
    case '90d':
      return { from: new Date(now.getTime() - Number.parseInt(p) * 86400_000), to: now }
    default:
      return { from: new Date(custom.from), to: new Date(custom.to) }
  }
}

const min = (s: number | null | undefined) => (s == null ? '—' : `${(s / 60).toFixed(1).replace('.', ',')} min`)
const minNum = (s: number | null | undefined) => (s == null ? null : Math.round((s / 60) * 10) / 10)

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {hint && <div className="text-xs text-slate-500">{hint}</div>}
    </div>
  )
}

function TooltipBox({ active, payload, label, unit = 'min' }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string; unit?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-medium text-slate-200">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 text-slate-300">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />
          {p.name}: <strong className="text-slate-100">{p.value ?? '—'} {unit}</strong>
        </div>
      ))}
    </div>
  )
}

function Heatmap({ cells }: { cells: Cell[] }) {
  const [hover, setHover] = useState<Cell | null>(null)
  const hours = [...new Set(cells.map((c) => c.hour))].sort((a, b) => a - b)
  const max = Math.max(1, ...cells.map((c) => c.avg ?? 0))
  const byKey = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c]))
  if (!cells.length) return <p className="text-sm text-slate-500">Sin datos en el período.</p>
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-[2px] text-xs">
          <thead>
            <tr>
              <th />
              {hours.map((h) => (
                <th key={h} className="px-1 font-normal text-slate-500">
                  {h}h
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {DAYS.map((d, wd) => (
              <tr key={d}>
                <td className="pr-2 text-slate-400">{d}</td>
                {hours.map((h) => {
                  const c = byKey.get(`${wd}-${h}`)
                  const idx = c?.avg != null ? Math.min(C.seq.length - 1, Math.floor((c.avg / max) * (C.seq.length - 1))) : -1
                  return (
                    <td
                      key={h}
                      onMouseEnter={() => setHover(c ?? null)}
                      onMouseLeave={() => setHover(null)}
                      className="h-7 w-9 rounded text-center"
                      style={{ background: idx >= 0 ? C.seq[idx] : 'transparent', outline: idx < 0 ? '1px solid #1e293b' : undefined, color: idx > 5 ? '#0f172a' : '#e2e8f0' }}
                    >
                      {c?.avg != null ? (c.avg / 60).toFixed(0) : ''}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 h-4 text-xs text-slate-400">
        {hover ? `${DAYS[hover.weekday]} ${hover.hour}h · espera promedio ${min(hover.avg)} · p90 ${min(hover.p90)} · ${hover.n} sesiones` : 'Minutos de espera promedio. Pasá el mouse por una celda para ver el detalle.'}
      </p>
    </div>
  )
}

export function AnalysisPage() {
  const [preset, setPreset] = useState<Preset>('7d')
  const [custom, setCustom] = useState(() => ({ from: new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10), to: new Date().toISOString().slice(0, 10) }))
  const [sector, setSector] = useState('')
  const [table, setTable] = useState('')
  const [shift, setShift] = useState('')
  const [tables, setTables] = useState<Table[]>([])
  const [shifts, setShifts] = useState<string[]>([])
  const [total, setTotal] = useState<Summary | null>(null)
  const [byHour, setByHour] = useState<Group[]>([])
  const [byDay, setByDay] = useState<Group[]>([])
  const [byTable, setByTable] = useState<Group[]>([])
  const [heat, setHeat] = useState<Cell[]>([])
  const [dist, setDist] = useState<Bin[]>([])
  const [showTable, setShowTable] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    apiJson<Table[]>('/tables').then(setTables).catch(setError)
    apiJson<{ shifts: { name: string }[] }>('/config/schedule').then((s) => setShifts(s.shifts.map((x) => x.name))).catch(() => undefined)
  }, [])

  const query = useMemo(() => {
    const r = range(preset, custom)
    const q = new URLSearchParams({ from: r.from.toISOString(), to: r.to.toISOString() })
    if (sector) q.set('sector', sector)
    if (table) q.set('table', table)
    if (shift) q.set('shift', shift)
    return q.toString()
  }, [preset, custom, sector, table, shift])

  useEffect(() => {
    let cancelled = false
    const get = <T,>(path: string) => apiJson<T>(`/metrics/${path}${path.includes('?') ? '&' : '?'}${query}`)
    Promise.all([
      get<Summary>('summary'),
      get<Summary>('summary?group_by=hour'),
      get<Summary>('summary?group_by=day'),
      get<Summary>('summary?group_by=table'),
      get<Cell[]>('heatmap'),
      get<Bin[]>('distribution?metric=t_esp&bin_seconds=60'),
    ])
      .then(([t, h, d, tb, hm, ds]) => {
        if (cancelled) return
        setTotal(t)
        setByHour(h.groups)
        setByDay(d.groups)
        setByTable(tb.groups)
        setHeat(hm)
        setDist(ds)
        setError(null)
      })
      .catch((e: unknown) => !cancelled && setError(e))
    return () => {
      cancelled = true
    }
  }, [query])

  async function download(path: string, name: string) {
    try {
      const r = await apiFetch(`/metrics/${path}${path.includes('?') ? '&' : '?'}${query}`)
      const a = document.createElement('a')
      a.href = URL.createObjectURL(await r.blob())
      a.download = name
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (err) {
      setError(err)
    }
  }

  const g = total?.groups[0]
  const sectors = [...new Set(tables.map((t) => t.sector))]
  const hourData = byHour.map((x) => ({ label: x.label, Promedio: minNum(x.t_esp.avg), p90: minNum(x.t_esp.p90) }))
  const dayData = byDay.map((x) => ({ label: x.label.slice(5), 'SLA %': x.sla_pct }))
  const ranking = [...byTable].filter((x) => x.t_esp.p90 != null).sort((a, b) => (b.t_esp.p90 ?? 0) - (a.t_esp.p90 ?? 0)).slice(0, 10)
  const distData = dist.map((b) => ({ label: `${b.from_s / 60}–${b.to_s / 60}`, Sesiones: b.n }))

  return (
    <>
      <PageHeader
        title="Análisis"
        subtitle="Tiempos de atención de las mesas"
        actions={
          <>
            <Button variant="secondary" onClick={() => download('sessions.csv', 'sesiones.csv')}>
              Exportar sesiones (CSV)
            </Button>
            <Button variant="secondary" onClick={() => download('summary.csv?group_by=table', 'resumen-por-mesa.csv')}>
              Exportar resumen (CSV)
            </Button>
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="flex flex-wrap gap-1">
          {(Object.keys(PRESETS) as (keyof typeof PRESETS)[]).map((p) => (
            <Button key={p} variant={preset === p ? 'primary' : 'secondary'} onClick={() => setPreset(p)}>
              {PRESETS[p]}
            </Button>
          ))}
          <Button variant={preset === 'custom' ? 'primary' : 'secondary'} onClick={() => setPreset('custom')}>
            Personalizado
          </Button>
        </div>
        {preset === 'custom' && (
          <>
            <Field label="Desde">
              <input type="date" className={inputClass} value={custom.from} onChange={(e) => setCustom({ ...custom, from: e.target.value })} />
            </Field>
            <Field label="Hasta">
              <input type="date" className={inputClass} value={custom.to} onChange={(e) => setCustom({ ...custom, to: e.target.value })} />
            </Field>
          </>
        )}
        <Field label="Sector">
          <select className={inputClass} value={sector} onChange={(e) => setSector(e.target.value)}>
            <option value="">Todos</option>
            {sectors.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Field label="Mesa">
          <select className={inputClass} value={table} onChange={(e) => setTable(e.target.value)}>
            <option value="">Todas</option>
            {tables.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Turno">
          <select className={inputClass} value={shift} onChange={(e) => setShift(e.target.value)}>
            <option value="">Todos</option>
            {shifts.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorBox error={error} />
      {g ? (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
            <Tile label="Sesiones" value={String(g.n_sessions)} hint={g.n_excluded || g.n_in_progress ? `${g.n_excluded} excluidas · ${g.n_in_progress} en curso` : undefined} />
            <Tile label="Espera promedio" value={min(g.t_esp.avg)} hint={`mediana ${min(g.t_esp.median)}`} />
            <Tile label="Espera p90" value={min(g.t_esp.p90)} hint="9 de cada 10 esperan menos" />
            <Tile label="Atendidas a tiempo" value={g.sla_pct == null ? '—' : `${g.sla_pct.toString().replace('.', ',')} %`} hint={`antes de ${min(total?.sla_threshold_seconds)}`} />
            <Tile label="Rotación" value={g.rotation == null ? '—' : g.rotation.toFixed(2).replace('.', ',')} hint="sesiones por mesa por hora" />
            <Tile label="Ocupación" value={g.occupancy_pct == null ? '—' : `${g.occupancy_pct.toString().replace('.', ',')} %`} />
            <Tile label="Se fueron sin atención" value={String(g.n_unattended)} />
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Espera hasta la primera atención, por hora">
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={hourData} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
                  <CartesianGrid stroke={C.grid} vertical={false} />
                  <XAxis dataKey="label" stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} />
                  <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} axisLine={false} unit=" min" width={56} />
                  <Tooltip content={<TooltipBox />} cursor={{ stroke: C.axis, strokeDasharray: '3 3' }} />
                  <Legend wrapperStyle={{ fontSize: 12, color: C.ink }} />
                  <Line type="monotone" dataKey="Promedio" stroke={C.s1} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                  <Line type="monotone" dataKey="p90" stroke={C.s2} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} strokeDasharray="5 3" />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Espera promedio por día y hora (min)">
              <Heatmap cells={heat} />
            </Card>
            <Card title="Distribución de la espera">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={distData} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
                  <CartesianGrid stroke={C.grid} vertical={false} />
                  <XAxis dataKey="label" stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} unit=" min" />
                  <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <Tooltip content={<TooltipBox unit="sesiones" />} cursor={{ fill: '#1e293b' }} />
                  <Bar dataKey="Sesiones" fill={C.s1} radius={[4, 4, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Mesas con mayor espera (p90)">
              <ResponsiveContainer width="100%" height={Math.max(160, ranking.length * 28 + 40)}>
                <BarChart data={ranking.map((x) => ({ label: `Mesa ${x.label}`, p90: minNum(x.t_esp.p90) }))} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }}>
                  <CartesianGrid stroke={C.grid} horizontal={false} />
                  <XAxis type="number" stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} unit=" min" />
                  <YAxis type="category" dataKey="label" stroke={C.axis} tick={{ fill: C.ink, fontSize: 12 }} width={90} tickLine={false} />
                  <Tooltip content={<TooltipBox />} cursor={{ fill: '#1e293b' }} />
                  <Bar dataKey="p90" name="Espera p90" fill={C.s2} radius={[0, 4, 4, 0]} maxBarSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Atendidas a tiempo, por día" className="xl:col-span-2">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={dayData} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
                  <CartesianGrid stroke={C.grid} vertical={false} />
                  <XAxis dataKey="label" stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} />
                  <YAxis domain={[0, 100]} stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} axisLine={false} unit="%" />
                  <Tooltip content={<TooltipBox unit="%" />} cursor={{ fill: '#1e293b' }} />
                  <Bar dataKey="SLA %" fill={C.s1} radius={[4, 4, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          </div>
          <div className="mt-4">
            <Button variant="ghost" onClick={() => setShowTable(!showTable)}>
              {showTable ? 'Ocultar tabla por mesa' : 'Ver tabla por mesa'}
            </Button>
            {showTable && (
              <div className="mt-2 overflow-x-auto rounded-md border border-slate-800">
                <table className="w-full text-sm">
                  <thead className="bg-slate-900 text-left text-slate-400">
                    <tr>
                      {['Mesa', 'Sesiones', 'Espera prom.', 'Espera p90', 'A tiempo', 'Estadía prom.', 'Hasta limpiar', 'Mayor intervalo sin visita', 'Visitas prom.', 'Rotación'].map((h) => (
                        <th key={h} className="px-3 py-2 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {byTable.map((x) => (
                      <tr key={x.key} className="border-t border-slate-800">
                        <td className="px-3 py-1.5">{x.label}</td>
                        <td className="px-3">{x.n_sessions}</td>
                        <td className="px-3">{min(x.t_esp.avg)}</td>
                        <td className="px-3">{min(x.t_esp.p90)}</td>
                        <td className="px-3">{x.sla_pct ?? '—'} %</td>
                        <td className="px-3">{min(x.t_est.avg)}</td>
                        <td className="px-3">{min(x.t_lim.avg)}</td>
                        <td className="px-3">{min(x.g_max.avg)}</td>
                        <td className="px-3">{x.visits_avg ?? '—'}</td>
                        <td className="px-3">{x.rotation ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : (
        !error && <p className="text-slate-400">Cargando…</p>
      )}
    </>
  )
}
