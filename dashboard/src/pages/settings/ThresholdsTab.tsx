import { useEffect, useState } from 'react'
import { Button, Card, ErrorBox, inputClass } from '../../components/ui'
import { apiJson } from '../../lib/api'
import type { Table } from '../../lib/types'

type Def = { key: string; default: number | boolean; description: string; unit: string }
type Item = { scope: 'global' | 'sector' | 'table'; scope_ref: string | null; key: string; value: number | boolean; valid_from: string | null; valid_to: string | null }

const MIN_KEYS = new Set(['alert_sin_atencion_warning_s', 'alert_sin_atencion_critical_s', 'alert_sin_visita_s', 'alert_mesa_sucia_s', 'alert_estadia_larga_s'])
// Las alertas se editan en minutos (más natural); el resto en segundos.
const toUi = (key: string, v: number) => (MIN_KEYS.has(key) ? v / 60 : v)
const fromUi = (key: string, v: number) => (MIN_KEYS.has(key) ? Math.round(v * 60) : v)
const unitUi = (d: Def) => (MIN_KEYS.has(d.key) ? 'min' : d.unit)

export function ThresholdsTab() {
  const [defs, setDefs] = useState<Def[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [tables, setTables] = useState<Table[]>([])
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    apiJson<{ definitions: Def[]; items: Item[] }>('/config/thresholds')
      .then((r) => {
        setDefs(r.definitions)
        setItems(r.items)
      })
      .catch(setError)
    apiJson<Table[]>('/tables').then(setTables).catch(setError)
  }, [])

  const globalOf = (key: string) => items.find((i) => i.scope === 'global' && i.key === key && !i.valid_from)
  function setGlobal(key: string, value: number | boolean | null) {
    setSaved(false)
    setItems((cur) => {
      const rest = cur.filter((i) => !(i.scope === 'global' && i.key === key && !i.valid_from))
      return value === null ? rest : [...rest, { scope: 'global', scope_ref: null, key, value, valid_from: null, valid_to: null }]
    })
  }
  const overrides = items.filter((i) => i.scope !== 'global' || i.valid_from)
  const sectors = [...new Set(tables.map((t) => t.sector))]

  async function save() {
    setError(null)
    try {
      const r = await apiJson<{ items: Item[] }>('/config/thresholds', { method: 'PUT', body: JSON.stringify(items) })
      setItems(r.items)
      setSaved(true)
    } catch (err) {
      setError(err)
    }
  }

  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Valores generales" actions={<Button onClick={save}>{saved ? 'Guardado ✓' : 'Guardar'}</Button>}>
        <p className="mb-3 text-sm text-slate-400">Los cambios se aplican en segundos, sin reiniciar nada. Vacío = valor por defecto.</p>
        <div className="grid gap-x-6 gap-y-2 md:grid-cols-2">
          {defs.map((d) => {
            const cur = globalOf(d.key)
            return (
              <label key={d.key} className="flex items-center justify-between gap-3 text-sm">
                <span>{d.description}</span>
                {typeof d.default === 'boolean' ? (
                  <input type="checkbox" checked={Boolean(cur?.value ?? d.default)} onChange={(e) => setGlobal(d.key, e.target.checked)} />
                ) : (
                  <span className="flex shrink-0 items-center gap-1">
                    <input
                      type="number"
                      min={0}
                      step="any"
                      className={`${inputClass} w-24`}
                      placeholder={String(toUi(d.key, d.default))}
                      value={cur ? toUi(d.key, Number(cur.value)) : ''}
                      onChange={(e) => setGlobal(d.key, e.target.value === '' ? null : fromUi(d.key, Number(e.target.value)))}
                    />
                    <span className="w-14 text-slate-500">{unitUi(d)}</span>
                  </span>
                )}
              </label>
            )
          })}
        </div>
      </Card>
      <Card
        title="Excepciones por sector o mesa"
        actions={
          <Button
            variant="secondary"
            onClick={() => setItems((c) => [...c, { scope: 'sector', scope_ref: sectors[0] ?? 'salon', key: 'alert_sin_atencion_warning_s', value: 240, valid_from: null, valid_to: null }])}
          >
            + Agregar
          </Button>
        }
      >
        {overrides.length === 0 && <p className="text-sm text-slate-500">Sin excepciones.</p>}
        <div className="space-y-2">
          {overrides.map((o) => {
            const idx = items.indexOf(o)
            const upd = (patch: Partial<Item>) => {
              setSaved(false)
              setItems((c) => c.map((x, j) => (j === idx ? { ...x, ...patch } : x)))
            }
            const def = defs.find((d) => d.key === o.key)
            return (
              <div key={idx} className="flex flex-wrap items-center gap-2 text-sm">
                <select className={`${inputClass} w-28`} value={o.scope} onChange={(e) => upd({ scope: e.target.value as Item['scope'], scope_ref: e.target.value === 'global' ? null : (e.target.value === 'table' ? tables[0]?.id : sectors[0]) ?? null })}>
                  <option value="sector">Sector</option>
                  <option value="table">Mesa</option>
                  <option value="global">Franja horaria</option>
                </select>
                {o.scope === 'sector' && (
                  <select className={`${inputClass} w-32`} value={o.scope_ref ?? ''} onChange={(e) => upd({ scope_ref: e.target.value })}>
                    {sectors.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                )}
                {o.scope === 'table' && (
                  <select className={`${inputClass} w-32`} value={o.scope_ref ?? ''} onChange={(e) => upd({ scope_ref: e.target.value })}>
                    {tables.map((t) => (
                      <option key={t.id} value={t.id}>
                        Mesa {t.label}
                      </option>
                    ))}
                  </select>
                )}
                <select className={`${inputClass} w-72`} value={o.key} onChange={(e) => upd({ key: e.target.value })}>
                  {defs.filter((d) => typeof d.default !== 'boolean').map((d) => (
                    <option key={d.key} value={d.key}>
                      {d.description}
                    </option>
                  ))}
                </select>
                <input type="number" step="any" className={`${inputClass} w-24`} value={toUi(o.key, Number(o.value))} onChange={(e) => upd({ value: fromUi(o.key, Number(e.target.value)) })} />
                <span className="text-slate-500">{def ? unitUi(def) : ''}</span>
                <span className="text-slate-400">de</span>
                <input type="time" className={`${inputClass} w-28`} value={o.valid_from ?? ''} onChange={(e) => upd({ valid_from: e.target.value || null, valid_to: o.valid_to ?? (e.target.value ? '23:59' : null) })} />
                <span className="text-slate-400">a</span>
                <input type="time" className={`${inputClass} w-28`} value={o.valid_to ?? ''} onChange={(e) => upd({ valid_to: e.target.value || null })} />
                <Button variant="ghost" onClick={() => setItems((c) => c.filter((_, j) => j !== idx))}>
                  Quitar
                </Button>
              </div>
            )
          })}
        </div>
        <p className="mt-2 text-xs text-slate-500">Sin horario = todo el día. "Franja horaria" cambia el valor general solo en ese horario.</p>
      </Card>
    </div>
  )
}
