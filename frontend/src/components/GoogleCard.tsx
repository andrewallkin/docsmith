import { useCallback, useEffect, useState } from 'react'
import api from '../client'

interface ConnectionStatus {
  connected: boolean
  google_email: string | null
  scopes: string[]
  connected_at: string | null
  expired: boolean
}

export default function GoogleCard() {
  const [status, setStatus] = useState<ConnectionStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api
      .get<ConnectionStatus>('/google/status')
      .then((res) => setStatus(res.data))
      .catch(() => setError('Could not load Google connection status'))
  }, [])

  useEffect(load, [load])

  const connect = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.get<{ auth_url: string }>('/google/connect')
      window.location.href = res.data.auth_url
    } catch {
      setError('Could not start the Google connection flow')
      setBusy(false)
    }
  }

  const disconnect = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.delete('/google/connection')
      load()
    } catch {
      setError('Could not disconnect Google')
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="mb-2 font-semibold">Google Workspace</h2>
        {error ? <p className="text-sm text-red-600">{error}</p> : <p className="text-sm text-slate-500">Loading…</p>}
      </section>
    )
  }

  const connectedAndValid = status.connected && !status.expired

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold">Google Workspace</h2>
        <span
          className={
            'rounded-full px-2.5 py-0.5 text-xs font-medium ' +
            (connectedAndValid
              ? 'bg-green-100 text-green-700'
              : status.expired
                ? 'bg-amber-100 text-amber-700'
                : 'bg-slate-100 text-slate-600')
          }
        >
          {status.expired ? 'Expired' : status.connected ? 'Connected' : 'Not connected'}
        </span>
      </div>
      {status.expired && (
        <p className="mb-3 rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
          Your Google connection has expired (test-mode tokens expire after 7 days).
          Reconnect to keep the tool API working.
        </p>
      )}
      {status.connected && status.google_email && (
        <p className="text-sm text-slate-600">
          Connected as <span className="font-medium">{status.google_email}</span>
        </p>
      )}
      <p className="mt-1 text-xs text-slate-400">
        Scope: docs created by Docsmith or opened via Google Picker
      </p>
      <div className="mt-4 flex gap-2">
        {connectedAndValid ? (
          <button
            onClick={disconnect}
            disabled={busy}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={connect}
            disabled={busy}
            className="rounded-md bg-brand-500 px-4 py-2 text-sm text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {status.expired ? 'Reconnect Google' : 'Connect Google'}
          </button>
        )}
        {status.expired && (
          <button
            onClick={disconnect}
            disabled={busy}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50"
          >
            Disconnect
          </button>
        )}
      </div>
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </section>
  )
}
