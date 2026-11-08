import { useEffect, useState } from 'react'
import api from '../client'

type Endpoint = { method: string; path: string; purpose: string }

const GROUPS: { label: string; endpoints: Endpoint[] }[] = [
  {
    label: 'Documents',
    endpoints: [
      { method: 'POST', path: '/api/v1/tools/documents/search', purpose: 'Search / list docs' },
      { method: 'GET', path: '/api/v1/tools/documents/{id}', purpose: 'Read a doc (markdown)' },
      { method: 'POST', path: '/api/v1/tools/documents', purpose: 'Create a doc from markdown' },
      { method: 'POST', path: '/api/v1/tools/documents/get-or-create', purpose: 'Create or return existing by title' },
      { method: 'PATCH', path: '/api/v1/tools/documents/{id}', purpose: 'Replace section / regenerate' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/append', purpose: 'Append markdown' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/delete', purpose: 'Move to trash (confirm)' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/restore', purpose: 'Restore from trash' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/rename', purpose: 'Rename a doc' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/move', purpose: 'Move doc into a folder' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/copy', purpose: 'Copy a doc' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/share', purpose: 'Share with an email' },
      { method: 'GET', path: '/api/v1/tools/documents/{id}/share', purpose: 'List sharing permissions' },
      { method: 'GET', path: '/api/v1/tools/documents/{id}/comments', purpose: 'List comments' },
      { method: 'POST', path: '/api/v1/tools/documents/{id}/comments', purpose: 'Add a comment' },
      { method: 'GET', path: '/api/v1/tools/documents/{id}/export', purpose: 'Export as PDF (base64)' },
    ],
  },
  {
    label: 'Spreadsheets',
    endpoints: [
      { method: 'POST', path: '/api/v1/tools/spreadsheets', purpose: 'Create a spreadsheet (optional tab list)' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/search', purpose: 'Search / list spreadsheets' },
      { method: 'GET', path: '/api/v1/tools/spreadsheets/{id}', purpose: 'Get metadata (tabs, grid sizes)' },
      { method: 'GET', path: '/api/v1/tools/spreadsheets/{id}/values', purpose: 'Read values / formulas / csv / markdown' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/values', purpose: 'Write rows to a range (formulas ok)' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/values/append', purpose: 'Append rows to a tab' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/values/clear', purpose: 'Clear a range (confirm)' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/find-replace', purpose: 'Find and replace (confirm)' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/sheets', purpose: 'Add a tab' },
      { method: 'PATCH', path: '/api/v1/tools/spreadsheets/{id}/sheets/{name}', purpose: 'Rename or hide a tab' },
      { method: 'DELETE', path: '/api/v1/tools/spreadsheets/{id}/sheets/{name}', purpose: 'Delete a tab (confirm)' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/delete', purpose: 'Move to trash (confirm)' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/restore', purpose: 'Restore from trash' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/rename', purpose: 'Rename a spreadsheet' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/move', purpose: 'Move into a folder' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/copy', purpose: 'Copy a spreadsheet' },
      { method: 'POST', path: '/api/v1/tools/spreadsheets/{id}/share', purpose: 'Share with an email' },
      { method: 'GET', path: '/api/v1/tools/spreadsheets/{id}/share', purpose: 'List sharing permissions' },
      { method: 'GET', path: '/api/v1/tools/spreadsheets/{id}/export', purpose: 'Export as csv / xlsx / pdf (base64)' },
    ],
  },
  {
    label: 'Folders',
    endpoints: [
      { method: 'POST', path: '/api/v1/tools/folders', purpose: 'Create a Drive folder' },
      { method: 'GET', path: '/api/v1/tools/folders', purpose: 'List Drive folders' },
      { method: 'POST', path: '/api/v1/tools/folders/{id}/delete', purpose: 'Move folder to trash (confirm)' },
    ],
  },
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
      <p className="mb-4 text-sm text-slate-500">
        The bot can fetch <code className="rounded bg-slate-100 px-1 font-mono text-xs">GET /api/v1/tools/info</code>{' '}
        (quick start) and <code className="rounded bg-slate-100 px-1 font-mono text-xs">GET /api/v1/tools/docs</code>{' '}
        (full reference with usage examples) without a key.
      </p>
      <p className="mb-2 text-sm text-slate-500">Endpoints:</p>
      <div className="mb-4 divide-y divide-slate-100 rounded-lg border border-slate-200">
        {GROUPS.map((group) => (
          <details key={group.label} className="group">
            <summary className="flex cursor-pointer select-none items-center justify-between px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              <span>
                {group.label}{' '}
                <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-normal text-slate-500">
                  {group.endpoints.length}
                </span>
              </span>
              <span className="text-xs text-slate-400 transition-transform group-open:rotate-180" aria-hidden="true">
                ▾
              </span>
            </summary>
            <ul className="space-y-1 border-t border-slate-100 px-3 py-2 text-sm text-slate-600">
              {group.endpoints.map((ep) => (
                <li key={`${ep.method} ${ep.path}`} className="flex gap-2">
                  <span className="font-mono text-xs text-brand-600">{ep.method}</span>
                  <span className="font-mono text-xs">{ep.path}</span>
                  <span className="text-slate-400">— {ep.purpose}</span>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </div>
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
