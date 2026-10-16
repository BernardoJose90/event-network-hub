import { useCallback, useEffect, useRef, useState } from 'react'
import { getDocument, startEdit } from './api.js'
import { loadIdentity } from './identity.js'

// Full-screen editor page, opened from "Edit" as /edit/<documentId>/<version>.
//
// Embeds Collabora Online the standard WOPI way: POST the access token into a
// named iframe. Talks to the editor over postMessage to know when the document
// is loaded, whether it has unsaved changes, and when a save has finished.
// "Back to documents" saves first, then returns to the document in the app.

function backToDocument(documentId) {
  window.location.assign(`/?doc=${documentId}`)
}
export default function EditorPage({ documentId, version }) {
  const [identity] = useState(loadIdentity) // TEST ONLY role/name chosen in the main tab
  const [session, setSession] = useState(null)
  const [title, setTitle] = useState(`Document · V${version}`)
  const [error, setError] = useState(null)
  const startedRef = useRef(false)

  useEffect(() => {
    // StrictMode runs effects twice in development; start only one session.
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        const doc = await getDocument(documentId)
        setTitle(`${doc.name} · V${version}`)
        document.title = `Editing ${doc.name} V${version}`
        setSession(await startEdit(documentId, version, identity))
      } catch (err) {
        setError(err.message)
      }
    })()
  }, [documentId, version, identity])

  if (error) {
    return (
      <div className="editor-page">
        <header className="editor-bar">
          <strong>{title}</strong>
        </header>
        <div className="editor-message">
          <p className="error">{error}</p>
          <p className="muted small">Editing needs the Network admin role.</p>
          <button onClick={() => backToDocument(documentId)}>Back to documents</button>
        </div>
      </div>
    )
  }

  return session ? (
    <SpreadsheetEditor session={session} title={title} documentId={documentId} />
  ) : (
    <div className="editor-page">
      <header className="editor-bar">
        <strong>{title}</strong>
        <span className="muted small">Opening editor…</span>
      </header>
    </div>
  )
}

function SpreadsheetEditor({ session, title, documentId }) {
  const frameRef = useRef(null)
  const formRef = useRef(null)
  const modifiedRef = useRef(false)
  const pendingCloseRef = useRef(null)
  const [status, setStatus] = useState('Loading…')
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

  // Save unsaved changes before leaving, so going back never loses work.
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
      if (!saved && !window.confirm('Saving did not finish. Leave anyway and lose unsaved changes?')) {
        setClosing(false)
        return
      }
    }
    modifiedRef.current = false // nothing left to warn about on unload
    backToDocument(documentId)
  }

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
            setStatus(`Saving creates a new version after the newest one.`)
          }
          break
        case 'Doc_ModifiedStatus':
          // The editor also reports "unmodified" right after loading; only a
          // modified -> unmodified change means a save happened.
          if (values.Modified) setStatus('Unsaved changes')
          else if (modifiedRef.current) setStatus('All changes saved as a new version')
          modifiedRef.current = !!values.Modified
          break
        case 'Action_Save_Resp':
          setStatus(values.success ? 'All changes saved as a new version' : `Save failed: ${values.result || 'unknown error'}`)
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

  // Warn before leaving the page (browser back, reload, closing) with unsaved changes.
  useEffect(() => {
    function beforeUnload(event) {
      if (!modifiedRef.current) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [])

  return (
    <div className="editor-page">
      <header className="editor-bar">
        <strong>Editing {title}</strong>
        <span className="muted small editor-status">{status}</span>
        <button className="secondary" onClick={requestClose} disabled={closing}>
          {closing ? 'Saving…' : 'Back to documents'}
        </button>
      </header>
      <form ref={formRef} action={session.editor_url} method="post" target="spreadsheet-editor">
        <input type="hidden" name="access_token" value={session.access_token} />
        <input type="hidden" name="access_token_ttl" value={session.access_token_ttl} />
      </form>
      <iframe
        ref={frameRef}
        name="spreadsheet-editor"
        className="editor-fullscreen"
        title={title}
        allow="clipboard-read *; clipboard-write *; fullscreen"
      />
    </div>
  )
}
