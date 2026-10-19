import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { AuthImage } from '../components/AuthImage'
import { PolygonEditor, type Shape } from '../components/PolygonEditor'
import { Badge, Button, Card, ErrorBox, Field, PageHeader, inputClass } from '../components/ui'
import { apiJson } from '../lib/api'
import type { Camera, CameraZones, CleanMode, Point, Polygon, StaffZoneKind, Table } from '../lib/types'

type DrawTarget =
  | { type: 'view-table'; tableId: string }
  | { type: 'view-seats'; tableId: string; tablePolygon: Polygon }
  | { type: 'staff'; kind: StaffZoneKind; name: string }
  | { type: 'mask'; name: string }
  | { type: 'redraw'; key: string }

const COLORS = {
  table: { stroke: '#38bdf8', fill: 'rgba(56,189,248,0.18)' },
  seats: { stroke: '#34d399', fill: 'rgba(52,211,153,0.10)' },
  staff: { stroke: '#fbbf24', fill: 'rgba(251,191,36,0.18)' },
  mask: { stroke: '#f87171', fill: 'rgba(248,113,113,0.30)' },
}

const STAFF_KINDS: Record<StaffZoneKind, string> = {
  bar: 'Barra',
  kitchen_pass: 'Pase de cocina',
  cashier: 'Caja',
  other: 'Otra',
}

const DRAW_HELP: Record<DrawTarget['type'], string> = {
  'view-table': 'Dibujá la SUPERFICIE de la mesa: hacé clic en cada esquina y cerrá clickeando el primer punto (o doble clic).',
  'view-seats': 'Ahora dibujá el área de ASIENTOS alrededor de la mesa (donde se sientan los clientes).',
  staff: 'Dibujá la zona de personal (barra, pase, caja).',
  mask: 'Dibujá el área a ignorar (vidrieras, pasillo exterior, fecha en pantalla).',
  redraw: 'Redibujá el polígono seleccionado.',
}

const emptyZones = (cameraId: string): CameraZones => ({ camera_id: cameraId, table_views: [], staff_zones: [], ignore_masks: [] })

