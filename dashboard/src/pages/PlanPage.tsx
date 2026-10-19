import { useCallback, useEffect, useState } from 'react'
import { FloorPlan } from '../components/FloorPlan'
import { Badge, Button, Card, ErrorBox, Field, PageHeader, inputClass } from '../components/ui'
import { apiJson } from '../lib/api'
import { sectorColor } from '../lib/colors'
import type { Table } from '../lib/types'

type Draft = { label: string; sector: string; capacity: string }

export function PlanPage() {
  const [tables, setTables] = useState<Table[]>([])
  const [moved, setMoved] = useState<Set<string>>(new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [draft, setDraft] = useState<Draft>({ label: '', sector: 'salon', capacity: '' })

  const load = useCallback(() => apiJson<Table[]>('/tables').then(setTables).catch(setError), [])
  useEffect(() => {
    load()
  }, [load])

  const sectors = [...new Set(tables.map((t) => t.sector))].sort()
  const selected = tables.find((t) => t.id === selectedId) ?? null

  function patchLocal(id: string, changes: Partial<Table>) {
    setTables((ts) => ts.map((t) => (t.id === id ? { ...t, ...changes } : t)))
  }

  async function saveLayout() {
    setError(null)
    try {
      await apiJson('/tables/layout/positions', {
        method: 'PUT',
        body: JSON.stringify(
          tables.filter((t) => moved.has(t.id)).map(({ id, plan_x, plan_y, plan_w, plan_h, plan_rotation }) => ({ id, plan_x, plan_y, plan_w, plan_h, plan_rotation })),
        ),
      })
      setMoved(new Set())
    } catch (err) {
      setError(err)
    }
  }

  async function addTable() {
    setError(null)
    const n = tables.length
    try {
      const t = await apiJson<Table>('/tables', {
        method: 'POST',
        body: JSON.stringify({
          label: draft.label,
          sector: draft.sector,
          capacity: draft.capacity ? Number(draft.capacity) : null,
          plan_x: 2 + (n % 10) * 6,
          plan_y: 2 + Math.floor(n / 10) * 6,
          plan_w: 4,
          plan_h: 4,
        }),
      })
      setTables((ts) => [...ts, t])
      setSelectedId(t.id)
      setDraft((d) => ({ ...d, label: nextLabel(d.label), capacity: d.capacity }))
    } catch (err) {
      setError(err)
    }
  }

  async function saveSelected() {
    if (!selected) return
    setError(null)
    try {
      const t = await apiJson<Table>(`/tables/${selected.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          label: selected.label,
          sector: selected.sector,
          capacity: selected.capacity,
          enabled: selected.enabled,
          plan_x: selected.plan_x,
          plan_y: selected.plan_y,
          plan_w: selected.plan_w,
          plan_h: selected.plan_h,
          plan_rotation: selected.plan_rotation,
        }),
      })
      patchLocal(t.id, t)
      setMoved((m) => {
        const n = new Set(m)
        n.delete(t.id)
        return n
      })
    } catch (err) {
      setError(err)
    }
  }

  async function deleteSelected() {
    if (!selected || !window.confirm(`¿Borrar la mesa ${selected.label}?`)) return
    try {
      await apiJson(`/tables/${selected.id}`, { method: 'DELETE' })
      setTables((ts) => ts.filter((t) => t.id !== selected.id))
      setSelectedId(null)
    } catch (err) {
      setError(err)
    }
  }

  return (
    <>
      <PageHeader
        title="Plano del salón"
        subtitle="Ubicá las mesas arrastrándolas. El plano se usa en la vista en vivo y en la pantalla de alertas."
        actions={
          <Button onClick={saveLayout} disabled={moved.size === 0}>
            Guardar ubicaciones{moved.size > 0 ? ` (${moved.size})` : ''}
          </Button>
        }
      />
      <ErrorBox error={error} />
      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <div>
          <FloorPlan
            tables={tables}
            fill={(t) => sectorColor(t.sector, sectors)}
            stroke={(t) => (t.enabled && t.camera_ids.length === 0 ? '#f87171' : '#94a3b8')}
            selectedId={selectedId}
            onSelect={(t) => setSelectedId(t?.id ?? null)}
            onMove={(id, x, y) => {
              patchLocal(id, { plan_x: x, plan_y: y })
              setMoved((m) => new Set(m).add(id))
            }}
          />
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
            {sectors.map((s) => (
              <span key={s} className="flex items-center gap-1">
                <span className="inline-block h-3 w-3 rounded-sm" style={{ background: sectorColor(s, sectors) }} />
                {s}
              </span>
            ))}
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-sm border-2 border-red-400" /> sin cámara
            </span>
          </div>
        </div>
        <div className="space-y-4">
          <Card title="Nueva mesa">
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Field label="Etiqueta">
                  <input className={inputClass} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} placeholder="12" />
                </Field>
                <Field label="Sector">
                  <input className={inputClass} value={draft.sector} onChange={(e) => setDraft({ ...draft, sector: e.target.value })} list="sectors" />
                  <datalist id="sectors">
                    {[...new Set([...sectors, 'salon', 'galeria', 'barra'])].map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                </Field>
                <Field label="Lugares">
                  <input className={inputClass} type="number" min={1} value={draft.capacity} onChange={(e) => setDraft({ ...draft, capacity: e.target.value })} />
                </Field>
              </div>
              <Button onClick={addTable} disabled={!draft.label || !draft.sector}>
                Agregar mesa
              </Button>
            </div>
          </Card>
          {selected && (
            <Card
              title={`Mesa ${selected.label}`}
              actions={
                <Button variant="danger" onClick={deleteSelected}>
                  Borrar
                </Button>
              }
            >
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Etiqueta">
                    <input className={inputClass} value={selected.label} onChange={(e) => patchLocal(selected.id, { label: e.target.value })} />
                  </Field>
                  <Field label="Sector">
                    <input className={inputClass} value={selected.sector} list="sectors" onChange={(e) => patchLocal(selected.id, { sector: e.target.value })} />
                  </Field>
                  <Field label="Ancho">
                    <input className={inputClass} type="number" min={1} step={0.5} value={selected.plan_w} onChange={(e) => patchLocal(selected.id, { plan_w: Number(e.target.value) })} />
                  </Field>
                  <Field label="Alto">
                    <input className={inputClass} type="number" min={1} step={0.5} value={selected.plan_h} onChange={(e) => patchLocal(selected.id, { plan_h: Number(e.target.value) })} />
                  </Field>
                  <Field label="Rotación (°)">
                    <input className={inputClass} type="number" step={15} value={selected.plan_rotation} onChange={(e) => patchLocal(selected.id, { plan_rotation: Number(e.target.value) })} />
                  </Field>
                  <Field label="Lugares">
                    <input className={inputClass} type="number" min={1} value={selected.capacity ?? ''} onChange={(e) => patchLocal(selected.id, { capacity: e.target.value ? Number(e.target.value) : null })} />
                  </Field>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={selected.enabled} onChange={(e) => patchLocal(selected.id, { enabled: e.target.checked })} />
                  Habilitada
                </label>
                <div className="text-sm">
                  Cámaras:{' '}
                  {selected.camera_ids.length ? selected.camera_ids.map((c) => <Badge key={c} tone="sky">{c}</Badge>) : <Badge tone="red">ninguna</Badge>}
                </div>
                <Button onClick={saveSelected}>Guardar mesa</Button>
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  )
}

function nextLabel(label: string): string {
  const m = label.match(/^(.*?)(\d+)$/)
  return m ? `${m[1]}${Number(m[2]) + 1}` : label
}
