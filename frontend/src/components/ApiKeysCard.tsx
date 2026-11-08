import { useCallback, useEffect, useState } from 'react'
import api from '../client'

interface ApiKey {
  id: string
  name: string
  key_prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export default function ApiKeysCard() {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fullKey, setFullKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => {
    api
      .get<ApiKey[]>('/keys')
      .then((res) => setKeys(res.data))
      .catch(() => setError('Could not load API keys'))
  }, [])

  useEffect(load, [load])

  const create = async () => {
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      const res = await api.post<{ full_key: string }>('/keys', { name: name.trim() })
      setFullKey(res.data.full_key)
      setName('')
      load()
    } catch {
      setError('Could not create the API key')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: string) => {
    try {
      await api.delete(`/keys/${id}`)
      load()
    } catch {
      setError('Could not revoke the key')
    }
  }

  const copy = async () => {
    if (fullKey) {
      await navigator.clipboard.writeText(fullKey)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="mb-3 font-semibold">API keys</h2>
      <p className="mb-4 text-sm text-slate-500">
        Give a key to your Grok bot. The full key is shown once, right after creating it.
      </p>
      {fullKey && (
        <div className="mb-4 rounded-md border border-green-200 bg-green-50 p-3">
          <p className="mb-2 text-sm font-medium text-green-800">
            Copy this key now — it will not be shown again:
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded bg-white px-2 py-1.5 text-xs">
              {fullKey}
            </code>
            <button
              onClick={copy}
              className="rounded-md bg-green-700 px-3 py-1.5 text-xs text-white hover:bg-green-800"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
      )}
      <div className="mb-4 flex gap-2">
        <input
          placeholder="Key name (e.g. Grok bot)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
          onKeyDown={(e) => e.key === 'Enter' && create()}
        />
        <button
          onClick={create}
          disabled={creating || !name.trim()}
          className="rounded-md bg-brand-500 px-4 py-2 text-sm text-white hover:bg-brand-600 disabled:opacity-50"
        >
          Generate key
        </button>
      </div>
      {keys.length === 0 ? (
        <p className="text-sm text-slate-400">No API keys yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-400">
              <th className="py-2 pr-4">Name</th>
              <th className="py-2 pr-4">Key</th>
              <th className="py-2 pr-4">Created</th>
              <th className="py-2 pr-4">Last used</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id} className="border-t border-slate-100">
                <td className="py-2 pr-4 font-medium">{key.name}</td>
                <td className="py-2 pr-4 font-mono text-xs text-slate-500">
                  {key.key_prefix}…
                </td>
                <td className="py-2 pr-4 text-slate-500">
                  {new Intl.DateTimeFormat('en-GB', {
                    day: '2-digit',
                    month: '2-digit',
                    year: 'numeric',
                  }).format(new Date(key.created_at))}
                </td>
                <td className="py-2 pr-4 text-slate-500">
                  {key.last_used_at
                    ? new Intl.DateTimeFormat('en-GB', {
                        day: '2-digit',
                        month: '2-digit',
                        year: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: false,
                      }).format(new Date(key.last_used_at))
                    : 'Never'}
                </td>
                <td className="py-2 pr-4">
                  {key.revoked_at ? (
                    <span className="text-red-600">Revoked</span>
                  ) : (
                    <span className="text-green-700">Active</span>
                  )}
                </td>
                <td className="py-2 text-right">
                  {!key.revoked_at && (
                    <button
                      onClick={() => revoke(key.id)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </section>
  )
}
