import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, ErrorBox, Field, inputClass } from '../../components/ui'
import { apiFetch, apiJson } from '../../lib/api'
import { formatDateTime } from '../../lib/format'
import type { Camera } from '../../lib/types'

type Box = { kind: 'staff' | 'customer'; b: [number, number, number, number] }
type Profile = { id: string; name: string; active: boolean; quality: string | null; separation: number | null; created_at: string; created_by: string | null }
type Calibration = Profile & { staff_scores: number[]; customer_scores: number[]; advice: string }

export function StaffTab() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [cam, setCam] = useState('')
  const [snap, setSnap] = useState<{ url: string; id: string } | null>(null)
  const [boxes, setBoxes] = useState<Box[]>([])
  const [mode, setMode] = useState<Box['kind']>('staff')
  const [drag, setDrag] = useState<{ x: number; y: number; x2: number; y2: number } | null>(null)
  const [result, setResult] = useState<Calibration | null>(null)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [name, setName] = useState('Ropa negra')
  const [error, setError] = useState<unknown>(null)
  const svg = useRef<SVGSVGElement>(null)

  const loadProfiles = () => apiJson<Profile[]>('/staff-profiles').then(setProfiles).catch(setError)
  useEffect(() => {
    apiJson<Camera[]>('/cameras')
      .then((c) => {
        const en = c.filter((x) => x.enabled)
        setCameras(en)
        setCam(en[0]?.id ?? '')
      })
      .catch(setError)
    loadProfiles()
  }, [])

  async function takeSnapshot() {
    setError(null)
    setResult(null)
    setBoxes([])
    try {
      const r = await apiFetch(`/cameras/${cam}/snapshot?stream=main`)
      const id = r.headers.get('X-Snapshot-Id') ?? ''
      if (snap) URL.revokeObjectURL(snap.url)
      setSnap({ url: URL.createObjectURL(await r.blob()), id })
    } catch (err) {
      setError(err)
    }
  }

  const pt = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect()
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) }
  }

  async function calibrate(activate: boolean) {
    if (!snap) return
    setError(null)
    try {
      const r = await apiJson<Calibration>('/staff-profiles/calibrate', {
        method: 'POST',
        body: JSON.stringify({
          snapshot_id: snap.id,
          name,
          activate,
          staff_boxes: boxes.filter((b) => b.kind === 'staff').map((b) => b.b),
          customer_boxes: boxes.filter((b) => b.kind === 'customer').map((b) => b.b),
        }),
      })
      setResult(r)
      loadProfiles()
    } catch (err) {
      setError(err)
    }
  }

  const nStaff = boxes.filter((b) => b.kind === 'staff').length
  const nCust = boxes.length - nStaff

  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Calibrar el perfil de personal">
        <p className="mb-3 text-sm text-slate-400">
          Tomá una imagen con varios mozos y clientes a la vista. Marcá con un rectángulo a 3–10 personas del personal y 3–10 clientes (de cuerpo entero o torso). El sistema aprende el color de la ropa del personal. No se identifica a nadie: solo se guarda un histograma de colores promedio.
        </p>
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <Field label="Cámara">
            <select className={inputClass} value={cam} onChange={(e) => setCam(e.target.value)}>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Button onClick={takeSnapshot} disabled={!cam}>
            Tomar imagen
          </Button>
          <span className="mx-2" />
          <Button variant={mode === 'staff' ? 'primary' : 'secondary'} onClick={() => setMode('staff')}>
            Marcar personal ({nStaff})
          </Button>
          <Button variant={mode === 'customer' ? 'primary' : 'secondary'} onClick={() => setMode('customer')}>
            Marcar clientes ({nCust})
          </Button>
          <Button variant="ghost" onClick={() => setBoxes([])}>
            Borrar marcas
          </Button>
        </div>
        {snap && (
          <div className="flex justify-center bg-black">
            <div className="relative inline-block select-none">
              <img src={snap.url} alt="Imagen para calibrar" className="block max-h-[70vh] max-w-full" />
              <svg
                ref={svg}
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
                className="absolute inset-0 h-full w-full cursor-crosshair"
                onPointerDown={(e) => {
                  const p = pt(e)
                  setDrag({ x: p.x, y: p.y, x2: p.x, y2: p.y })
                }}
                onPointerMove={(e) => drag && setDrag({ ...drag, x2: pt(e).x, y2: pt(e).y })}
                onPointerUp={() => {
                  if (drag && Math.abs(drag.x2 - drag.x) > 0.01 && Math.abs(drag.y2 - drag.y) > 0.02) {
                    const b: Box['b'] = [Math.min(drag.x, drag.x2), Math.min(drag.y, drag.y2), Math.max(drag.x, drag.x2), Math.max(drag.y, drag.y2)]
                    setBoxes((c) => [...c, { kind: mode, b }])
                  }
                  setDrag(null)
                }}
              >
                {boxes.map((x, i) => (
                  <rect
                    key={i}
                    x={x.b[0]}
                    y={x.b[1]}
                    width={x.b[2] - x.b[0]}
                    height={x.b[3] - x.b[1]}
                    fill="none"
                    stroke={x.kind === 'staff' ? '#facc15' : '#38bdf8'}
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                    onPointerDown={(e) => {
                      e.stopPropagation()
                      setBoxes((c) => c.filter((_, j) => j !== i))
                    }}
                  />
                ))}
                {drag && (
                  <rect
                    x={Math.min(drag.x, drag.x2)}
                    y={Math.min(drag.y, drag.y2)}
                    width={Math.abs(drag.x2 - drag.x)}
                    height={Math.abs(drag.y2 - drag.y)}
                    fill="none"
                    stroke="white"
                    strokeDasharray="4 3"
                    vectorEffect="non-scaling-stroke"
                  />
                )}
              </svg>
            </div>
          </div>
        )}
        <p className="mt-2 text-xs text-slate-500">Amarillo = personal, celeste = clientes. Tocá un rectángulo para borrarlo.</p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Field label="Nombre del perfil">
            <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Button disabled={!snap || nStaff < 2 || nCust < 2} onClick={() => calibrate(false)}>
            Calcular
          </Button>
          <Button variant="secondary" disabled={!snap || nStaff < 2 || nCust < 2} onClick={() => calibrate(true)}>
            Calcular y activar
          </Button>
        </div>
        {result && (
          <div className="mt-4 rounded-md border border-slate-700 p-3 text-sm">
            <p>
              Calidad:{' '}
              <Badge tone={result.quality === 'buena' ? 'green' : result.quality === 'regular' ? 'amber' : 'red'}>{result.quality}</Badge> (separación {result.separation})
            </p>
            <p className="mt-1">{result.advice}</p>
            <p className="mt-1 text-slate-400">
              Personal: {result.staff_scores.join(', ')} · Clientes: {result.customer_scores.join(', ')}
            </p>
          </div>
        )}
      </Card>
      <Card title="Perfiles guardados">
        <ul className="space-y-2 text-sm">
          {profiles.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{p.name}</span>
              {p.active && <Badge tone="green">Activo</Badge>}
              {p.quality && <Badge tone={p.quality === 'buena' ? 'green' : p.quality === 'regular' ? 'amber' : 'red'}>{p.quality}</Badge>}
              <span className="text-slate-500">{formatDateTime(p.created_at)} · {p.created_by}</span>
              {!p.active && (
                <Button variant="secondary" onClick={() => apiJson(`/staff-profiles/${p.id}/activate`, { method: 'POST' }).then(loadProfiles).catch(setError)}>
                  Activar
                </Button>
              )}
              <Button variant="ghost" onClick={() => apiFetch(`/staff-profiles/${p.id}`, { method: 'DELETE' }).then(loadProfiles).catch(setError)}>
                Borrar
              </Button>
            </li>
          ))}
          {profiles.length === 0 && <li className="text-slate-500">Todavía no hay perfiles: el sistema usa solo postura y recorridos.</li>}
        </ul>
      </Card>
    </div>
  )
}
