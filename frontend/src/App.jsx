import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteDocument,
  downloadFile,
  fetchFile,
  listDocuments,
  listVersions,
  uploadNewDocument,
} from './api.js'

// TEST ONLY: role switcher standing in for real authentication.
const ROLES = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'network-admin', label: 'Network admin' },
]

// TEST ONLY identity is remembered per browser so a page reload (e.g. a Vite
// restart) does not silently switch the user back to the viewer role.
const IDENTITY_KEY = 'portal-pdf-test.identity'

function loadIdentity() {
  try {
    const saved = JSON.parse(localStorage.getItem(IDENTITY_KEY) || 'null')
    if (saved && ROLES.some((r) => r.value === saved.role) && typeof saved.user === 'string') return saved
  } catch {
    // storage unavailable or corrupt: fall back to defaults
  }
  return { role: 'viewer', user: '' }
}

function saveIdentity(identity) {
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity))
  } catch {
    // storage unavailable: identity just won't survive reloads
  }
}

function formatTime(iso) {
  return new Date(iso).toLocaleString()
}

function Warnings({ warnings }) {
  if (!warnings || warnings.length === 0) return <span className="muted">None</span>
  return (
    <details>
      <summary className="warn">
        {warnings.length} formula error{warnings.length === 1 ? '' : 's'}
      </summary>
      <ul className="warning-list">
        {warnings.map((w) => (
          <li key={`${w.sheet}!${w.cell}`}>
            <code>
              {w.sheet}!{w.cell}
            </code>{' '}
            {w.error}
          </li>
        ))}
      </ul>
    </details>
  )
}

function IdentityBar({ identity, onChange }) {
  return (
    <header className="topbar">
      <h1>Portal PDF Test</h1>
      <div className="identity">
        <span className="badge" title="Fake identity for local testing; replaced by real auth in production">
          TEST ONLY
        </span>
        <label>
          Your name
          <input
            value={identity.user}
            onChange={(e) => onChange({ ...identity, user: e.target.value })}
            placeholder="e.g. alice"
            maxLength={100}
          />
        </label>
        <label>
          Role
          <select value={identity.role} onChange={(e) => onChange({ ...identity, role: e.target.value })}>
            {ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  )
}

function UploadResult({ result }) {
  const tone = { created: 'ok', unchanged: 'info', failed: 'error' }[result.outcome]
  return (
    <div className={`notice ${tone}`}>
      <p>{result.message}</p>
      {result.version.warnings.length > 0 && (
        <>
          <p className="small">This upload contains formula errors:</p>
          <Warnings warnings={result.version.warnings} />
        </>
      )}
    </div>
  )
}

function NewDocumentForm({ identity, onCreated }) {
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const res = await uploadNewDocument(file, identity)
      setResult(res)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      onCreated(res.document)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="stack">
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        onChange={(e) => setFile(e.target.files[0] || null)}
      />
      <button type="submit" disabled={!file || busy}>
        {busy ? 'Converting…' : 'Upload new document'}
      </button>
      <p className="muted small">The document is named after the file.</p>
      {error && <p className="error">{error}</p>}
      {result && <UploadResult result={result} />}
    </form>
  )
}

function DocumentList({ documents, selectedId, onSelect, onCreated, identity }) {
  return (
    <aside className="panel sidebar">
      <h2>Documents</h2>
      {identity.role === 'network-admin' && <NewDocumentForm identity={identity} onCreated={onCreated} />}
      <ul className="doc-list">
        {documents.map((d) => (
          <li key={d.id}>
            <button
              className={`doc-item ${d.id === selectedId ? 'selected' : ''}`}
              onClick={() => onSelect(d.id)}
            >
              <span>{d.name}</span>
              <span className="muted small">
                {d.latest_ready_version ? `v${d.latest_ready_version}` : 'no versions'}
              </span>
            </button>
          </li>
        ))}
        {documents.length === 0 && <li className="muted">No documents yet.</li>}
      </ul>
    </aside>
  )
}

