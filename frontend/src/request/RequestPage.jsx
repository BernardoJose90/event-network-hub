import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useDialog } from '../ui/Dialog.jsx'

// Public ICT request form, /request/<token>. The link (one per team and event) is the credential.
// GOV.UK patterns: error summary that takes focus and links to fields, inline errors, fieldsets
// with legends, "Not sure yet" as a valid answer, add-another, confirmation page.

const OTHER = '__other__'

async function call(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  let payload = null
  try { payload = await res.json() } catch { /* empty */ }
  if (!res.ok) {
    const detail = payload?.detail
    const err = new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : `Request failed (HTTP ${res.status})`)
    err.status = res.status
    throw err
  }
  return payload
}

function emptyLine(template) {
  return {
    kind: 'wired', area: '', area_other: '', room: '', purpose: '', needed_by: '', notes: '',
    device_type: '', ports: '', ports_unsure: false, poe: template?.poe || 'not_sure', bandwidth: '',
    network_name: '', devices: '', devices_unsure: false, coverage: '', password: 'yes',
  }
}

function toPayload(line) {
  const area = line.area === OTHER ? line.area_other : line.area
  const base = { kind: line.kind, area, room: line.room || null, purpose: line.purpose,
    needed_by: line.needed_by || null, notes: line.notes || null }
  if (line.kind === 'wired') {
    return { ...base, device_type: line.device_type || null, ports: line.ports_unsure ? null : Number(line.ports),
      poe: line.poe, bandwidth: line.bandwidth || null }
  }
  return { ...base, network_name: line.network_name || null, devices: line.devices_unsure ? null : Number(line.devices),
    coverage: line.coverage || null, password: line.password }
}

function validate(requester, lines) {
  const errors = []
  if (!requester.name.trim()) errors.push(['req-name', 'Enter your name'])
  if (requester.contact.trim().length < 3) errors.push(['req-contact', 'Enter an email address or phone number'])
  lines.forEach((l, i) => {
    const n = lines.length > 1 ? ` for workspace ${i + 1}` : ''
    if (!l.area) errors.push([`l${i}-area`, `Choose the area${n}`])
    else if (l.area === OTHER && !l.area_other.trim()) errors.push([`l${i}-area-other`, `Describe the area${n}`])
    if (!l.purpose.trim()) errors.push([`l${i}-purpose`, `Say what it's for${n}`])
    if (l.kind === 'wired' && !l.ports_unsure && !/^[1-9]\d*$/.test(l.ports)) {
      errors.push([`l${i}-ports`, `Enter how many cables you need${n}, or tick "Not sure yet"`])
    }
    if (l.kind === 'wifi' && !l.devices_unsure && !/^[1-9]\d*$/.test(l.devices)) {
      errors.push([`l${i}-devices`, `Enter how many devices will connect${n}, or tick "Not sure yet"`])
    }
  })
  return errors
}

function Radios({ legend, name, value, options, onChange, hint }) {
  return (
    <fieldset className="field radios">
      <legend className="field-label">{legend}</legend>
      {hint && <p className="field-hint">{hint}</p>}
      <div className="radio-row">
        {options.map(([v, label]) => (
          <label key={v}><input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} /> {label}</label>
        ))}
      </div>
    </fieldset>
  )
}

