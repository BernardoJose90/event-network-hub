import { useCallback, useEffect, useRef, useState } from 'react'
import { createDocument, downloadPdf, listDocuments, listVersions, uploadVersion } from './api.js'

// TEST ONLY: role switcher standing in for real authentication.
const ROLES = [
  { value: 'viewer', label: 'Viewer (redacted PDF)' },
  { value: 'network-admin', label: 'Network admin (full PDF)' },
]

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

function DocumentList({ documents, selectedId, onSelect, onCreated }) {
  const [name, setName] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const doc = await createDocument(name.trim())
      setName('')
      onCreated(doc)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="panel sidebar">
      <h2>Documents</h2>
      <form onSubmit={submit} className="stack">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New document name"
          maxLength={200}
          required
        />
        <button type="submit" disabled={busy || !name.trim()}>
          Create document
        </button>
        {error && <p className="error">{error}</p>}
      </form>
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

function UploadForm({ documentId, identity, onUploaded }) {
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setResult(null)
    setError(null)
  }, [documentId])

  async function submit(e) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const res = await uploadVersion(documentId, file, identity)
      setResult(res)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      onUploaded()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const tone = result ? { created: 'ok', unchanged: 'info', failed: 'error' }[result.outcome] : null

  return (
    <section className="panel">
      <h3>Upload a new version</h3>
      <form onSubmit={submit} className="row">
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => setFile(e.target.files[0] || null)}
        />
        <button type="submit" disabled={!file || busy}>
          {busy ? 'Converting…' : 'Upload'}
        </button>
      </form>
      <p className="muted small">.xlsx only, up to 10 MB. Conversion can take up to 90 seconds.</p>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className={`notice ${tone}`}>
          <p>{result.message}</p>
          {result.outcome === 'created' && result.redacted_cells > 0 && (
            <p className="small">{result.redacted_cells} sensitive cell(s) redacted in the viewer copy.</p>
          )}
          {result.version.warnings.length > 0 && (
            <>
              <p className="small">This upload contains formula errors:</p>
              <Warnings warnings={result.version.warnings} />
            </>
          )}
        </div>
      )}
    </section>
  )
}

function VersionTable({ documentId, versions, identity }) {
  const [error, setError] = useState(null)
  const [busyVersion, setBusyVersion] = useState(null)
  const isAdmin = identity.role === 'network-admin'

  async function download(version) {
    setError(null)
    setBusyVersion(version ?? 'latest')
    try {
      await downloadPdf(documentId, version, identity)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyVersion(null)
    }
  }

  const hasReady = versions.some((v) => v.status === 'ready')

  return (
    <section className="panel">
      <div className="row space-between">
        <h3>Versions</h3>
        <button onClick={() => download(null)} disabled={!hasReady || busyVersion !== null}>
          Download latest ({isAdmin ? 'full' : 'redacted'})
        </button>
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
                    <button
                      className="secondary"
                      onClick={() => download(v.version)}
                      disabled={v.status !== 'ready' || busyVersion !== null}
                    >
                      {isAdmin ? 'Full PDF' : 'Redacted PDF'}
                    </button>
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

export default function App() {
  const [identity, setIdentity] = useState({ role: 'viewer', user: '' })
  const [documents, setDocuments] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [versions, setVersions] = useState([])
  const [error, setError] = useState(null)

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
              <h2>{selected.name}</h2>
              <UploadForm
                documentId={selected.id}
                identity={identity}
                onUploaded={() => {
                  refreshVersions()
                  refreshDocuments()
                }}
              />
              <VersionTable documentId={selected.id} versions={versions} identity={identity} />
            </>
          ) : (
            <p className="muted">Create or select a document to get started.</p>
          )}
        </div>
      </main>
    </>
  )
}
