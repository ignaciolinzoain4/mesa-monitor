import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router'
import { Button, ErrorBox, Field, inputClass } from '../components/ui'
import { useAuth } from '../lib/auth'

export function LoginPage() {
  const { user, login } = useAuth()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  if (user) {
    const from = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={from} replace />
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 p-4 text-slate-100">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-slate-800 bg-slate-900 p-6">
        <h1 className="text-2xl font-semibold">Mesa Monitor</h1>
        <Field label="Usuario">
          <input className={inputClass} value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
        </Field>
        <Field label="Contraseña">
          <input className={inputClass} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" disabled={busy || !username || !password} className="w-full">
          {busy ? 'Ingresando…' : 'Ingresar'}
        </Button>
      </form>
    </main>
  )
}