export function ZonesPage() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [tables, setTables] = useState<Table[]>([])
  const [cameraId, setCameraId] = useState<string | null>(null)
  const [zones, setZones] = useState<CameraZones | null>(null)
  const [dirty, setDirty] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [draw, setDraw] = useState<{ target: DrawTarget; points: Polygon } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  const [newTableId, setNewTableId] = useState('')

  const loadTables = useCallback(() => apiJson<Table[]>('/tables').then(setTables), [])

  useEffect(() => {
    Promise.all([apiJson<Camera[]>('/cameras'), loadTables()])
      .then(([cams]) => {
        const enabled = cams.filter((c) => c.enabled)
        setCameras(enabled)
        setCameraId((cur) => cur ?? enabled[0]?.id ?? null)
      })
      .catch(setError)
  }, [loadTables])

  useEffect(() => {
    if (!cameraId) return
    let cancelled = false
    apiJson<CameraZones>(`/zones/${cameraId}`)
      .then((z) => {
        if (cancelled) return
        setZones(z)
        setDirty(false)
        setSelected(null)
        setDraw(null)
      })
      .catch(setError)
    return () => {
      cancelled = true
    }
  }, [cameraId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDraw(null)
      if (e.key === 'Enter' && draw && draw.points.length >= 3) finishDrawing()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const z = zones ?? emptyZones(cameraId ?? '')
  const tableById = useMemo(() => new Map(tables.map((t) => [t.id, t])), [tables])

  const pending: Shape[] =
    draw?.target.type === 'view-seats' ? [{ key: 'pending:table', points: draw.target.tablePolygon, ...COLORS.table }] : []
  const shapes: Shape[] = [
    ...pending,
    ...z.ignore_masks.map((m, i) => ({ key: `mask:${i}`, points: m.polygon, ...COLORS.mask, label: m.name })),
    ...z.staff_zones.map((s, i) => ({ key: `staff:${i}`, points: s.polygon, ...COLORS.staff, label: s.name })),
    ...z.table_views.flatMap((v, i) => [
      { key: `view:${i}:seats`, points: v.seats_polygon, ...COLORS.seats, dashed: true },
      { key: `view:${i}:table`, points: v.table_polygon, ...COLORS.table, label: tableById.get(v.table_id)?.label },
    ]),
  ]

  function update(fn: (draft: CameraZones) => CameraZones) {
    setZones((cur) => fn(cur ?? emptyZones(cameraId ?? '')))
    setDirty(true)
  }

  function getPolygon(key: string): Polygon | null {
    const [kind, idx, part] = key.split(':')
    const i = Number(idx)
    if (kind === 'mask') return z.ignore_masks[i]?.polygon ?? null
    if (kind === 'staff') return z.staff_zones[i]?.polygon ?? null
    const v = z.table_views[i]
    if (!v) return null
    return part === 'seats' ? v.seats_polygon : v.table_polygon
  }

  function setPolygon(draft: CameraZones, key: string, poly: Polygon): CameraZones {
    const [kind, idx, part] = key.split(':')
    const i = Number(idx)
    if (kind === 'mask') return { ...draft, ignore_masks: draft.ignore_masks.map((m, j) => (j === i ? { ...m, polygon: poly } : m)) }
    if (kind === 'staff') return { ...draft, staff_zones: draft.staff_zones.map((s, j) => (j === i ? { ...s, polygon: poly } : s)) }
    return {
      ...draft,
      table_views: draft.table_views.map((v, j) => (j === i ? (part === 'seats' ? { ...v, seats_polygon: poly } : { ...v, table_polygon: poly }) : v)),
    }
  }

  function finishDrawing() {
    if (!draw || draw.points.length < 3) return
    const poly = draw.points
    const t = draw.target
    if (t.type === 'view-table') {
      setDraw({ target: { type: 'view-seats', tableId: t.tableId, tablePolygon: poly }, points: [] })
      return
    }
    if (t.type === 'view-seats') {
      update((d) => ({
        ...d,
        table_views: [...d.table_views, { table_id: t.tableId, table_polygon: t.tablePolygon, seats_polygon: poly, priority: 1, clean_mode: 'objects_and_staff' }],
      }))
      setSelected(`view:${z.table_views.length}:table`)
    } else if (t.type === 'staff') {
      update((d) => ({ ...d, staff_zones: [...d.staff_zones, { kind: t.kind, name: t.name, polygon: poly }] }))
    } else if (t.type === 'mask') {
      update((d) => ({ ...d, ignore_masks: [...d.ignore_masks, { name: t.name, polygon: poly }] }))
    } else {
      update((d) => setPolygon(d, t.key, poly))
    }
    setDraw(null)
  }

  function removeSelected() {
    if (!selected) return
    const [kind, idx] = selected.split(':')
    const i = Number(idx)
    update((d) => ({
      ...d,
      table_views: kind === 'view' ? d.table_views.filter((_, j) => j !== i) : d.table_views,
      staff_zones: kind === 'staff' ? d.staff_zones.filter((_, j) => j !== i) : d.staff_zones,
      ignore_masks: kind === 'mask' ? d.ignore_masks.filter((_, j) => j !== i) : d.ignore_masks,
    }))
    setSelected(null)
  }

  async function save() {
    if (!cameraId || !zones) return
    setSaving(true)
    setError(null)
    try {
      const saved = await apiJson<CameraZones>(`/zones/${cameraId}`, {
        method: 'PUT',
        body: JSON.stringify({ table_views: zones.table_views, staff_zones: zones.staff_zones, ignore_masks: zones.ignore_masks }),
      })
      setZones(saved)
      setDirty(false)
      await loadTables()
    } catch (err) {
      setError(err)
    } finally {
      setSaving(false)
    }
  }

  function switchCamera(id: string) {
    if (dirty && !window.confirm('Hay cambios sin guardar en esta cámara. ¿Descartarlos?')) return
    setCameraId(id)
  }

  const usedHere = new Set(z.table_views.map((v) => v.table_id))
  const available = tables.filter((t) => !usedHere.has(t.id))
  const withoutView = tables.filter((t) => t.enabled && t.camera_ids.length === 0)
  const [selKind, selIdx] = selected?.split(':') ?? []
  const selI = Number(selIdx)

  return (
    <>
      <PageHeader
        title="Zonas por cámara"
        subtitle="Dibujá sobre la imagen las mesas, sus asientos, las zonas de personal y las áreas a ignorar"
        actions={
          <>
            {dirty && <Badge tone="amber">Cambios sin guardar</Badge>}
            <Button onClick={save} disabled={!dirty || saving}>
              {saving ? 'Guardando…' : 'Guardar'}
            </Button>
          </>
        }
      />
      <ErrorBox error={error} />
      <div className="mb-3 flex flex-wrap gap-1">
        {cameras.map((c) => (
          <button
            key={c.id}
            onClick={() => switchCamera(c.id)}
            className={`rounded-md px-3 py-1 text-sm ${c.id === cameraId ? 'bg-sky-700 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
          >
            {c.name}
          </button>
        ))}
        {cameras.length === 0 && <p className="text-slate-400">No hay cámaras habilitadas.</p>}
      </div>

      {cameraId && (
        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <div>
            {/* Siempre visible (aunque no se esté dibujando) para que la imagen no salte. */}
            <div
              className={`mb-2 flex min-h-12 items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm ${draw ? 'border-sky-700 bg-sky-950/60' : 'border-slate-800 text-slate-400'}`}
            >
              <span>{draw ? DRAW_HELP[draw.target.type] : 'Elegí en el panel de la derecha qué querés agregar, o tocá una forma para editarla.'}</span>
              {draw && (
                <span className="flex shrink-0 gap-2">
                  <Button variant="secondary" onClick={finishDrawing} disabled={draw.points.length < 3}>
                    Terminar
                  </Button>
                  <Button variant="ghost" onClick={() => setDraw(null)}>
                    Cancelar
                  </Button>
                </span>
              )}
            </div>
            <div className="flex justify-center rounded-md border border-slate-800 bg-black">
             <div className="relative inline-block select-none">
              <AuthImage
                path={`/cameras/${cameraId}/snapshot?stream=main`}
                alt="Imagen de la cámara"
                className="block max-h-[calc(100vh-14rem)] min-h-48 max-w-full"
              />
              <PolygonEditor
                shapes={shapes}
                selectedKey={selected}
                drawing={draw ? { points: draw.points, stroke: draw.target.type === 'view-seats' ? COLORS.seats.stroke : draw.target.type === 'staff' ? COLORS.staff.stroke : draw.target.type === 'mask' ? COLORS.mask.stroke : COLORS.table.stroke } : null}
                onSelect={setSelected}
                onAddPoint={(p: Point) => setDraw((d) => (d ? { ...d, points: [...d.points, p] } : d))}
                onFinishDrawing={finishDrawing}
                onMovePoint={(key, index, p) => {
                  const poly = getPolygon(key)
                  if (poly) update((d) => setPolygon(d, key, poly.map((q, j) => (j === index ? p : q))))
                }}
                onDeletePoint={(key, index) => {
                  const poly = getPolygon(key)
                  if (poly) update((d) => setPolygon(d, key, poly.filter((_, j) => j !== index)))
                }}
              />
             </div>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              Seleccioná una forma para mover sus puntos. Clic derecho sobre un punto lo borra. Las coordenadas se guardan en proporción, así sirven para el stream de alta y de baja resolución.
            </p>
          </div>

          <div className="space-y-4">
            <Card title="Agregar">
              <div className="space-y-3">
                <div className="flex gap-2">
                  <select className={inputClass} value={newTableId} onChange={(e) => setNewTableId(e.target.value)}>
                    <option value="">Elegí una mesa…</option>
                    {available.map((t) => (
                      <option key={t.id} value={t.id}>
                        Mesa {t.label} ({t.sector})
                      </option>
                    ))}
                  </select>
                  <Button
                    disabled={!newTableId || !!draw}
                    onClick={() => {
                      setDraw({ target: { type: 'view-table', tableId: newTableId }, points: [] })
                      setNewTableId('')
                    }}
                  >
                    Dibujar
                  </Button>
                </div>
                {tables.length === 0 && (
                  <p className="text-xs text-slate-400">
                    Primero creá las mesas en <Link className="text-sky-400 underline" to="/plano">Plano del salón</Link>.
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" disabled={!!draw} onClick={() => setDraw({ target: { type: 'staff', kind: 'bar', name: 'Barra' }, points: [] })}>
                    + Zona de personal
                  </Button>
                  <Button variant="secondary" disabled={!!draw} onClick={() => setDraw({ target: { type: 'mask', name: 'Ignorar' }, points: [] })}>
                    + Área a ignorar
                  </Button>
                </div>
              </div>
            </Card>

            {selected && (
              <Card
                title="Selección"
                actions={
                  <>
                    <Button variant="ghost" onClick={() => setDraw({ target: { type: 'redraw', key: selected }, points: [] })}>
                      Redibujar
                    </Button>
                    <Button variant="danger" onClick={removeSelected}>
                      Borrar
                    </Button>
                  </>
                }
              >
                {selKind === 'view' && z.table_views[selI] && (
                  <div className="space-y-3">
                    <p className="text-sm">Mesa {tableById.get(z.table_views[selI].table_id)?.label}</p>
                    <Field label="Prioridad" hint="Si la mesa se ve en varias cámaras, se usa la de mayor prioridad que esté funcionando.">
                      <input
                        type="number"
                        min={0}
                        max={100}
                        className={inputClass}
                        value={z.table_views[selI].priority}
                        onChange={(e) => update((d) => ({ ...d, table_views: d.table_views.map((v, j) => (j === selI ? { ...v, priority: Number(e.target.value) } : v)) }))}
                      />
                    </Field>
                    <Field label="Cómo se detecta que quedó limpia" hint="Si desde este ángulo no se ven bien vasos y platos, usá solo la visita del personal.">
                      <select
                        className={inputClass}
                        value={z.table_views[selI].clean_mode}
                        onChange={(e) => update((d) => ({ ...d, table_views: d.table_views.map((v, j) => (j === selI ? { ...v, clean_mode: e.target.value as CleanMode } : v)) }))}
                      >
                        <option value="objects_and_staff">Visita del personal y sin objetos</option>
                        <option value="staff_visit_only">Solo visita del personal</option>
                      </select>
                    </Field>
                  </div>
                )}
                {selKind === 'staff' && z.staff_zones[selI] && (
                  <div className="space-y-3">
                    <Field label="Nombre">
                      <input className={inputClass} value={z.staff_zones[selI].name} onChange={(e) => update((d) => ({ ...d, staff_zones: d.staff_zones.map((s, j) => (j === selI ? { ...s, name: e.target.value } : s)) }))} />
                    </Field>
                    <Field label="Tipo">
                      <select className={inputClass} value={z.staff_zones[selI].kind} onChange={(e) => update((d) => ({ ...d, staff_zones: d.staff_zones.map((s, j) => (j === selI ? { ...s, kind: e.target.value as StaffZoneKind } : s)) }))}>
                        {Object.entries(STAFF_KINDS).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>
                )}
                {selKind === 'mask' && z.ignore_masks[selI] && (
                  <Field label="Nombre">
                    <input className={inputClass} value={z.ignore_masks[selI].name} onChange={(e) => update((d) => ({ ...d, ignore_masks: d.ignore_masks.map((m, j) => (j === selI ? { ...m, name: e.target.value } : m)) }))} />
                  </Field>
                )}
              </Card>
            )}

            <Card title="En esta cámara">
              <ul className="space-y-1 text-sm">
                {z.table_views.map((v, i) => (
                  <li key={`v${i}`}>
                    <button className={`hover:underline ${selected?.startsWith(`view:${i}:`) ? 'text-sky-300' : ''}`} onClick={() => setSelected(`view:${i}:table`)}>
                      <span className="mr-2 inline-block h-2 w-2 rounded-full bg-sky-400" />
                      Mesa {tableById.get(v.table_id)?.label ?? '?'} · prioridad {v.priority}
                    </button>
                  </li>
                ))}
                {z.staff_zones.map((s, i) => (
                  <li key={`s${i}`}>
                    <button className="hover:underline" onClick={() => setSelected(`staff:${i}`)}>
                      <span className="mr-2 inline-block h-2 w-2 rounded-full bg-amber-400" />
                      {s.name} ({STAFF_KINDS[s.kind]})
                    </button>
                  </li>
                ))}
                {z.ignore_masks.map((m, i) => (
                  <li key={`m${i}`}>
                    <button className="hover:underline" onClick={() => setSelected(`mask:${i}`)}>
                      <span className="mr-2 inline-block h-2 w-2 rounded-full bg-red-400" />
                      {m.name}
                    </button>
                  </li>
                ))}
                {shapes.length === 0 && <li className="text-slate-500">Nada dibujado todavía.</li>}
              </ul>
            </Card>

            <Card title="Mesas sin ninguna cámara">
              {withoutView.length === 0 ? (
                <p className="text-sm text-emerald-400">Todas las mesas habilitadas tienen al menos una vista.</p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {withoutView.map((t) => (
                    <Badge key={t.id} tone="red">
                      {t.label}
                    </Badge>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </div>
      )}
    </>
  )
}
