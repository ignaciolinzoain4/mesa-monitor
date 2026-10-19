import { createBrowserRouter, Navigate, RouterProvider, useLocation } from 'react-router'
import { lazy, Suspense, type ReactNode } from 'react'
import { Layout } from './components/Layout'
import { AuthProvider, useAuth } from './lib/auth'
import { AlertasPage } from './pages/AlertasPage'
import { BackupPage } from './pages/BackupPage'
import { CamerasPage } from './pages/CamerasPage'
import { HealthPage } from './pages/HealthPage'
import { LivePage } from './pages/LivePage'
import { SessionsPage } from './pages/SessionsPage'
import { SettingsPage } from './pages/SettingsPage'
import { LoginPage } from './pages/LoginPage'
import { PlanPage } from './pages/PlanPage'
import { ZonesPage } from './pages/ZonesPage'

function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="p-6 text-slate-400">Cargando…</div>
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (admin && user.role !== 'admin') return <Navigate to="/" replace />
  return children
}

// Análisis trae los gráficos (Recharts): se carga aparte para que /alertas siga liviana.
const AnalysisPage = lazy(() => import('./pages/AnalysisPage').then((m) => ({ default: m.AnalysisPage })))

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/alertas', element: <AlertasPage /> },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { path: '/', element: <Navigate to="/en-vivo" replace /> },
      { path: '/en-vivo', element: <LivePage /> },
      {
        path: '/analisis',
        element: (
          <Suspense fallback={<div className="p-6 text-slate-400">Cargando…</div>}>
            <AnalysisPage />
          </Suspense>
        ),
      },
      { path: '/sesiones', element: <SessionsPage /> },
      { path: '/salud', element: <HealthPage /> },
      { path: '/configuracion', element: <RequireAuth admin><SettingsPage /></RequireAuth> },
      { path: '/camaras', element: <RequireAuth admin><CamerasPage /></RequireAuth> },
      { path: '/zonas', element: <RequireAuth admin><ZonesPage /></RequireAuth> },
      { path: '/plano', element: <RequireAuth admin><PlanPage /></RequireAuth> },
      { path: '/respaldo', element: <RequireAuth admin><BackupPage /></RequireAuth> },
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
