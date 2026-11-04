import { AuthProvider, useAuth } from './auth'
import Dashboard from './components/Dashboard'
import Login from './components/Login'

function Shell() {
  const { user, loading, logout } = useAuth()
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-slate-500">Loading…</p>
      </div>
    )
  }
  if (!user) return <Login />
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <h1 className="text-lg font-semibold text-brand-500">Docsmith</h1>
          <div className="flex items-center gap-3 text-sm text-slate-600">
            <span>{user.email}</span>
            <button
              onClick={logout}
              className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-8">
        <Dashboard />
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
