import { useCallback, useEffect, useRef, useState } from 'react'
import { downloadSheet, getDocument, getSheet, listVersions, saveSheet, startSheet } from './api.js'
import { loadIdentity } from './identity.js'
import Grid from './sheet/Grid.jsx'

// Live sheet page, /sheet/<documentId>: the in-app, editable copy of a
// document's workbook. Edits save automatically; Excel and PDF are generated
// from it on download. Network admins only (enforced by the API).

const AUTOSAVE_DELAY_MS = 1500
const MAX_UNDO = 100

function backToDocument(documentId) {
  window.location.assign(`/?doc=${documentId}`)
}

export default function SheetPage({ documentId }) {
  const [identity] = useState(loadIdentity) // TEST ONLY role/name chosen on the main page
  const [docName, setDocName] = useState('')
  const [phase, setPhase] = useState('loading') // loading | missing | ready | error
  const [error, setError] = useState(null)
  const [latestReady, setLatestReady] = useState(null)
  const [meta, setMeta] = useState(null) // { base_version, latest_version, warnings }
  const [workbook, setWorkbook] = useState(null)
  const [sheetIndex, setSheetIndex] = useState(0)
  const [saveState, setSaveState] = useState('saved') // saved | pending | saving | failed | conflict
  const [saveError, setSaveError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [, setHistoryTick] = useState(0) // re-render when undo/redo availability changes

  const workbookRef = useRef(null)
  const revisionRef = useRef(null)
  const editSeqRef = useRef(0) // increases on every change
  const savedSeqRef = useRef(0) // editSeq covered by the last successful save
  const timerRef = useRef(null)
  const savingRef = useRef(null) // promise of the save in flight
  const historyRef = useRef({ past: [], future: [] })
  const saveStateRef = useRef(saveState)
  saveStateRef.current = saveState

  const loadSheet = useCallback(
    (sheet) => {
      workbookRef.current = sheet.data
      revisionRef.current = sheet.revision
      editSeqRef.current = 0
      savedSeqRef.current = 0
      historyRef.current = { past: [], future: [] }
      setWorkbook(sheet.data)
      setMeta({ base_version: sheet.base_version, latest_version: sheet.latest_version, warnings: sheet.data.warnings || [] })
      setSheetIndex((i) => Math.min(i, sheet.data.sheets.length - 1))
      setSaveState('saved')
      setSaveError(null)
      setPhase('ready')
    },
    [],
  )

  useEffect(() => {
    ;(async () => {
      try {
        const doc = await getDocument(documentId)
        setDocName(doc.name)
        document.title = `${doc.name} · live sheet`
        const sheet = await getSheet(documentId, identity)
        if (sheet) loadSheet(sheet)
        else {
          const versions = await listVersions(documentId)
          setLatestReady(versions.find((v) => v.status === 'ready')?.version ?? null)
          setPhase('missing')
        }
      } catch (err) {
        setError(err.status === 403 ? 'The live sheet is for network admins.' : err.message)
        setPhase('error')
      }
    })()
  }, [documentId, identity, loadSheet])

  // --- saving -----------------------------------------------------------------

  const save = useCallback(async () => {
    if (savingRef.current) await savingRef.current
    if (editSeqRef.current === savedSeqRef.current) return
    const seq = editSeqRef.current
    setSaveState('saving')
    const attempt = (async () => {
      try {
        const res = await saveSheet(documentId, workbookRef.current, revisionRef.current, identity)
        revisionRef.current = res.revision
        savedSeqRef.current = seq
        setSaveError(null)
        setSaveState(editSeqRef.current === seq ? 'saved' : 'pending')
      } catch (err) {
        setSaveError(err.message)
        setSaveState(err.status === 409 ? 'conflict' : 'failed')
      }
    })()
    savingRef.current = attempt
    await attempt
    savingRef.current = null
  }, [documentId, identity])

  const scheduleSave = useCallback(() => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      await save()
      if (editSeqRef.current !== savedSeqRef.current && saveStateRef.current === 'pending') scheduleSave()
    }, AUTOSAVE_DELAY_MS)
  }, [save])

  // Save now (before leaving or downloading).
  const flush = useCallback(async () => {
    clearTimeout(timerRef.current)
    await save()
    return editSeqRef.current === savedSeqRef.current
  }, [save])

  const setAndTrack = useCallback(
    (next) => {
      workbookRef.current = next
      editSeqRef.current += 1
      setWorkbook(next)
      if (saveStateRef.current !== 'conflict') {
        setSaveState('pending')
        scheduleSave()
      }
    },
    [scheduleSave],
  )

  const change = useCallback(
    (fn) => {
      const prev = workbookRef.current
      const next = fn(prev)
      if (next === prev) return
      const h = historyRef.current
      h.past = [...h.past.slice(-(MAX_UNDO - 1)), prev]
      h.future = []
      setAndTrack(next)
      setHistoryTick((t) => t + 1)
    },
    [setAndTrack],
  )

  const undo = useCallback(() => {
    const h = historyRef.current
    if (!h.past.length) return
    h.future = [workbookRef.current, ...h.future]
    setAndTrack(h.past[h.past.length - 1])
    h.past = h.past.slice(0, -1)
    setHistoryTick((t) => t + 1)
  }, [setAndTrack])

  const redo = useCallback(() => {
    const h = historyRef.current
    if (!h.future.length) return
    h.past = [...h.past, workbookRef.current]
    setAndTrack(h.future[0])
    h.future = h.future.slice(1)
    setHistoryTick((t) => t + 1)
  }, [setAndTrack])

  useEffect(() => {
    function onKey(e) {
      const tag = e.target?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return // the cell editor's own undo
      if (!(e.ctrlKey || e.metaKey)) return
      const k = e.key.toLowerCase()
      if (k === 'z' && !e.shiftKey) {
        e.preventDefault()
        undo()
      } else if ((k === 'z' && e.shiftKey) || k === 'y') {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  useEffect(() => {
    function beforeUnload(e) {
      if (editSeqRef.current === savedSeqRef.current) return
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [])

  // --- actions ----------------------------------------------------------------

  async function create() {
    setBusy(true)
    try {
      loadSheet(await startSheet(documentId, identity))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function startAgain() {
    const ok = window.confirm(
      `Start again from V${meta.latest_version}? Every change made in the live sheet since it was started from V${meta.base_version} will be replaced.`,
    )
    if (!ok) return
    clearTimeout(timerRef.current)
    setBusy(true)
    try {
      loadSheet(await startSheet(documentId, identity))
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function reloadAfterConflict() {
    clearTimeout(timerRef.current)
    const sheet = await getSheet(documentId, identity)
    if (sheet) loadSheet(sheet)
  }

  async function download(format) {
    setBusy(true)
    try {
      if (!(await flush())) throw new Error('Save your changes before downloading.')
      await downloadSheet(documentId, format, identity)
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function back() {
    if (!(await flush()) && !window.confirm('Some changes are not saved. Leave anyway?')) return
    editSeqRef.current = savedSeqRef.current // nothing left to warn about on unload
    backToDocument(documentId)
  }

  // --- render -----------------------------------------------------------------

  const statusText = {
    saved: 'All changes saved',
    pending: 'Unsaved changes…',
    saving: 'Saving…',
    failed: `Not saved: ${saveError}`,
    conflict: 'Not saved: someone else changed this sheet',
  }[saveState]

  const bar = (
    <header className="sheet-bar">
      <button className="secondary" onClick={phase === 'ready' ? back : () => backToDocument(documentId)}>
        ← Documents
      </button>
      <strong className="sheet-title">{docName || 'Document'}</strong>
      <span className="muted small">Live sheet</span>
      {phase === 'ready' && (
        <>
          <span className={`sheet-status ${saveState}`}>{statusText}</span>
          <span className="sheet-actions">
            <button className="secondary" onClick={undo} disabled={!historyRef.current.past.length} title="Undo (Ctrl+Z)">
              Undo
            </button>
            <button className="secondary" onClick={redo} disabled={!historyRef.current.future.length} title="Redo (Ctrl+Shift+Z)">
              Redo
            </button>
            <button onClick={() => download('xlsx')} disabled={busy}>
              Download Excel
            </button>
            <button onClick={() => download('pdf')} disabled={busy}>
              Download PDF
            </button>
          </span>
        </>
      )}
    </header>
  )

  if (phase !== 'ready') {
    return (
      <div className="sheet-page">
        {bar}
        <div className="sheet-message">
          {phase === 'loading' && <p className="muted">Loading…</p>}
          {phase === 'error' && <p className="error">{error}</p>}
          {phase === 'missing' && (
            <>
              <p>This document has no live sheet yet.</p>
              {latestReady ? (
                <button onClick={create} disabled={busy}>
                  Create the live sheet from V{latestReady}
                </button>
              ) : (
                <p className="muted">Upload a version first.</p>
              )}
              {error && <p className="error">{error}</p>}
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="sheet-page">
      {bar}
      {saveState === 'conflict' && (
        <div className="sheet-banner error-banner">
          {saveError}{' '}
          <button className="secondary" onClick={reloadAfterConflict}>
            Reload
          </button>
        </div>
      )}
      {meta.latest_version && meta.latest_version > meta.base_version && (
        <div className="sheet-banner">
          V{meta.latest_version} has been uploaded since this live sheet was started from V{meta.base_version}.{' '}
          <button className="secondary" onClick={startAgain} disabled={busy}>
            Start again from V{meta.latest_version}
          </button>
        </div>
      )}
      {meta.warnings.length > 0 && (
        <details className="sheet-notes">
          <summary>
            {meta.warnings.length} note{meta.warnings.length === 1 ? '' : 's'} from importing the workbook
          </summary>
          <ul>
            {meta.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </details>
      )}
      <Grid workbook={workbook} sheetIndex={sheetIndex} onChange={change} />
      <nav className="sheet-tabs" aria-label="Sheets">
        {workbook.sheets.map((s, i) => (
          <button key={s.id} className={i === sheetIndex ? 'active' : ''} onClick={() => setSheetIndex(i)}>
            {s.name}
          </button>
        ))}
      </nav>
    </div>
  )
}
