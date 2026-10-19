import { useCallback, useEffect, useState } from 'react'
import { AuthImage } from '../components/AuthImage'
import { Badge, Button, Card, ErrorBox, PageHeader } from '../components/ui'
import { apiJson } from '../lib/api'
import { formatTime } from '../lib/format'
import type { Camera, CameraStatus } from '../lib/types'

const STATUS: Record<CameraStatus, { label: string; tone: 'green' | 'amber' | 'red' }> = {
  online: { label: 'En línea', tone: 'green' },
  degraded: { label: 'Con problemas', tone: 'amber' },
  offline: { label: 'Sin señal', tone: 'red' },
}

export function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [snapshotKey, setSnapshotKey] = useState(() => Date.now())

  const load = useCallback(() => {
    apiJson<Camera[]>('/cameras')
      .then((c) => {
        setCameras(c)
        setError(null)
      })
      .catch(setError)
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 10_000)
    return () => clearInterval(id)
  }, [load])

  return (
    <>
      <PageHeader
        title="Cámaras"
        subtitle="Las cámaras se configuran en config/local/cameras.yaml"
        actions={
          <Button variant="secondary" onClick={() => setSnapshotKey(Date.now())}>
            Actualizar imágenes
          </Button>
        }
      />
      <ErrorBox error={error} />
      {cameras && cameras.length === 0 && <p className="text-slate-400">No hay cámaras configuradas.</p>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {cameras?.map((cam) => (
          <Card
            key={cam.id}
            title={
              <span className="flex items-center gap-2">
                {cam.name}
                <span className="text-xs text-slate-500">{cam.id}</span>
              </span>
            }
            actions={cam.enabled ? <Badge tone={STATUS[cam.status].tone}>{STATUS[cam.status].label}</Badge> : <Badge tone="slate">Deshabilitada</Badge>}
          >
            {cam.enabled ? (
              <AuthImage path={`/cameras/${cam.id}/snapshot?stream=main&_=${snapshotKey}`} alt={`Imagen de ${cam.name}`} className="aspect-video w-full rounded object-contain bg-black" />
            ) : (
              <div className="flex aspect-video items-center justify-center rounded bg-slate-900 text-sm text-slate-500">Deshabilitada</div>
            )}
            <dl className="mt-3 grid grid-cols-2 gap-x-2 gap-y-1 text-sm">
              <dt className="text-slate-400">Canal</dt>
              <dd>{cam.channel ?? '—'}</dd>
              <dt className="text-slate-400">Resolución (detección)</dt>
              <dd>
                {cam.resolution ? cam.resolution.join('×') : '—'}
                {cam.expected_resolution && <span className="text-slate-500"> (esperada {cam.expected_resolution.join('×')})</span>}
              </dd>
              <dt className="text-slate-400">FPS de detección</dt>
              <dd>{cam.fps_detection}</dd>
              <dt className="text-slate-400">Última imagen</dt>
              <dd>{formatTime(cam.last_ok_at)}</dd>
            </dl>
            {cam.status_detail && cam.enabled && <p className="mt-2 text-xs text-amber-300">{cam.status_detail}</p>}
          </Card>
        ))}
      </div>
    </>
  )
}
