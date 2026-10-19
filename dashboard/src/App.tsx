import { createBrowserRouter, Navigate, RouterProvider, useLocation } from 'react-router'
import type { ReactNode } from 'react'
import { Layout } from './components/Layout'
import { AuthProvider, useAuth } from './lib/auth'
import { CamerasPage } from './pages/CamerasPage'
import { LoginPage } from './pages/LoginPage'

function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="p-6 text-slate-400">Cargando…</div>
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (admin && user.role !== 'admin') return <Navigate to="/" replace />
  return children
}

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { path: '/', element: <Navigate to="/camaras" replace /> },
      { path: '/camaras', element: <RequireAuth admin><CamerasPage /></RequireAuth> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
])

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  )
}
