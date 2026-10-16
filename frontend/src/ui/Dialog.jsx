import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react'

// Accessible confirm/prompt dialogs, replacing window.confirm/prompt.
// Follows the WAI-ARIA APG modal dialog and alert dialog patterns:
// - native <dialog> opened with showModal(): the rest of the page is inert and
//   Escape closes it (Tab stays inside the dialog);
// - role="alertdialog" for confirmations, labelled by the title and described by the body;
// - focus starts on the least destructive action for destructive steps (Cancel),
//   on the input for prompts, otherwise on the main action;
// - focus returns to the element that opened the dialog.
//
//   const dialog = useDialog()
//   if (await dialog.confirm({ title: 'Delete event?', body: '…', confirmLabel: 'Delete', danger: true })) …
//   const name = await dialog.prompt({ title: 'Template name', label: 'Name', initial: 'LIS' })  // null if cancelled

const DialogContext = createContext(null)

export function DialogProvider({ children }) {
  const [request, setRequest] = useState(null) // { id, kind, options, resolve, opener }
  const nextId = useRef(0)
  const restoreTo = useRef(null)

  // Return focus to the opener once the dialog has left the page (APG: on close).
  useEffect(() => {
    if (request || !restoreTo.current) return
    const el = restoreTo.current
    restoreTo.current = null
    if (document.contains(el) && typeof el.focus === 'function') el.focus()
  }, [request])

  const open = useCallback((kind, options) => new Promise((resolve) => {
    nextId.current += 1
    setRequest({ id: nextId.current, kind, options, resolve, opener: document.activeElement })
  }), [])

  const api = useMemo(() => ({
    confirm: (options) => open('confirm', options),
    prompt: (options) => open('prompt', options),
  }), [open])

  function close(value) {
    const { resolve, opener } = request
    restoreTo.current = opener
    setRequest(null)
    resolve(value)
  }

  return (
    <DialogContext.Provider value={api}>
      {children}
      {request && <DialogView key={request.id} request={request} onClose={close} />}
    </DialogContext.Provider>
  )
}

export function useDialog() {
  const value = useContext(DialogContext)
  if (!value) throw new Error('useDialog needs a <DialogProvider> above it')
  return value
}

function DialogView({ request, onClose }) {
  const { kind, options } = request
  const {
    title, body, confirmLabel = 'OK', cancelLabel = 'Cancel', danger = false,
    label, initial = '', placeholder, hint, required = true, maxLength,
  } = options
  const ref = useRef(null)
  const inputRef = useRef(null)
  const cancelRef = useRef(null)
  const confirmRef = useRef(null)
  const [value, setValue] = useState(initial)
  const [error, setError] = useState(null)
  const titleId = useId()
  const bodyId = useId()
  const inputId = useId()
  const errorId = useId()

  useEffect(() => {
    const dialog = ref.current
    dialog.showModal()
    const target = kind === 'prompt' ? inputRef.current : danger ? cancelRef.current : confirmRef.current
    target?.focus()
    if (kind === 'prompt') inputRef.current?.select()
  }, [kind, danger])

  function submit(e) {
    e.preventDefault()
    if (kind === 'prompt') {
      const v = value.trim()
      if (required && !v) {
        setError(`Enter ${label ? label.toLowerCase() : 'a value'}`)
        inputRef.current?.focus()
        return
      }
      onClose(v)
    } else {
      onClose(true)
    }
  }

  const cancelValue = kind === 'prompt' ? null : false
  return (
    <dialog
      ref={ref}
      className={`dialog${danger ? ' danger' : ''}`}
      role={kind === 'confirm' ? 'alertdialog' : undefined}
      aria-labelledby={titleId}
      aria-describedby={body ? bodyId : undefined}
      aria-modal="true"
      onCancel={(e) => { e.preventDefault(); onClose(cancelValue) }}
    >
      <form method="dialog" onSubmit={submit} noValidate>
        <h2 id={titleId} className="dialog-title">{title}</h2>
        {body && <div id={bodyId} className="dialog-body">{typeof body === 'string' ? <p>{body}</p> : body}</div>}
        {kind === 'prompt' && (
          <div className={`field${error ? ' has-error' : ''}`}>
            <label htmlFor={inputId} className="field-label">{label}</label>
            {hint && <p className="field-hint">{hint}</p>}
            {error && <p id={errorId} className="field-error"><span className="visually-hidden">Error: </span>{error}</p>}
            <input
              ref={inputRef}
              id={inputId}
              value={value}
              placeholder={placeholder}
              maxLength={maxLength}
              aria-invalid={error ? 'true' : undefined}
              aria-describedby={error ? errorId : undefined}
              onChange={(e) => { setValue(e.target.value); setError(null) }}
            />
          </div>
        )}
        <div className="dialog-actions">
          <button type="button" ref={cancelRef} className="secondary" onClick={() => onClose(cancelValue)}>
            {cancelLabel}
          </button>
          <button type="submit" ref={confirmRef} className={danger ? 'danger-solid' : undefined}>{confirmLabel}</button>
        </div>
      </form>
    </dialog>
  )
}
