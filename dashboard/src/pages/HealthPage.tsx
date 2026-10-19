import { useEffect, useState } from 'react'
import { Badge, Card, ErrorBox, PageHeader } from '../components/ui'
import { apiJson } from '../lib/api'

type Health = {
  api_version: string
  worker: {
    model: string
    tiling: boolean
    open: boolean
    cameras: Record<string, { fps: number; latency_ms: number | null; people: number; frame_age_s: number | null; error: string | null }>
    gpu: { name: string; memory_used_gb: number; memory_total_gb: number } | null
  } | null
  worker_age_s: number | null
  worker_ok: boolean
  cameras: { camera_id: string; name: string; status: string; detail: string | null }[]
  host: { cpu_percent: number; memory_percent: number; memory_total_gb: number }
  websocket_clients: number
}

export function HealthPage() {
  const [h, setH] = useState<Health | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    const load = () => apiJson<Health>('/system/health').then(setH).catch(setError)
    load()
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [])
  const w = h?.worker
  return (
    <>
      <PageHeader title="Salud del sistema" subtitle="Se actualiza cada 5 segundos" />
      <ErrorBox error={error} />
      {h && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Procesamiento de video" actions={h.worker_ok ? <Badge tone="green">Funcionando</Badge> : <Badge tone="red">Detenido</Badge>}>
            {w ? (
              <dl className="grid grid-cols-2 gap-1 text-sm">
                <dt className="text-slate-400">Modelo</dt>
                <dd>RF-DETR {w.model} {w.tiling ? '(mosaicos)' : ''}</dd>
                <dt className="text-slate-400">Horario operativo</dt>
                <dd>{w.open ? 'Abierto' : 'Cerrado (en reposo)'}</dd>
                <dt className="text-slate-400">GPU</dt>
                <dd>{w.gpu ? `${w.gpu.name} · ${w.gpu.memory_used_gb}/${w.gpu.memory_total_gb} GB` : 'Sin GPU (CPU)'}</dd>
                <dt className="text-slate-400">Último reporte</dt>
                <dd>hace {h.worker_age_s} s</dd>
              </dl>
            ) : (
              <p className="text-sm text-slate-400">El worker todavía no reportó.</p>
            )}
          </Card>
          <Card title="Servidor">
            <dl className="grid grid-cols-2 gap-1 text-sm">
              <dt className="text-slate-400">Versión</dt>
              <dd>{h.api_version}</dd>
              <dt className="text-slate-400">CPU</dt>
              <dd>{h.host.cpu_percent}%</dd>
              <dt className="text-slate-400">Memoria</dt>
              <dd>{h.host.memory_percent}% de {h.host.memory_total_gb} GB</dd>
              <dt className="text-slate-400">Pantallas conectadas</dt>
              <dd>{h.websocket_clients}</dd>
            </dl>
          </Card>
          <Card title="Cámaras" className="lg:col-span-2">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-400">
                <tr>
                  {['Cámara', 'Estado', 'FPS real', 'Latencia', 'Personas', 'Último frame', 'Detalle'].map((x) => (
                    <th key={x} className="py-1 font-medium">
                      {x}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {h.cameras.map((c) => {
                  const s = w?.cameras[c.camera_id]
                  return (
                    <tr key={c.camera_id} className="border-t border-slate-800">
                      <td className="py-1">{c.name}</td>
                      <td>
                        <Badge tone={c.status === 'online' ? 'green' : c.status === 'degraded' ? 'amber' : 'red'}>{c.status}</Badge>
                      </td>
                      <td>{s?.fps ?? '—'}</td>
                      <td>{s?.latency_ms != null ? `${Math.round(s.latency_ms)} ms` : '—'}</td>
                      <td>{s?.people ?? '—'}</td>
                      <td>{s?.frame_age_s != null ? `hace ${s.frame_age_s} s` : '—'}</td>
                      <td className="text-xs text-slate-400">{s?.error ?? c.detail ?? ''}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        </div>
      )}
    </>
  )
}
