import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, ErrorBox, Field, inputClass } from '../../components/ui'
import { apiFetch, apiJson } from '../../lib/api'
import { formatDateTime } from '../../lib/format'

type Hook = { id: string; name: string; url: string; event_types: string[]; enabled: boolean }
type Delivery = { id: number; event_type: string; status: string; attempts: number; last_status_code: number | null; last_error: string | null; created_at: string }

export function WebhooksTab() {
  const [hooks, setHooks] = useState<Hook[]>([])
  const [types, setTypes] = useState<string[]>([])
  const [draft, setDraft] = useState({ name: '', url: 'https://', event_types: ['alert.opened'] })
  const [secret, setSecret] = useState<string | null>(null)
  const [deliveries, setDeliveries] = useState<{ id: string; rows: Delivery[] } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(() => apiJson<Hook[]>('/webhooks').then(setHooks).catch(setError), [])
  useEffect(() => {
    load()
    apiJson<string[]>('/webhooks/event-types').then(setTypes).catch(setError)
  }, [load])

  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Webhooks salientes">
        <p className="mb-3 text-sm text-slate-400">El sistema avisa a otra aplicación (POST con firma HMAC-SHA256 en el header X-Mesa-Signature) cuando pasa algo. Reintenta solo si falla.</p>
        <ul className="space-y-2 text-sm">
          {hooks.map((h) => (
            <li key={h.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{h.name}</span>
              <span className="text-slate-400">{h.url}</span>
              {h.enabled ? <Badge tone="green">Activo</Badge> : <Badge tone="slate">Pausado</Badge>}
              <span className="text-slate-500">{h.event_types.join(', ')}</span>
              <Button variant="ghost" onClick={() => apiJson(`/webhooks/${h.id}/test`, { method: 'POST' }).catch(setError)}>
                Probar
              </Button>
              <Button variant="ghost" onClick={() => apiJson<Delivery[]>(`/webhooks/${h.id}/deliveries`).then((rows) => setDeliveries({ id: h.id, rows })).catch(setError)}>
                Entregas
              </Button>
              <Button variant="ghost" onClick={() => window.confirm('¿Borrar el webhook?') && apiFetch(`/webhooks/${h.id}`, { method: 'DELETE' }).then(load).catch(setError)}>
                Borrar
              </Button>
            </li>
          ))}
        </ul>
        {deliveries && (
          <table className="mt-3 w-full text-xs">
            <tbody>
              {deliveries.rows.map((d) => (
                <tr key={d.id} className="border-t border-slate-800">
                  <td className="py-1">{formatDateTime(d.created_at)}</td>
                  <td>{d.event_type}</td>
                  <td>
                    <Badge tone={d.status === 'success' ? 'green' : d.status === 'failed' ? 'red' : 'amber'}>{d.status}</Badge>
                  </td>
                  <td>{d.attempts} intentos</td>
                  <td className="text-slate-400">{d.last_status_code ?? ''} {d.last_error ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Nuevo webhook">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Nombre">
            <input className={inputClass} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </Field>
          <Field label="URL">
            <input className={inputClass} value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap gap-3 text-sm">
          {types.map((t) => (
            <label key={t} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={draft.event_types.includes(t)}
                onChange={(e) => setDraft({ ...draft, event_types: e.target.checked ? [...draft.event_types, t] : draft.event_types.filter((x) => x !== t) })}
              />
              {t}
            </label>
          ))}
        </div>
        <Button
          className="mt-3"
          onClick={() =>
            apiJson<{ secret: string }>('/webhooks', { method: 'POST', body: JSON.stringify(draft) })
              .then((r) => {
                setSecret(r.secret)
                load()
              })
              .catch(setError)
          }
        >
          Crear
        </Button>
        {secret && (
          <div className="mt-3 rounded-md border border-amber-600 bg-amber-950/40 p-3 text-sm">
            <p className="text-amber-300">Secreto para verificar la firma (se muestra una sola vez):</p>
            <p className="break-all font-mono">{secret}</p>
          </div>
        )}
      </Card>
    </div>
  )
}
