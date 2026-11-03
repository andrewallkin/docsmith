import { useEffect, useState } from 'react'
import api from '../client'

const ENDPOINTS = [
  { method: 'POST', path: '/api/v1/tools/documents/search', purpose: 'Search docs by title' },
  { method: 'GET', path: '/api/v1/tools/documents/{id}', purpose: 'Read a doc (markdown)' },
  { method: 'POST', path: '/api/v1/tools/documents', purpose: 'Create a doc from markdown' },
  { method: 'PATCH', path: '/api/v1/tools/documents/{id}', purpose: 'Replace section / regenerate' },
  { method: 'POST', path: '/api/v1/tools/documents/{id}/append', purpose: 'Append markdown' },
  { method: 'POST', path: '/api/v1/tools/documents/{id}/delete', purpose: 'Move to trash (confirm)' },
]

export default function IntegrationCard() {
  const [base] = useState(window.location.origin)
  const [docs, setDocs] = useState<string | null>(null)
  const [showDocs, setShowDocs] = useState(false)

  useEffect(() => {
    api
      .get<string>('/v1/tools/docs', { responseType: 'text', transformResponse: [(d) => d] })
      .then((res) => setDocs(res.data))
      .catch(() => setDocs(null))
  }, [])

  const curlExample = `curl -X POST ${base}/api/v1/tools/documents \\
  -H "Authorization: Bearer ds_live_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"title": "Weekly report", "markdown": "# Weekly report\\n\\nAll good.", "theme": "professional"}'`

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="mb-3 font-semibold">Grok bot integration</h2>
      <p className="mb-3 text-sm text-slate-500">
        Point your Grok bot at Docsmith's tool API. Base URL:
      </p>
      <code className="mb-4 block overflow-x-auto rounded bg-slate-100 px-3 py-2 text-xs">
        {base}/api/v1
      </code>
      <p className="mb-2 text-sm text-slate-500">Endpoints:</p>
      <ul className="mb-4 space-y-1 text-sm text-slate-600">
        {ENDPOINTS.map((ep) => (
          <li key={ep.path} className="flex gap-2">
            <span className="font-mono text-xs text-brand-600">{ep.method}</span>
            <span className="font-mono text-xs">{ep.path}</span>
            <span className="text-slate-400">— {ep.purpose}</span>
          </li>
        ))}
      </ul>
      <p className="mb-2 text-sm text-slate-500">Example — create a document:</p>
      <pre className="mb-4 overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100">
        {curlExample}
      </pre>
      {docs && (
        <>
          <button
            onClick={() => setShowDocs(!showDocs)}
            className="text-sm text-brand-500 hover:underline"
          >
            {showDocs ? 'Hide' : 'Show'} full API reference for the bot
          </button>
          {showDocs && (
            <pre className="mt-3 max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs whitespace-pre-wrap">
              {docs}
            </pre>
          )}
        </>
      )}
    </section>
  )
}
