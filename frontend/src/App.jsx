import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createEvent,
  deleteDocument,
  deleteTemplate,
  downloadFile,
  fetchFile,
  listDocuments,
  listTemplates,
  listVersions,
  uploadDocument,
} from './api.js'
import { ROLES, loadIdentity, saveIdentity } from './identity.js'

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
      <h1 className="brand">
        <img src="/websummit-mark.png" alt="Web Summit" className="brand-mark" />
        Event Network Hub
      </h1>
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
        _ or - between words; &ldquo;Event Network Matrix [V1]&rdquo; also works). A new V number updates that event&apos;s document; each V number can be
        uploaded once.
      </p>
      {error && <p className="error">{error}</p>}
      {result && <UploadResult result={result} />}
    </form>
  )
}

function NewEventForm({ identity, templates }) {
  const [open, setOpen] = useState(false)
  const [code, setCode] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const doc = await createEvent(
        { eventCode: code.trim(), templateId: templateId ? Number(templateId) : null },
        identity,
      )
      window.location.assign(`/event/${doc.id}`)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  if (!open) {
    return <button onClick={() => setOpen(true)}>+ New event</button>
  }
  return (
    <form onSubmit={submit} className="stack new-event">
      <label className="stack-label">
        Event code
        <input autoFocus value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. WEB26" maxLength={20} />
      </label>
      <label className="stack-label">
        Start from
        <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
          <option value="">Empty (one venue, the usual sections)</option>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      {templates.length === 0 && (
        <p className="muted small">No templates yet. Use &ldquo;Save as template&rdquo; on an event&apos;s requirements page.</p>
      )}
      <p className="muted small">
        {templateId
          ? 'Copies the template: venues, sections, lists, Wi-Fi networks, Ethernet items and VLAN extensions.'
          : 'Add venues, sections and list values on the requirements page.'}{' '}
        Publishing creates V1.
      </p>
      <div className="row nowrap">
        <button type="submit" disabled={busy || !code.trim()}>{busy ? 'Creating…' : 'Create'}</button>
        <button type="button" className="secondary" onClick={() => { setOpen(false); setError(null) }}>Cancel</button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  )
}

function TemplateList({ identity, templates, onChanged }) {
  const [error, setError] = useState(null)

  async function remove(t) {
    if (!window.confirm(`Delete the template "${t.name}"?\n\nEvents already created from it are not affected.`)) return
    setError(null)
    try {
      await deleteTemplate(t.id, identity)
      onChanged()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <details className="templates">
      <summary className="muted small">Templates ({templates.length})</summary>
      <ul className="template-list">
        {templates.map((t) => (
          <li key={t.id} className="row space-between nowrap">
            <button className="link" onClick={() => window.location.assign(`/event/${t.id}`)} title="View and edit the template">
              {t.name}
            </button>
            <button className="link danger-link" onClick={() => remove(t)}>Delete</button>
          </li>
        ))}
        {templates.length === 0 && <li className="muted small">None yet. Save an event as a template on its requirements page.</li>}
      </ul>
      {error && <p className="error">{error}</p>}
    </details>
  )
}

function DocumentList({ documents, selectedId, viewing, onSelect, onViewVersion, onCreated, identity }) {
  const [templates, setTemplates] = useState([])
  const isAdmin = identity.role === 'network-admin'
  const loadTemplates = useCallback(() => {
    if (!isAdmin) return
    listTemplates(identity).then(setTemplates).catch(() => setTemplates([]))
  }, [identity, isAdmin])
  useEffect(loadTemplates, [loadTemplates])

  return (
    <aside className="panel sidebar">
      <h2>Documents</h2>
      {isAdmin && (
        <div className="stack">
          <NewEventForm identity={identity} templates={templates} />
          <TemplateList identity={identity} templates={templates} onChanged={loadTemplates} />
          <details className="legacy-upload">
            <summary className="muted small">Legacy: upload a workbook</summary>
            <UploadForm identity={identity} onCreated={onCreated} />
          </details>
        </div>
      )}
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
        <div className="row">
          <button onClick={() => window.location.assign(`/event/${document.id}`)} title="View and edit the requirements">
            Open requirements
          </button>
          {identity.role === 'network-admin' && (
            <>
              <details className="legacy">
                <summary>Legacy</summary>
                <div className="legacy-menu">
                  <button className="secondary" onClick={() => window.location.assign(`/sheet/${document.id}`)}>
                    Open live sheet
                  </button>
                  <p className="muted small">The Excel-style editor is on each version: Edit.</p>
                </div>
              </details>
              <button className="danger" onClick={remove} disabled={busy}>
                {busy ? 'Deleting…' : 'Delete document'}
              </button>
            </>
          )}
        </div>
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
        <p className="muted">No versions yet. Publishing the requirements creates V1.</p>
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
                    {v.source === 'publish' && <div className="muted small">published from requirements</div>}
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
  // "/?doc=<id>" opens that document, e.g. when coming back from the editor page.
  const [selectedId, setSelectedId] = useState(() => {
    const id = Number(new URLSearchParams(window.location.search).get('doc'))
    return Number.isInteger(id) && id > 0 ? id : null
  })
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
          return { url, title: `${documentName} · V${file.version}`, documentId, version: Number(file.version) }
        })
      } catch (err) {
        setError(err.message)
      }
    },
    [identity],
  )

  useEffect(closePreview, [selectedId, closePreview])

  // The editor is its own full-screen page (see EditorPage.jsx).
  const openEditor = useCallback((documentId, version) => {
    window.location.assign(`/edit/${documentId}/${version}`)
  }, [])

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
                onEdit={(version) => openEditor(selected.id, version)}
              />
              {preview && <PdfViewer preview={preview} onClose={closePreview} />}
            </>
          ) : (
            <p className="muted">
              {identity.role === 'network-admin'
                ? 'Create a new event or select a document to get started.'
                : 'Select a document to get started.'}
            </p>
          )}
        </div>
      </main>
    </>
  )
}
