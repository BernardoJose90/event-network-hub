import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react'

// Accessible confirm/prompt/form dialogs, replacing window.confirm/prompt.
// Follows the WAI-ARIA APG modal dialog and alert dialog patterns:
// - native <dialog> opened with showModal(): the rest of the page is inert and
//   Escape closes it (Tab stays inside the dialog);
// - role="alertdialog" for confirmations, labelled by the title and described by the body;
// - focus starts on the least destructive action for destructive steps (Cancel),
//   on the first field for prompts and forms, otherwise on the main action;
// - focus returns to the element that opened the dialog;
// - field errors are shown inline, linked with aria-describedby, and focus moves to the first one.
//
//   const dialog = useDialog()
//   if (await dialog.confirm({ title: 'Delete event?', body: '…', confirmLabel: 'Delete', danger: true })) …
//   const name = await dialog.prompt({ title: 'Template name', label: 'Name', initial: 'LIS' })  // null if cancelled
//     validate: (value) => error message or null, shown inline under the field
//   const values = await dialog.form({ title, fields: [{ name, label, type, options, required, hint, list,
//     initial, maxLength, validate, full }] })  // { name: value } or null
//     type: text | textarea | select | tel | date | number | checkboxes (value is an array)

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
    prompt: async (options) => {
      const { label, initial, placeholder, hint, required = true, maxLength, validate, ...rest } = options
      const values = await open('form', {
        ...rest,
        fields: [{ name: 'value', label, initial, placeholder, hint, required, maxLength, validate }],
      })
      return values ? values.value : null
    },
    form: (options) => open('form', options),
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

function Field({ field, value, error, onChange, inputRef, ids }) {
  const { name, label, type = 'text', options = [], hint, list, placeholder, maxLength, required } = field
  const describedBy = [hint && ids.hint, error && ids.error].filter(Boolean).join(' ') || undefined
  const common = {
    id: ids.input, name, value: value ?? '', ref: inputRef, maxLength, placeholder,
    'aria-invalid': error ? 'true' : undefined, 'aria-describedby': describedBy, 'aria-required': required || undefined,
    onChange: (e) => onChange(name, e.target.value),
  }
  if (type === 'checkboxes') {
    const chosen = new Set(value || [])
    return (
      <fieldset className={`field checkbox-field${error ? ' has-error' : ''}`} aria-describedby={describedBy}>
        <legend className="field-label">{label}{!required && <span className="muted"> (optional)</span>}</legend>
        {hint && <p id={ids.hint} className="field-hint">{hint}</p>}
        {error && <p id={ids.error} className="field-error"><span className="visually-hidden">Error: </span>{error}</p>}
        <div className="checkbox-list">
          {options.map((o, i) => (
            <label key={o.value} className="checkbox-option">
              <input type="checkbox" ref={i === 0 ? inputRef : undefined} checked={chosen.has(o.value)}
                onChange={(e) => {
                  const next = new Set(chosen)
                  if (e.target.checked) next.add(o.value)
                  else next.delete(o.value)
                  onChange(name, options.map((x) => x.value).filter((v) => next.has(v)))
                }} />
              {o.label}
            </label>
          ))}
          {options.length === 0 && <p className="muted small">Nothing to choose from yet.</p>}
        </div>
      </fieldset>
    )
  }
  return (
    <div className={`field${error ? ' has-error' : ''}`}>
      <label htmlFor={ids.input} className="field-label">
        {label}{!required && <span className="muted"> (optional)</span>}
      </label>
      {hint && <p id={ids.hint} className="field-hint">{hint}</p>}
      {error && <p id={ids.error} className="field-error"><span className="visually-hidden">Error: </span>{error}</p>}
      {type === 'textarea' ? (
        <textarea rows={4} {...common} />
      ) : type === 'select' ? (
        <select {...common}>
          {!required && <option value="">—</option>}
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : (
        <>
          <input type={['tel', 'date', 'number'].includes(type) ? type : 'text'} list={list?.length ? ids.list : undefined}
            autoComplete="off" {...common} />
          {list?.length > 0 && <datalist id={ids.list}>{list.map((v) => <option key={v} value={v} />)}</datalist>}
        </>
      )}
    </div>
  )
}

function DialogView({ request, onClose }) {
  const { kind, options } = request
  const { title, body, confirmLabel = 'OK', cancelLabel = 'Cancel', danger = false, fields = [], wide = false } = options
  const ref = useRef(null)
  const cancelRef = useRef(null)
  const confirmRef = useRef(null)
  const fieldRefs = useRef({})
  const [values, setValues] = useState(() => Object.fromEntries(
    fields.map((f) => [f.name, f.initial ?? (f.type === 'checkboxes' ? [] : '')])))
  const [errors, setErrors] = useState({})
  const titleId = useId()
  const bodyId = useId()
  const baseId = useId()

  useEffect(() => {
    ref.current.showModal()
    const first = fields[0] && fieldRefs.current[fields[0].name]
    const target = first || (danger ? cancelRef.current : confirmRef.current)
    target?.focus()
    if (first?.select && fields.length === 1) first.select()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function submit(e) {
    e.preventDefault()
    if (kind !== 'form') {
      onClose(true)
      return
    }
    const out = {}
    const problems = {}
    for (const f of fields) {
      const v = typeof values[f.name] === 'string' ? values[f.name].trim() : values[f.name]
      out[f.name] = v === '' ? null : v
    }
    for (const f of fields) {
      const v = out[f.name]
      const empty = Array.isArray(v) ? v.length === 0 : !v
      const problem = f.required && empty
        ? (f.type === 'checkboxes' || f.type === 'select' ? `Choose ${f.label.toLowerCase()}` : `Enter ${f.label.toLowerCase()}`)
        : f.validate?.(v ?? '', out)
      if (problem) problems[f.name] = problem
    }
    if (Object.keys(problems).length) {
      setErrors(problems)
      fieldRefs.current[fields.find((f) => problems[f.name]).name]?.focus()
      return
    }
    onClose(out)
  }

  const cancelValue = kind === 'form' ? null : false
  return (
    <dialog
      ref={ref}
      className={`dialog${danger ? ' danger' : ''}${wide ? ' wide' : ''}`}
      role={kind === 'confirm' ? 'alertdialog' : undefined}
      aria-labelledby={titleId}
      aria-describedby={body ? bodyId : undefined}
      aria-modal="true"
      onCancel={(e) => { e.preventDefault(); onClose(cancelValue) }}
    >
      <form method="dialog" onSubmit={submit} noValidate>
        <h2 id={titleId} className="dialog-title">{title}</h2>
        {body && <div id={bodyId} className="dialog-body">{typeof body === 'string' ? <p>{body}</p> : body}</div>}
        {fields.length > 0 && (
          <div className={`dialog-fields${fields.length > 3 ? ' grid' : ''}`}>
            {fields.map((f, i) => (
              <div key={f.name} className={f.full || f.type === 'textarea' ? 'span-all' : undefined}>
                <Field field={f} value={values[f.name]} error={errors[f.name]}
                  inputRef={(el) => { fieldRefs.current[f.name] = el }}
                  ids={{ input: `${baseId}-${i}`, hint: `${baseId}-${i}-hint`, error: `${baseId}-${i}-error`,
                    list: `${baseId}-${i}-list` }}
                  onChange={(name, v) => {
                    setValues((vs) => ({ ...vs, [name]: v }))
                    setErrors((es) => ({ ...es, [name]: undefined }))
                  }} />
              </div>
            ))}
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
