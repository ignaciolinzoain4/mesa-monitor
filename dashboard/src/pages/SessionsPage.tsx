import { useCallback, useEffect, useState } from 'react'
import { Button, Card, ErrorBox, Field, PageHeader, inputClass } from '../components/ui'
import { apiJson } from '../lib/api'
import { formatDateTime, formatMinutes, formatTime } from '../lib/format'
import { useNow } from '../lib/live'

type Session = {
  id: string
  table: { id: string; label: string; sector: string }
  business_date: string
  seated_at: string
  first_attention_at: string | null
  vacated_at: string | null
  cleaned_at: string | null
  closed_at: string | null
  close_reason: string | null
  max_people: number
  visits: number
  wait_seconds: number | null
  stay_seconds: number | null
  dirty_seconds: number | null
}
type Detail = Session & {
  visit_list: { started_at: string; ended_at: string | null }[]
  transitions: { ts: string; from_state: string | null; to_state: string; reason: string }[]
}

const CLOSE_REASON: Record<string, string> = {
  cleaned: 'Limpia',
  reoccupied: 'Se ocupó antes de limpiar',
  vision_lost: 'Sin visión',
  closing_time: 'Cierre del local',
  disabled: 'Mesa deshabilitada',
}

function localInput(d: Date): string {
  const p = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function Timeline({ s, now }: { s: Detail; now: number }) {
  const start = Date.parse(s.seated_at)
  const endIso = s.cleaned_at ?? s.closed_at ?? s.vacated_at
  const end = endIso ? Date.parse(endIso) : now
  const span = Math.max(60_000, end - start)
  const x = (iso: string) => `${Math.min(100, Math.max(0, ((Date.parse(iso) - start) / span) * 100))}%`
  const marks: [string | null, string, string][] = [
    [s.seated_at, 'Se sientan', 'bg-amber-400'],
    [s.first_attention_at, 'Primera atención', 'bg-sky-400'],
    [s.vacated_at, 'Se van', 'bg-violet-400'],
    [s.cleaned_at, 'Limpia', 'bg-emerald-400'],
  ]
  return (
    <div className="space-y-3">
      <div className="relative h-14 rounded bg-slate-800">
        {s.visit_list.map((v, i) => (
          <div
            key={i}
            title={`Visita ${formatTime(v.started_at)}`}
            className="absolute top-5 h-4 rounded bg-sky-600/70"
            style={{ left: x(v.started_at), width: `max(3px, calc(${x(v.ended_at ?? v.started_at)} - ${x(v.started_at)}))` }}
          />
        ))}
        {marks.map(([iso, label, color]) =>
          iso ? (
            <div key={label} className="absolute top-0 h-full" style={{ left: x(iso) }} title={`${label} ${formatTime(iso)}`}>
              <div className={`h-full w-0.5 ${color}`} />
            </div>
          ) : null,
        )}
      </div>
      <ul className="grid gap-1 text-sm sm:grid-cols-2">
        {marks.map(([iso, label, color]) => (
          <li key={label} className="flex items-center gap-2">
            <span className={`inline-block h-3 w-3 rounded-sm ${color}`} />
            {label}: {formatTime(iso)}
          </li>
        ))}
        <li className="flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-sm bg-sky-600" />
          Visitas del personal: {s.visit_list.length}
        </li>
      </ul>
    </div>
  )
}

export function SessionsPage() {
  const now = useNow(30_000)
  const [from, setFrom] = useState(() => localInput(new Date(Date.now() - 24 * 3600_000)))
  const [to, setTo] = useState(() => localInput(new Date()))
  const [table, setTable] = useState('')
  const [sector, setSector] = useState('')
  const [rows, setRows] = useState<Session[] | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(() => {
    const q = new URLSearchParams({ from: new Date(from).toISOString(), to: new Date(to).toISOString() })
    if (table) q.set('table', table)
    if (sector) q.set('sector', sector)
    apiJson<Session[]>(`/sessions?${q}`)
      .then((r) => {
        setRows(r)
        setError(null)
      })
      .catch(setError)
  }, [from, to, table, sector])

  useEffect(() => {
    load()
  }, [load])

  return (
    <>
      <PageHeader title="Sesiones" subtitle="Cada ocupación de mesa: desde que se sientan hasta que queda limpia" />
      <ErrorBox error={error} />
      <div className="mb-4 grid gap-3 sm:grid-cols-5">
        <Field label="Desde">
          <input type="datetime-local" className={inputClass} value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="Hasta">
          <input type="datetime-local" className={inputClass} value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
        <Field label="Mesa">
          <input className={inputClass} value={table} onChange={(e) => setTable(e.target.value)} placeholder="Todas" />
        </Field>
        <Field label="Sector">
          <input className={inputClass} value={sector} onChange={(e) => setSector(e.target.value)} placeholder="Todos" />
        </Field>
        <div className="flex items-end">
          <Button onClick={load}>Buscar</Button>
        </div>
      </div>
      {detail && (
        <Card
          className="mb-4"
          title={`Mesa ${detail.table.label} · ${formatDateTime(detail.seated_at)}`}
          actions={
            <Button variant="ghost" onClick={() => setDetail(null)}>
              Cerrar
            </Button>
          }
        >
          <Timeline s={detail} now={now} />
        </Card>
      )}
      <div className="overflow-x-auto rounded-md border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-left text-slate-400">
            <tr>
              {['Mesa', 'Se sentaron', 'Espera', 'Visitas', 'Estadía', 'Hasta limpiar', 'Personas', 'Cierre'].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows?.map((r) => (
              <tr
                key={r.id}
                className="cursor-pointer border-t border-slate-800 hover:bg-slate-900"
                onClick={() => apiJson<Detail>(`/sessions/${r.id}`).then(setDetail).catch(setError)}
              >
                <td className="px-3 py-2 font-medium">{r.table.label}</td>
                <td className="px-3 py-2">{formatDateTime(r.seated_at)}</td>
                <td className="px-3 py-2">{formatMinutes(r.wait_seconds)}</td>
                <td className="px-3 py-2">{r.visits}</td>
                <td className="px-3 py-2">{formatMinutes(r.stay_seconds)}</td>
                <td className="px-3 py-2">{formatMinutes(r.dirty_seconds)}</td>
                <td className="px-3 py-2">{r.max_people}</td>
                <td className="px-3 py-2 text-slate-400">{r.closed_at ? (CLOSE_REASON[r.close_reason ?? ''] ?? r.close_reason) : 'En curso'}</td>
              </tr>
            ))}
            {rows?.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                  No hay sesiones en ese período.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
