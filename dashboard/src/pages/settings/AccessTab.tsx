import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, ErrorBox, Field, inputClass } from '../../components/ui'
import { apiFetch, apiJson } from '../../lib/api'
import { formatDateTime } from '../../lib/format'

type UserT = { id: string; username: string; role: 'admin' | 'viewer'; active: boolean; last_login_at: string | null }
type Key = { id: string; name: string; prefix: string; role: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
type Device = { id: string; name: string; has_pin: boolean; created_at: string; last_seen_at: string | null; revoked_at: string | null }

function Secret({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-amber-600 bg-amber-950/40 p-3 text-sm">
      <p className="font-medium text-amber-300">{title}</p>
      <div className="mt-1 break-all font-mono">{children}</div>
    </div>
  )
}

export function AccessTab() {
  const [users, setUsers] = useState<UserT[]>([])
  const [keys, setKeys] = useState<Key[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [error, setError] = useState<unknown>(null)
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'viewer' })
  const [keyName, setKeyName] = useState('Sistema de gestión')
  const [devName, setDevName] = useState('PC caja')
  const [shownKey, setShownKey] = useState<string | null>(null)
  const [shownDev, setShownDev] = useState<{ link: string; pin: string | null } | null>(null)

  const load = useCallback(() => {
    apiJson<UserT[]>('/users').then(setUsers).catch(setError)
    apiJson<Key[]>('/api-keys').then(setKeys).catch(setError)
    apiJson<Device[]>('/device-tokens').then(setDevices).catch(setError)
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const run = (p: Promise<unknown>) => p.then(load).catch(setError)

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ErrorBox error={error} />
      <Card title="Usuarios del dashboard" className="xl:col-span-2">
        <table className="mb-3 w-full text-sm">
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-slate-800">
                <td className="py-1.5 font-medium">{u.username}</td>
                <td>
                  <select className={`${inputClass} w-28`} value={u.role} onChange={(e) => run(apiJson(`/users/${u.id}`, { method: 'PATCH', body: JSON.stringify({ role: e.target.value }) }))}>
                    <option value="admin">Admin</option>
                    <option value="viewer">Lectura</option>
                  </select>
                </td>
                <td>{u.active ? <Badge tone="green">Activo</Badge> : <Badge tone="slate">Inactivo</Badge>}</td>
                <td className="text-slate-500">Último ingreso: {formatDateTime(u.last_login_at)}</td>
                <td className="text-right">
                  <Button variant="ghost" onClick={() => run(apiJson(`/users/${u.id}`, { method: 'PATCH', body: JSON.stringify({ active: !u.active }) }))}>
                    {u.active ? 'Desactivar' : 'Activar'}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      const pw = window.prompt(`Nueva contraseña para ${u.username} (mínimo 8 caracteres)`)
                      if (pw) run(apiJson(`/users/${u.id}`, { method: 'PATCH', body: JSON.stringify({ password: pw }) }))
                    }}
                  >
                    Cambiar contraseña
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Usuario">
            <input className={inputClass} value={newUser.username} onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} />
          </Field>
          <Field label="Contraseña">
            <input type="password" className={inputClass} value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
          </Field>
          <Field label="Rol">
            <select className={inputClass} value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
              <option value="viewer">Lectura (ve y marca alertas)</option>
              <option value="admin">Admin (todo)</option>
            </select>
          </Field>
          <Button onClick={() => run(apiJson('/users', { method: 'POST', body: JSON.stringify(newUser) }).then(() => setNewUser({ username: '', password: '', role: 'viewer' })))}>Crear usuario</Button>
        </div>
      </Card>

      <Card title="API keys (sistema de gestión)">
        <ul className="mb-3 space-y-1 text-sm">
          {keys.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{k.name}</span>
              <span className="font-mono text-slate-500">{k.prefix}…</span>
              {k.revoked_at ? <Badge tone="slate">Revocada</Badge> : <Badge tone="green">Activa</Badge>}
              <span className="text-slate-500">uso: {formatDateTime(k.last_used_at)}</span>
              {!k.revoked_at && (
                <Button variant="ghost" onClick={() => window.confirm('¿Revocar esta API key?') && run(apiFetch(`/api-keys/${k.id}`, { method: 'DELETE' }))}>
                  Revocar
                </Button>
              )}
            </li>
          ))}
        </ul>
        <div className="flex items-end gap-2">
          <Field label="Nombre">
            <input className={inputClass} value={keyName} onChange={(e) => setKeyName(e.target.value)} />
          </Field>
          <Button onClick={() => run(apiJson<{ key: string }>('/api-keys', { method: 'POST', body: JSON.stringify({ name: keyName }) }).then((r) => setShownKey(r.key)))}>Crear API key</Button>
        </div>
        {shownKey && <Secret title="Copiala ahora: no se vuelve a mostrar. Va en el .env del sistema de gestión.">{shownKey}</Secret>}
      </Card>

      <Card title="Dispositivos (pantalla /alertas)">
        <ul className="mb-3 space-y-1 text-sm">
          {devices.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{d.name}</span>
              {d.revoked_at ? <Badge tone="slate">Revocado</Badge> : <Badge tone="green">Activo</Badge>}
              <span className="text-slate-500">visto: {formatDateTime(d.last_seen_at)}</span>
              {!d.revoked_at && (
                <Button variant="ghost" onClick={() => window.confirm('¿Revocar este dispositivo?') && run(apiFetch(`/device-tokens/${d.id}`, { method: 'DELETE' }))}>
                  Revocar
                </Button>
              )}
            </li>
          ))}
        </ul>
        <div className="flex items-end gap-2">
          <Field label="Nombre de la PC">
            <input className={inputClass} value={devName} onChange={(e) => setDevName(e.target.value)} />
          </Field>
          <Button
            onClick={() =>
              run(
                apiJson<{ link_path: string; pin: string | null }>('/device-tokens', { method: 'POST', body: JSON.stringify({ name: devName }) }).then((r) =>
                  setShownDev({ link: `${location.origin}${r.link_path}`, pin: r.pin }),
                ),
              )
            }
          >
            Crear dispositivo
          </Button>
        </div>
        {shownDev && (
          <Secret title="Abrí este link en la PC de tiqueo (o entrá a /alertas y escribí el PIN). Se muestra una sola vez.">
            <p>{shownDev.link}</p>
            {shownDev.pin && <p className="mt-2 text-2xl tracking-widest">PIN: {shownDev.pin}</p>}
          </Secret>
        )}
      </Card>
    </div>
  )
}
