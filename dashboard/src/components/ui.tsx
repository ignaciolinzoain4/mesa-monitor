import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }) {
  const styles = {
    primary: 'bg-sky-600 hover:bg-sky-500 text-white',
    secondary: 'bg-slate-700 hover:bg-slate-600 text-slate-100',
    danger: 'bg-red-700 hover:bg-red-600 text-white',
    ghost: 'hover:bg-slate-800 text-slate-300',
  }[variant]
  return (
    <button
      className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    />
  )
}

export function Card({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-slate-800 bg-slate-900/60 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-800 px-4 py-2">
          <h2 className="font-medium">{title}</h2>
          <div className="flex gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle && <p className="text-sm text-slate-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  const msg = error instanceof Error ? error.message : String(error)
  return <p className="rounded-md border border-red-800 bg-red-950/50 px-3 py-2 text-sm text-red-300">{msg}</p>
}

export function Badge({ tone, children }: { tone: 'green' | 'amber' | 'red' | 'slate' | 'sky' | 'violet'; children: ReactNode }) {
  const styles = {
    green: 'bg-emerald-900/60 text-emerald-300 border-emerald-700',
    amber: 'bg-amber-900/60 text-amber-300 border-amber-700',
    red: 'bg-red-900/60 text-red-300 border-red-700',
    slate: 'bg-slate-800 text-slate-300 border-slate-600',
    sky: 'bg-sky-900/60 text-sky-300 border-sky-700',
    violet: 'bg-violet-900/60 text-violet-300 border-violet-700',
  }[tone]
  return <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${styles}`}>{children}</span>
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-slate-300">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none'
