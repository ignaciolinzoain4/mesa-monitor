import { useEffect, useState } from 'react'
import { Button, Card, ErrorBox, Field, inputClass } from '../../components/ui'
import { apiJson } from '../../lib/api'

type Hours = { weekday: number; opens_at: string; closes_at: string }
type ShiftT = { name: string; starts_at: string; ends_at: string }
type Schedule = { operating_hours: Hours[]; shifts: ShiftT[]; business_day_cutoff: string }
const DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
const hhmm = (t: string) => t.slice(0, 5)

export function ScheduleTab() {
  const [s, setS] = useState<Schedule | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    apiJson<Schedule>('/config/schedule').then(setS).catch(setError)
  }, [])
  if (!s) return <ErrorBox error={error} />
  const upd = (patch: Partial<Schedule>) => {
    setSaved(false)
    setS({ ...s, ...patch })
  }
  async function save() {
    setError(null)
    try {
      setS(await apiJson<Schedule>('/config/schedule', { method: 'PUT', body: JSON.stringify(s) }))
      setSaved(true)
    } catch (err) {
      setError(err)
    }
  }
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Horario operativo" actions={<Button onClick={save}>{saved ? 'Guardado ✓' : 'Guardar'}</Button>}>
        <p className="mb-3 text-sm text-slate-400">
          Fuera de horario el sistema queda en reposo y al cerrar se cierran las mesas abiertas. Si cierra después de medianoche, poné la hora de cierre del día siguiente (ej. 08:00 a 02:00). Sin horarios = siempre abierto.
        </p>
        <div className="space-y-2">
          {DAYS.map((name, wd) => {
            const spans = s.operating_hours.filter((h) => h.weekday === wd)
            return (
              <div key={wd} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="w-24">{name}</span>
                {spans.length === 0 && <span className="text-slate-500">Cerrado</span>}
                {spans.map((h) => {
                  const i = s.operating_hours.indexOf(h)
                  const set = (p: Partial<Hours>) => upd({ operating_hours: s.operating_hours.map((x, j) => (j === i ? { ...x, ...p } : x)) })
                  return (
                    <span key={i} className="flex items-center gap-1">
                      <input type="time" className={`${inputClass} w-28`} value={hhmm(h.opens_at)} onChange={(e) => set({ opens_at: e.target.value })} />
                      a
                      <input type="time" className={`${inputClass} w-28`} value={hhmm(h.closes_at)} onChange={(e) => set({ closes_at: e.target.value })} />
                      <Button variant="ghost" onClick={() => upd({ operating_hours: s.operating_hours.filter((_, j) => j !== i) })}>
                        ✕
                      </Button>
                    </span>
                  )
                })}
                <Button variant="ghost" onClick={() => upd({ operating_hours: [...s.operating_hours, { weekday: wd, opens_at: '08:00', closes_at: '01:00' }] })}>
                  + horario
                </Button>
              </div>
            )
          })}
        </div>
      </Card>
      <Card title="Turnos (para filtrar el análisis)">
        <div className="space-y-2">
          {s.shifts.map((sh, i) => {
            const set = (p: Partial<ShiftT>) => upd({ shifts: s.shifts.map((x, j) => (j === i ? { ...x, ...p } : x)) })
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
                <input className={`${inputClass} w-40`} value={sh.name} onChange={(e) => set({ name: e.target.value })} />
                <input type="time" className={`${inputClass} w-28`} value={hhmm(sh.starts_at)} onChange={(e) => set({ starts_at: e.target.value })} />
                a
                <input type="time" className={`${inputClass} w-28`} value={hhmm(sh.ends_at)} onChange={(e) => set({ ends_at: e.target.value })} />
                <Button variant="ghost" onClick={() => upd({ shifts: s.shifts.filter((_, j) => j !== i) })}>
                  ✕
                </Button>
              </div>
            )
          })}
          <Button variant="secondary" onClick={() => upd({ shifts: [...s.shifts, { name: `Turno ${s.shifts.length + 1}`, starts_at: '08:00', ends_at: '16:00' }] })}>
            + Turno
          </Button>
        </div>
        <div className="mt-4 max-w-xs">
          <Field label="Hora de corte del día" hint="Lo que pasa antes de esta hora cuenta para el día anterior (ej. 06:00).">
            <input type="time" className={inputClass} value={hhmm(s.business_day_cutoff)} onChange={(e) => upd({ business_day_cutoff: e.target.value })} />
          </Field>
        </div>
      </Card>
    </div>
  )
}