function Line({ index, line, count, form, errors, onChange, onRemove, onFixed }) {
  const err = (id) => errors.find(([k]) => k === id)?.[1]
  const id = (f) => `l${index}-${f}`
  const set = (patch) => {
    onChange({ ...line, ...patch })
    // A field's error goes away once it's changed (it's checked again on submit).
    onFixed(Object.keys(patch).map((k) => id(k.replace('_unsure', '').replace('area_other', 'area-other'))))
  }
  const field = (f, label, input, hint) => (
    <div className={`field${err(id(f)) ? ' has-error' : ''}`}>
      <label className="field-label" htmlFor={id(f)}>{label}</label>
      {hint && <p className="field-hint" id={`${id(f)}-hint`}>{hint}</p>}
      {err(id(f)) && <p className="field-error" id={`${id(f)}-error`}><span className="visually-hidden">Error: </span>{err(id(f))}</p>}
      {input}
    </div>
  )
  const described = (f, hint) => [hint && `${id(f)}-hint`, err(id(f)) && `${id(f)}-error`].filter(Boolean).join(' ') || undefined
  return (
    <fieldset className="panel request-line">
      <legend><h2 className="line-title">Workspace {count > 1 ? index + 1 : ''}</h2></legend>
      <Radios legend="What do you need?" name={`kind-${index}`} value={line.kind} onChange={(v) => set({ kind: v })}
        options={Object.entries(form.kinds)} />
      <div className="dialog-fields grid">
        {field('area', 'Area', (
          <select id={id('area')} value={line.area} aria-invalid={err(id('area')) ? 'true' : undefined}
            aria-describedby={described('area')} onChange={(e) => set({ area: e.target.value })}>
            <option value="">Choose an area</option>
            {form.areas.map((a) => <option key={a} value={a}>{a}</option>)}
            <option value={OTHER}>Not in the list</option>
          </select>
        ))}
        {line.area === OTHER
          ? field('area-other', 'Describe the area', (
            <input id={id('area-other')} value={line.area_other} maxLength={200} aria-invalid={err(id('area-other')) ? 'true' : undefined}
              aria-describedby={described('area-other')} onChange={(e) => set({ area_other: e.target.value })} />))
          : field('room', 'Room or spot (optional)', (
            <input id={id('room')} value={line.room} maxLength={200} onChange={(e) => set({ room: e.target.value })} />))}
        {field('purpose', 'What is it for?', (
          <input id={id('purpose')} value={line.purpose} maxLength={200} aria-invalid={err(id('purpose')) ? 'true' : undefined}
            aria-describedby={described('purpose', true)} onChange={(e) => set({ purpose: e.target.value })} />
        ), line.kind === 'wired' ? 'For example "Stream encoders for the main stage"' : 'For example "Photo uploads from the press area"')}
        {field('needed_by', 'Needed by (optional)', (
          <input id={id('needed_by')} type="date" value={line.needed_by} onChange={(e) => set({ needed_by: e.target.value })} />
        ))}
      </div>
      {line.kind === 'wired' ? (
        <div className="dialog-fields grid">
          {field('device_type', 'What will you plug in?', (
            <>
              <input id={id('device_type')} list={id('devices-list')} value={line.device_type} maxLength={200}
                onChange={(e) => set({ device_type: e.target.value })} />
              <datalist id={id('devices-list')}>{form.template.devices.map((d) => <option key={d} value={d} />)}</datalist>
            </>
          ))}
          <div className={`field${err(id('ports')) ? ' has-error' : ''}`}>
            <label className="field-label" htmlFor={id('ports')}>How many cables (Ethernet points)?</label>
            <p className="field-hint" id={`${id('ports')}-hint`}>One per device that needs a wired connection</p>
            {err(id('ports')) && <p className="field-error" id={`${id('ports')}-error`}><span className="visually-hidden">Error: </span>{err(id('ports'))}</p>}
            <input id={id('ports')} type="number" min="1" inputMode="numeric" value={line.ports} disabled={line.ports_unsure}
              aria-invalid={err(id('ports')) ? 'true' : undefined} aria-describedby={described('ports', true)}
              onChange={(e) => set({ ports: e.target.value })} />
            <label className="checkbox-option"><input type="checkbox" checked={line.ports_unsure}
              onChange={(e) => set({ ports_unsure: e.target.checked, ports: '' })} /> Not sure yet</label>
          </div>
          <Radios legend="Do the devices need power over the cable (PoE)?" name={`poe-${index}`} value={line.poe}
            onChange={(v) => set({ poe: v })} options={[['yes', 'Yes'], ['no', 'No'], ['not_sure', 'Not sure']]}
            hint="Cameras and some phones are powered through the cable" />
          {field('bandwidth', 'Speed needed', (
            <select id={id('bandwidth')} value={line.bandwidth} onChange={(e) => set({ bandwidth: e.target.value })}>
              <option value="">Not sure</option>
              {form.bandwidths.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          ))}
        </div>
      ) : (
        <div className="dialog-fields grid">
          {field('network_name', 'Network name you would like (optional)', (
            <input id={id('network_name')} value={line.network_name} maxLength={200} onChange={(e) => set({ network_name: e.target.value })} />
          ), 'ICT may adjust it to fit the naming rules')}
          <div className={`field${err(id('devices')) ? ' has-error' : ''}`}>
            <label className="field-label" htmlFor={id('devices')}>How many devices will connect?</label>
            {err(id('devices')) && <p className="field-error" id={`${id('devices')}-error`}><span className="visually-hidden">Error: </span>{err(id('devices'))}</p>}
            <input id={id('devices')} type="number" min="1" inputMode="numeric" value={line.devices} disabled={line.devices_unsure}
              aria-invalid={err(id('devices')) ? 'true' : undefined} aria-describedby={described('devices')}
              onChange={(e) => set({ devices: e.target.value })} />
            <label className="checkbox-option"><input type="checkbox" checked={line.devices_unsure}
              onChange={(e) => set({ devices_unsure: e.target.checked, devices: '' })} /> Not sure yet</label>
          </div>
          {field('coverage', 'Where should it reach? (optional)', (
            <input id={id('coverage')} value={line.coverage} maxLength={200} onChange={(e) => set({ coverage: e.target.value })} />
          ), 'For example "Press room and backstage"')}
          <Radios legend="Should it have a password?" name={`password-${index}`} value={line.password}
            onChange={(v) => set({ password: v })} options={[['yes', 'Yes'], ['no', 'No, open network']]} />
        </div>
      )}
      {field('notes', 'Anything else ICT should know? (optional)', (
        <textarea id={id('notes')} rows={2} maxLength={2000} value={line.notes} onChange={(e) => set({ notes: e.target.value })} />
      ))}
      {count > 1 && <button type="button" className="link danger-link" onClick={onRemove}>Remove workspace {index + 1}</button>}
    </fieldset>
  )
}

export default function RequestPage({ token }) {
  const dialog = useDialog()
  const [form, setForm] = useState(null)
  const [error, setError] = useState(null)
  const [requester, setRequester] = useState({ name: '', contact: '' })
  const [lines, setLines] = useState([])
  const [errors, setErrors] = useState([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const summaryRef = useRef(null)
  const doneRef = useRef(null)
  const nameId = useId()

  const load = useCallback(async () => {
    try {
      const data = await call('GET', `/api/request-links/${encodeURIComponent(token)}`)
      setForm(data)
      setLines((ls) => (ls.length ? ls : [emptyLine(data.template)]))
      document.title = `ICT request · ${data.team.label} · ${data.event} · Event Network Hub`
    } catch (err) {
      setError(err.message)
      document.title = 'ICT request · Event Network Hub'
    }
  }, [token])
  useEffect(() => { load() }, [load])

  useEffect(() => { if (errors.length) summaryRef.current?.focus() }, [errors])
  useEffect(() => { if (done) doneRef.current?.focus() }, [done])

  async function submit(e) {
    e.preventDefault()
    const problems = validate(requester, lines)
    setErrors(problems)
    if (problems.length) return
    setBusy(true)
    try {
      const result = await call('POST', `/api/request-links/${encodeURIComponent(token)}/requests`, {
        requester_name: requester.name.trim(), requester_contact: requester.contact.trim(), lines: lines.map(toPayload),
      })
      setDone(result)
      setLines([emptyLine(form.template)])
      load()
    } catch (err) {
      setErrors([['submit', err.message]])
    } finally {
      setBusy(false)
    }
  }

  async function answer(r) {
    const d = r.data
    const fields = r.kind === 'wired'
      ? [{ name: 'ports', label: 'How many cables (Ethernet points)?', type: 'number', required: false, initial: d.ports ? String(d.ports) : '',
        hint: 'Leave empty if you still don\'t know', validate: (v) => (!v || /^[1-9]\d*$/.test(String(v)) ? null : 'Enter a whole number') },
      { name: 'notes', label: 'Your answer or notes', type: 'textarea', maxLength: 2000, initial: d.notes }]
      : [{ name: 'devices', label: 'How many devices will connect?', type: 'number', required: false, initial: d.devices ? String(d.devices) : '',
        validate: (v) => (!v || /^[1-9]\d*$/.test(String(v)) ? null : 'Enter a whole number') },
      { name: 'notes', label: 'Your answer or notes', type: 'textarea', maxLength: 2000, initial: d.notes }]
    const values = await dialog.form({ title: `Update request #${r.id}`, body: r.review_note ? `ICT asked: ${r.review_note}` : undefined,
      fields, confirmLabel: 'Send update' })
    if (!values) return
    const line = { kind: r.kind, ...d }
    if (r.kind === 'wired') line.ports = values.ports ? Number(values.ports) : null
    else line.devices = values.devices ? Number(values.devices) : null
    line.notes = values.notes
    try {
      await call('PUT', `/api/request-links/${encodeURIComponent(token)}/requests/${r.id}`, { revision: r.revision, line })
      load()
    } catch (err) {
      setErrors([['submit', err.message]])
    }
  }

  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="topbar">
        <div className="topbar-start">
          <p className="brand"><img src="/websummit-mark.png" alt="Web Summit" className="brand-mark" />Event Network Hub</p>
        </div>
      </header>
      <main id="main" className="request-page" tabIndex={-1}>
        {error && (
          <div className="panel">
            <h1>ICT request</h1>
            <p className="error" role="alert">{error}</p>
          </div>
        )}
        {!form && !error && <div className="skeleton-block" aria-hidden="true" />}
        {form && done && (
          <div className="panel confirmation" tabIndex={-1} ref={doneRef}>
            <h1>Request sent</h1>
            <p>Your request number{done.requests.length > 1 ? 's are' : ' is'}{' '}
              <strong>{done.requests.map((r) => `#${r.id}`).join(', ')}</strong>.</p>
            <p>The ICT team will review {done.requests.length > 1 ? 'them' : 'it'} and may contact you.
              {done.late ? ' It arrived after the deadline, so it may not be possible to deliver in time.' : ''}</p>
            <p>You can follow progress at the bottom of this page.</p>
            <button type="button" onClick={() => setDone(null)}>Make another request</button>
          </div>
        )}
        {form && !done && (
          <form noValidate onSubmit={submit}>
            <span className="caption">{form.event} · {form.team.label}</span>
            <h1>Request a network connection</h1>
            <p>Ask for wired connections (Ethernet cables) or a private Wi-Fi network for each workspace your team uses.
              Telling us early means the right number of cables is installed, and none are wasted.</p>
            {form.deadline && (
              <p className={`notice ${form.late ? 'error' : 'info'}`}>
                {form.late
                  ? <><strong>The deadline ({form.deadline}) has passed.</strong> You can still send a request, but it may not be possible to deliver it in time.</>
                  : <>Send requests by <strong>{form.deadline}</strong>.</>}
              </p>
            )}
            {form.instructions && <p className="pre-wrap">{form.instructions}</p>}

            {errors.length > 0 && (
              <div className="error-summary" role="alert" tabIndex={-1} ref={summaryRef} aria-labelledby="error-summary-title">
                <h2 id="error-summary-title">There is a problem</h2>
                <ul>
                  {errors.map(([field, message]) => (
                    <li key={field + message}>
                      {field === 'submit' ? message : <a href={`#${field}`} onClick={(e) => { e.preventDefault(); document.getElementById(field)?.focus() }}>{message}</a>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <fieldset className="panel request-line">
              <legend><h2 className="line-title">Your details</h2></legend>
              <div className="dialog-fields grid">
                <div className={`field${errors.some(([k]) => k === 'req-name') ? ' has-error' : ''}`}>
                  <label className="field-label" htmlFor="req-name">Your name</label>
                  <input id="req-name" autoComplete="name" value={requester.name} maxLength={200}
                    aria-invalid={errors.some(([k]) => k === 'req-name') ? 'true' : undefined}
                    onChange={(e) => { setRequester((r) => ({ ...r, name: e.target.value })); setErrors((es) => es.filter(([k]) => k !== 'req-name')) }} />
                </div>
                <div className={`field${errors.some(([k]) => k === 'req-contact') ? ' has-error' : ''}`}>
                  <label className="field-label" htmlFor="req-contact">Email or phone</label>
                  <p className="field-hint" id={`${nameId}-contact-hint`}>So ICT can ask you questions about the request</p>
                  <input id="req-contact" autoComplete="email" value={requester.contact} maxLength={200}
                    aria-describedby={`${nameId}-contact-hint`} aria-invalid={errors.some(([k]) => k === 'req-contact') ? 'true' : undefined}
                    onChange={(e) => { setRequester((r) => ({ ...r, contact: e.target.value })); setErrors((es) => es.filter(([k]) => k !== 'req-contact')) }} />
                </div>
              </div>
            </fieldset>

            {lines.map((line, i) => (
              <Line key={i} index={i} line={line} count={lines.length} form={form} errors={errors}
                onFixed={(fixed) => setErrors((es) => es.filter(([k]) => !fixed.includes(k)))}
                onChange={(next) => setLines((ls) => ls.map((l, j) => (j === i ? next : l)))}
                onRemove={() => setLines((ls) => ls.filter((_, j) => j !== i))} />
            ))}
            <div className="row">
              {lines.length < 30 && (
                <button type="button" className="secondary" onClick={() => setLines((ls) => [...ls, emptyLine(form.template)])}>
                  + Add another workspace
                </button>
              )}
              <button type="submit" className="primary-cta" disabled={busy}>{busy ? 'Sending…' : 'Send request'}</button>
            </div>
          </form>
        )}
        {form && form.requests.length > 0 && (
          <section className="panel your-requests" aria-labelledby="your-requests">
            <h2 id="your-requests">{form.team.label} requests for {form.event}</h2>
            <table className="ov-table">
              <thead><tr><th scope="col">#</th><th scope="col">Request</th><th scope="col">Status</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
              <tbody>
                {form.requests.map((r) => (
                  <tr key={r.id}>
                    <td className="num">{r.id}</td>
                    <td>{form.kinds[r.kind]}: {r.data.purpose}<div className="muted small">{r.data.area} · by {r.requester_name}</div></td>
                    <td>{r.status_label}{r.review_note && <div className="small pre-wrap">ICT: {r.review_note}</div>}</td>
                    <td>{['submitted', 'needs_info'].includes(r.status) && (
                      <button type="button" className={r.status === 'needs_info' ? '' : 'secondary'} onClick={() => answer(r)}
                        aria-label={`Update request ${r.id}`}>{r.status === 'needs_info' ? 'Answer' : 'Update'}</button>
                    )}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </main>
    </>
  )
}
