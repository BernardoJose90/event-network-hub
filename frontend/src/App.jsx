import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteDocument,
  downloadFile,
  fetchFile,
  startEdit,
  listDocuments,
  listVersions,
  uploadDocument,
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

function UploadForm({ identity, onCreated }) {
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
      const res = await uploadDocument(file, identity)
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
        {busy ? 'Converting…' : 'Upload'}
      </button>
      <p className="muted small">
        Excel (.xlsx) only, named like <code>LIS26 _ Event_Network_Requirements_V1.xlsx</code> (spaces,
        _ or - between words). A new V number updates that event&apos;s document; each V number can be
        uploaded once.
      </p>
      {error && <p className="error">{error}</p>}
      {result && <UploadResult result={result} />}
    </form>
  )
}

function DocumentList({ documents, selectedId, viewing, onSelect, onViewVersion, onCreated, identity }) {
  return (
    <aside className="panel sidebar">
      <h2>Documents</h2>
      {identity.role === 'network-admin' && <UploadForm identity={identity} onCreated={onCreated} />}
      <ul className="doc-list">
        {documents.map((d) => (
          <li key={d.id}>
            <button
              className={`doc-item ${d.id === selectedId ? 'selected' : ''}`}
              onClick={() => onSelect(d.id)}
            >
              <span>{d.name}</span>
              <span className="muted small">
                {d.versions.length} version{d.versions.length === 1 ? '' : 's'}
              </span>
            </button>
            {d.versions.length > 0 && (
              <ul className="version-list">
                {d.versions.map((v) => (
                  <li key={v.version}>
                    <button
                      className={`version-item ${v.status} ${
                        viewing?.documentId === d.id && viewing?.version === v.version ? 'selected' : ''
                      }`}
                      onClick={() => onViewVersion(d, v.version)}
                      disabled={v.status !== 'ready'}
                      title={v.status === 'ready' ? `View V${v.version}` : `V${v.version} is ${v.status}`}
                    >
                      V{v.version}
                      {v.version === d.latest_ready_version && <span className="small muted"> · latest</span>}
                      {v.status !== 'ready' && <span className="small"> · {v.status}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
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

function VersionTable({ documentId, versions, identity, onView, onEdit }) {
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
  const latestReady = versions.find((v) => v.status === 'ready')?.version
  const isAdmin = identity.role === 'network-admin'

  return (
    <section className="panel">
      <div className="row space-between">
        <h3>Versions</h3>
        <div className="row">
          <span className="muted small">Latest version:</span>
          <button onClick={() => onView(null)} disabled={!hasReady || busy}>
            View
          </button>
          {isAdmin && (
            <button onClick={() => onEdit(latestReady)} disabled={!hasReady || busy}>
              Edit
            </button>
          )}
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
                  <td>
                    V{v.version}
                    {v.edited_from && <div className="muted small">edited from V{v.edited_from}</div>}
                  </td>
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
                      {isAdmin && (
                        <button
                          className="secondary"
                          onClick={() => onEdit(v.version)}
                          disabled={v.status !== 'ready' || busy}
                        >
                          Edit
                        </button>
                      )}
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

// Embeds Collabora Online the standard WOPI way: POST the access token into a
// named iframe. Talks to the editor over postMessage to know when the document
// is loaded, whether it has unsaved changes, and when a save has finished.
function SpreadsheetEditor({ session, onSaved, onClose }) {
  const frameRef = useRef(null)
  const formRef = useRef(null)
  const modifiedRef = useRef(false)
  const pendingCloseRef = useRef(null)
  const [status, setStatus] = useState('Loading editor…')
  const [closing, setClosing] = useState(false)
  const editorOrigin = new URL(session.editor_url).origin

  const post = useCallback(
    (MessageId, Values = {}) =>
      frameRef.current?.contentWindow?.postMessage(
        JSON.stringify({ MessageId, SendTime: Date.now(), Values }),
        editorOrigin,
      ),
    [editorOrigin],
  )

  useEffect(() => {
    formRef.current.submit()
  }, [session])

  useEffect(() => {
    function onMessage(event) {
      if (event.origin !== editorOrigin || event.source !== frameRef.current?.contentWindow) return
      let msg
      try {
        msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data
      } catch {
        return
      }
      const values = msg.Values || {}
      switch (msg.MessageId) {
        case 'App_LoadingStatus':
          if (values.Status === 'Document_Loaded') {
            post('Host_PostmessageReady')
            setStatus(`Editing V${session.base_version}. Saving creates a new version.`)
          }
          break
        case 'Doc_ModifiedStatus':
          // Modified -> unmodified means a save (Ctrl+S, toolbar or autosave) finished.
          if (modifiedRef.current && !values.Modified) onSaved()
          modifiedRef.current = !!values.Modified
          setStatus(values.Modified ? 'Unsaved changes' : 'All changes saved')
          break
        case 'Action_Save_Resp':
          if (values.success) onSaved()
          setStatus(values.success ? 'Saved as a new version' : `Save failed: ${values.result || 'unknown error'}`)
          pendingCloseRef.current?.(values.success)
          break
        case 'UI_Close':
          requestClose()
          break
        default:
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  })

  // Save unsaved changes before closing, so closing never loses work.
  async function requestClose() {
    if (closing) return
    setClosing(true)
    if (modifiedRef.current) {
      setStatus('Saving before closing…')
      const saved = await new Promise((resolve) => {
        pendingCloseRef.current = resolve
        post('Action_Save', { DontTerminateEdit: true, DontSaveIfUnmodified: true, Notify: true })
        setTimeout(() => resolve(false), 20000)
      })
      pendingCloseRef.current = null
      if (!saved && !window.confirm('Saving did not finish. Close the editor anyway and lose unsaved changes?')) {
        setClosing(false)
        return
      }
    }
    onClose()
  }

  return (
    <section className="panel editor">
      <div className="row space-between">
        <div>
          <h3>{session.title}</h3>
          <p className="muted small">{status}</p>
        </div>
        <button className="secondary" onClick={requestClose} disabled={closing}>
          {closing ? 'Closing…' : 'Close editor'}
        </button>
      </div>
      <form ref={formRef} action={session.editor_url} method="post" target="spreadsheet-editor">
        <input type="hidden" name="access_token" value={session.access_token} />
        <input type="hidden" name="access_token_ttl" value={session.access_token_ttl} />
      </form>
      <iframe
        ref={frameRef}
        name="spreadsheet-editor"
        className="editor-frame"
        title={session.title}
        allow="clipboard-read *; clipboard-write *; fullscreen"
      />
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
  const [editing, setEditing] = useState(null) // editor session, see SpreadsheetEditor

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
          return { url, title: `${documentName} · V${file.version}`, documentId, version: Number(file.version) }
        })
      } catch (err) {
        setError(err.message)
      }
    },
    [identity],
  )

  useEffect(closePreview, [selectedId, closePreview])
  useEffect(() => setEditing(null), [selectedId])

  const openEditor = useCallback(
    async (documentId, documentName, version) => {
      setError(null)
      try {
        const session = await startEdit(documentId, version, identity)
        closePreview()
        setEditing({ ...session, title: `${documentName} · editing V${version}` })
      } catch (err) {
        setError(err.message)
      }
    },
    [identity, closePreview],
  )

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

  // A new or edited version is converted in the background; re-check until it settles.
  useEffect(() => {
    if (!versions.some((v) => v.status === 'processing')) return undefined
    const timer = setTimeout(() => {
      refreshVersions()
      refreshDocuments()
    }, 2000)
    return () => clearTimeout(timer)
  }, [versions, refreshVersions, refreshDocuments])

  const selected = documents.find((d) => d.id === selectedId)

  return (
    <>
      <IdentityBar identity={identity} onChange={setIdentity} />
      <main className="layout">
        <DocumentList
          identity={identity}
          documents={documents}
          selectedId={selectedId}
          viewing={preview}
          onSelect={setSelectedId}
          onViewVersion={(doc, version) => {
            setSelectedId(doc.id)
            viewPdf(doc.id, doc.name, version)
          }}
          onCreated={(doc) => {
            refreshDocuments()
            // Selecting a different document reloads its versions; when the upload
            // added a version to the open document, reload them explicitly.
            if (doc.id === selectedId) refreshVersions()
            else setSelectedId(doc.id)
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
                onEdit={(version) => openEditor(selected.id, selected.name, version)}
              />
              {editing && (
                <SpreadsheetEditor
                  key={editing.access_token}
                  session={editing}
                  onSaved={() => {
                    refreshVersions()
                    refreshDocuments()
                  }}
                  onClose={() => {
                    setEditing(null)
                    refreshVersions()
                    refreshDocuments()
                  }}
                />
              )}
              {preview && <PdfViewer preview={preview} onClose={closePreview} />}
            </>
          ) : (
            <p className="muted">
              {identity.role === 'network-admin'
                ? 'Upload a file or select a document to get started.'
                : 'Select a document to get started.'}
            </p>
          )}
        </div>
      </main>
    </>
  )
}
