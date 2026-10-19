import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../lib/auth'

const NAV: { to: string; label: string; adminOnly?: boolean }[] = [
  { to: '/camaras', label: 'Cámaras', adminOnly: true },
]

export function Layout() {
  const { user, logout } = useAuth()
  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <aside className="hidden w-52 shrink-0 flex-col border-r border-slate-800 bg-slate-900/40 md:flex">
        <div className="px-4 py-4 text-lg font-semibold">Mesa Monitor</div>
        <nav className="flex-1 space-y-0.5 px-2">
          {NAV.filter((n) => !n.adminOnly || user?.role === 'admin').map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `block rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:bg-slate-800/60'}`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-800 px-4 py-3 text-sm">
          <div className="text-slate-400">{user?.username}</div>
          <button onClick={logout} className="text-sky-400 hover:underline">
            Cerrar sesión
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-800 px-2 py-2 md:hidden">
          {NAV.filter((n) => !n.adminOnly || user?.role === 'admin').map((n) => (
            <NavLink key={n.to} to={n.to} className="shrink-0 rounded px-2 py-1 text-sm text-slate-300">
              {n.label}
            </NavLink>
          ))}
          <button onClick={logout} className="ml-auto shrink-0 px-2 text-sm text-sky-400">
            Salir
          </button>
        </nav>
        <main className="min-w-0 flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