function DocumentHeader({ document, identity, onDeleted }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function remove() {
    const ok = window.confirm(
      `Delete "${document.name}"?\n\nThis permanently removes every version, its PDFs and Excel files, and its download history.`,
    )
    if (!ok) return
    setBusy(true)
    setError(null)
    try {
      await deleteDocument(document.id, identity)
      onDeleted()
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="doc-header">
      <div className="row space-between">
        <h2>{document.name}</h2>
        {identity.role === 'network-admin' && (
          <button className="danger" onClick={remove} disabled={busy}>
            {busy ? 'Deleting…' : 'Delete document'}
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  )
}

function VersionTable({ documentId, versions, identity, onView }) {
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function download(version, format) {
    setError(null)
    setBusy(true)
    try {
      await downloadFile(documentId, version, format, identity)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const hasReady = versions.some((v) => v.status === 'ready')

  return (
    <section className="panel">
      <div className="row space-between">
        <h3>Versions</h3>
        <div className="row">
          <span className="muted small">Latest version:</span>
          <button onClick={() => onView(null)} disabled={!hasReady || busy}>
            View
          </button>
          <button onClick={() => download(null, 'pdf')} disabled={!hasReady || busy}>
            Download PDF
          </button>
          <button onClick={() => download(null, 'xlsx')} disabled={!hasReady || busy}>
            Download Excel
          </button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {versions.length === 0 ? (
        <p className="muted">No versions uploaded yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Uploaded by</th>
                <th>Uploaded at</th>
                <th>Status</th>
                <th>Warnings</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id}>
                  <td>v{v.version}</td>
                  <td>{v.uploaded_by}</td>
                  <td>{formatTime(v.uploaded_at)}</td>
                  <td>
                    <span className={`status ${v.status}`} title={v.error || undefined}>
                      {v.status}
                    </span>
                  </td>
                  <td>
                    <Warnings warnings={v.warnings} />
                  </td>
                  <td>
                    <div className="row nowrap">
                      <button
                        className="secondary"
                        onClick={() => onView(v.version)}
                        disabled={v.status !== 'ready' || busy}
                      >
                        View
                      </button>
                      <button
                        className="secondary"
                        onClick={() => download(v.version, 'pdf')}
                        disabled={v.status !== 'ready' || busy}
                      >
                        PDF
                      </button>
                      <button
                        className="secondary"
                        onClick={() => download(v.version, 'xlsx')}
                        disabled={v.status !== 'ready' || busy}
                      >
                        Excel
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function PdfViewer({ preview, onClose }) {
  return (
    <section className="panel viewer">
      <div className="row space-between">
        <h3>{preview.title}</h3>
        <div className="row">
          <a className="button secondary" href={preview.url} target="_blank" rel="noopener noreferrer">
            Open in new tab
          </a>
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <iframe className="pdf-frame" src={preview.url} title={preview.title} />
    </section>
  )
}

export default function App() {
  const [identity, setIdentity] = useState(loadIdentity)

  useEffect(() => saveIdentity(identity), [identity])
  const [documents, setDocuments] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [versions, setVersions] = useState([])
  const [error, setError] = useState(null)
  const [preview, setPreview] = useState(null) // { url, title } of the PDF shown on the page

  const closePreview = useCallback(() => {
    setPreview((p) => {
      if (p?.url) URL.revokeObjectURL(p.url)
      return null
    })
  }, [])

  // Viewing fetches the PDF with the role header (like a download) and shows it on the page.
  const viewPdf = useCallback(
    async (documentId, documentName, version) => {
      setError(null)
      try {
        const file = await fetchFile(documentId, version, 'pdf', identity)
        const url = URL.createObjectURL(file.blob)
        setPreview((p) => {
          if (p?.url) URL.revokeObjectURL(p.url)
          return { url, title: `${documentName} · v${file.version}` }
        })
      } catch (err) {
        setError(err.message)
      }
    },
    [identity],
  )

  useEffect(closePreview, [selectedId, closePreview])

  const refreshDocuments = useCallback(async () => {
    try {
      setDocuments(await listDocuments())
    } catch (err) {
      setError(err.message)
    }
  }, [])

  const refreshVersions = useCallback(async () => {
    if (selectedId == null) return
    try {
      setVersions(await listVersions(selectedId))
    } catch (err) {
      setError(err.message)
    }
  }, [selectedId])

  useEffect(() => {
    refreshDocuments()
  }, [refreshDocuments])

  useEffect(() => {
    setVersions([])
    refreshVersions()
  }, [refreshVersions])

  const selected = documents.find((d) => d.id === selectedId)

  return (
    <>
      <IdentityBar identity={identity} onChange={setIdentity} />
      <main className="layout">
        <DocumentList
          identity={identity}
          documents={documents}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreated={(doc) => {
            refreshDocuments()
            setSelectedId(doc.id)
          }}
        />
        <div className="content">
          {error && <p className="error">{error}</p>}
          {selected ? (
            <>
              <DocumentHeader
                key={selected.id}
                document={selected}
                identity={identity}
                onDeleted={() => {
                  setSelectedId(null)
                  refreshDocuments()
                }}
              />
              <VersionTable
                documentId={selected.id}
                versions={versions}
                identity={identity}
                onView={(version) => viewPdf(selected.id, selected.name, version)}
              />
              {preview && <PdfViewer preview={preview} onClose={closePreview} />}
            </>
          ) : (
            <p className="muted">
              {identity.role === 'network-admin'
                ? 'Upload a new document or select one to get started.'
                : 'Select a document to get started.'}
            </p>
          )}
        </div>
      </main>
    </>
  )
}
