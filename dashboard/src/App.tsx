import { useEffect, useState } from 'react'
import { fetchHealth, type Health } from './api'

type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok'; health: Health }
  | { kind: 'error'; message: string }

function App() {
  const [state, setState] = useState<HealthState>({ kind: 'loading' })

  useEffect(() => {
    const ctrl = new AbortController()
    fetchHealth(ctrl.signal)
      .then((health) => setState({ kind: 'ok', health }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return
        setState({ kind: 'error', message: err instanceof Error ? err.message : String(err) })
      })
    return () => ctrl.abort()
  }, [])

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <div className="max-w-md w-full space-y-4">
        <h1 className="text-3xl font-semibold">Mesa Monitor</h1>
        <p className="text-slate-400">Panel en construcción.</p>
        <div className="rounded-lg border border-slate-800 p-4">
          {state.kind === 'loading' && <p>Consultando el servidor…</p>}
          {state.kind === 'error' && (
            <p className="text-red-400">No se pudo contactar al servidor: {state.message}</p>
          )}
          {state.kind === 'ok' && (
            <dl className="grid grid-cols-2 gap-y-1">
              <dt className="text-slate-400">Servidor</dt>
              <dd className={state.health.status === 'ok' ? 'text-emerald-400' : 'text-amber-400'}>
                {state.health.status === 'ok' ? 'Funcionando' : 'Con problemas'}
              </dd>
              <dt className="text-slate-400">Base de datos</dt>
              <dd className={state.health.database === 'ok' ? 'text-emerald-400' : 'text-red-400'}>
                {state.health.database === 'ok' ? 'Conectada' : 'Sin conexión'}
              </dd>
              <dt className="text-slate-400">Versión</dt>
              <dd>{state.health.version}</dd>
            </dl>
          )}
        </div>
      </div>
    </main>
  )
}

export default App
