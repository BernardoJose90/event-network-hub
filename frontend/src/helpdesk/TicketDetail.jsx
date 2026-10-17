import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useDialog } from '../ui/Dialog.jsx'
import { SeverityBadge, SlaBadge, SourceTag, StatusBadge } from './badges.jsx'
import { duration, timeOfDay, usePolling } from './format.js'
import { resolveFields, ticketFields } from './forms.js'

const OPEN = ['new', 'acknowledged', 'in_progress', 'waiting']

function describe(entry, meta) {
  const label = (field, value) => {
    if (value === null || value === undefined) return '—'
    if (field === 'status') return meta.statuses[value] || value
    if (field === 'category') return meta.categories[value] || value
    if (field === 'team') return meta.teams[value] || value
    if (field === 'severity') return meta.severities[value]?.label || value
    return value
  }
  switch (entry.kind) {
    case 'created': return `Raised the ticket: ${entry.body}`
    case 'acknowledge': return 'Acknowledged the ticket'
    case 'assign': return entry.new ? `Assigned to ${entry.new}` : 'Unassigned the ticket'
    case 'status': return `Changed status from ${label('status', entry.old)} to ${label('status', entry.new)}`
    case 'field': return `Changed ${entry.field.replace('_', ' ')} from ${label(entry.field, entry.old)} to ${label(entry.field, entry.new)}`
    case 'merge': return entry.body
    case 'comment': return null
    default: return entry.body || entry.kind
  }
}

export default function TicketDetail({ id, api, meta, canEdit, identity, notify, onChanged, onClose, onOpen, now }) {
  const dialog = useDialog()
  const [ticket, setTicket] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [comment, setComment] = useState('')
  const [visibility, setVisibility] = useState('internal')
  const headingRef = useRef(null)
  const commentId = useId()

  const load = useCallback(async () => {
    try {
      setTicket(await api.get(id))
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [api, id])

  usePolling(load, 15000, [load])

  // Focus the ticket heading when another ticket is opened (keyboard and screen-reader users).
  useEffect(() => {
    if (ticket?.id === id) headingRef.current?.focus()
  }, [ticket?.id, id])

  async function act(fn, success) {
    setBusy(true)
    try {
      await fn()
      if (success) notify(success, 'ok')
      await load()
      onChanged()
    } catch (err) {
      notify(err.message, 'error', err.status === 409 ? load : undefined)
    } finally {
      setBusy(false)
    }
  }

  if (error) return <div className="ticket-detail panel"><p className="error" role="alert">{error}</p></div>
  if (!ticket) return <div className="ticket-detail panel" aria-busy="true"><span className="skeleton" /></div>

  const t = ticket
  const merged = !!t.merged_into
  const open = OPEN.includes(t.status)
  const edit = canEdit && !merged

  async function resolve(status) {
    const closing = status === 'closed'
    const values = await dialog.form({
      title: `${closing ? 'Close' : 'Resolve'} ticket #${t.id}`,
      body: 'Record the root cause and the fix. They go into the post-event report and the recurring issues list.',
      fields: resolveFields(t, closing),
      confirmLabel: closing ? 'Close ticket' : 'Resolve ticket',
    })
    if (values) await act(() => api.setStatus(t.id, t.revision, status, values), `Ticket #${t.id} ${closing ? 'closed' : 'resolved'}.`)
  }

  async function assign() {
    const values = await dialog.form({
      title: `Assign ticket #${t.id}`,
      fields: [
        { name: 'assignee', label: 'Owner', list: meta.assignees, maxLength: 200, initial: t.assignee,
          hint: 'Leave empty to unassign' },
        { name: 'team', label: 'Team', type: 'select', required: true, initial: t.team,
          options: Object.entries(meta.teams).map(([value, label]) => ({ value, label })) },
      ],
      confirmLabel: 'Assign',
    })
    if (values) await act(() => api.assign(t.id, t.revision, values.assignee, values.team), `Ticket #${t.id} assigned.`)
  }

  async function editTicket() {
    const values = await dialog.form({
      title: `Edit ticket #${t.id}`, wide: true, confirmLabel: 'Save changes',
      fields: ticketFields(meta, { initial: t }),
    })
    if (values) await act(() => api.update(t.id, t.revision, values), `Ticket #${t.id} updated.`)
  }

  async function merge() {
    const into = await dialog.prompt({
      title: `Merge #${t.id} into another ticket`,
      body: 'Use this when the same problem was reported twice. This ticket is closed as a duplicate, and its reporter follows the other ticket.',
      label: 'Ticket number to keep', hint: 'For example 12', maxLength: 10,
      validate: (v) => (/^#?\d+$/.test(v) && Number(v.replace('#', '')) !== t.id ? null : 'Enter another ticket number'),
      confirmLabel: 'Merge',
    })
    if (into) await act(() => api.merge(t.id, t.revision, Number(into.replace('#', ''))), `#${t.id} merged into #${into.replace('#', '')}.`)
  }

  async function addComment(e) {
    e.preventDefault()
    const body = comment.trim()
    if (!body) return
    await act(() => api.comment(t.id, body, visibility), visibility === 'reporter' ? 'Update sent to the reporter.' : 'Note added.')
    setComment('')
  }

  const facts = [
    ['Event', t.event_code || '—'],
    ['Category', meta.categories[t.category]],
    ['Team', meta.teams[t.team]],
    ['Owner', t.assignee || 'Unassigned'],
    ['Location', t.location || '—'],
    ['Stand', t.stand || '—'],
    ['Partner', t.partner || '—'],
    ['Reported by', [t.reporter_name, t.reporter_phone].filter(Boolean).join(' · ') || '—'],
    ['Raised', `${timeOfDay(t.created_at)} · ${t.source === 'whatsapp' ? 'WhatsApp' : `by ${t.created_by}`}`],
    ['Acknowledged', t.acknowledged_at
      ? `${timeOfDay(t.acknowledged_at)} by ${t.acknowledged_by} (${duration(t.sla.minutes_to_ack)})` : 'Not yet'],
    ['Resolved', t.resolved_at ? `${timeOfDay(t.resolved_at)} (${duration(t.sla.minutes_to_resolve)})` : 'Not yet'],
  ]
  const targets = meta.severities[t.severity]

  return (
    <article className="ticket-detail panel" aria-labelledby="ticket-title">
      <header className="ticket-head">
        <div>
          <h2 id="ticket-title" tabIndex={-1} ref={headingRef}>
            <span className="ticket-number">#{t.id}</span> {t.title}
          </h2>
          <div className="badge-row">
            <SeverityBadge severity={t.severity} meta={meta} />
            <StatusBadge status={t.status} meta={meta} />
            <SlaBadge ticket={t} now={now} />
            <SourceTag source={t.source} />
          </div>
        </div>
        <button type="button" className="icon-button close-detail" onClick={onClose} aria-label="Close ticket details">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18M6 6l12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      {merged && (
        <p className="notice info">
          Merged into <button type="button" className="link" onClick={() => onOpen(t.merged_into)}>#{t.merged_into}</button>.
          Work continues on that ticket.
        </p>
      )}

      {edit && (
        <div className="ticket-actions" role="group" aria-label="Ticket actions">
          {open && !t.acknowledged_at && (
            <button type="button" className="primary-cta" disabled={busy}
              onClick={() => act(() => api.acknowledge(t.id, t.revision), `Ticket #${t.id} acknowledged.`)}>
              Acknowledge
            </button>
          )}
          {['new', 'acknowledged', 'waiting'].includes(t.status) && (
            <button type="button" disabled={busy}
              onClick={() => act(() => api.setStatus(t.id, t.revision, 'in_progress'), `Ticket #${t.id} in progress.`)}>
              Start work
            </button>
          )}
          {t.status === 'in_progress' && (
            <button type="button" className="secondary" disabled={busy}
              onClick={() => act(() => api.setStatus(t.id, t.revision, 'waiting'), `Ticket #${t.id} is waiting.`)}>
              Waiting
            </button>
          )}
          {open && <button type="button" disabled={busy} onClick={() => resolve('resolved')}>Resolve…</button>}
          {t.status === 'resolved' && <button type="button" disabled={busy} onClick={() => resolve('closed')}>Close…</button>}
          {!open && (
            <button type="button" className="secondary" disabled={busy}
              onClick={() => act(() => api.setStatus(t.id, t.revision, 'in_progress'), `Ticket #${t.id} reopened.`)}>
              Reopen
            </button>
          )}
          {open && t.assignee !== identity.user && identity.user && (
            <button type="button" className="secondary" disabled={busy}
              onClick={() => act(() => api.assign(t.id, t.revision, identity.user), `Ticket #${t.id} is yours.`)}>
              Assign to me
            </button>
          )}
          <button type="button" className="secondary" disabled={busy} onClick={assign}>Assign…</button>
          <button type="button" className="secondary" disabled={busy} onClick={editTicket}>Edit…</button>
          {open && <button type="button" className="secondary" disabled={busy} onClick={merge}>Merge…</button>}
        </div>
      )}

      <section className="sla-box" aria-label="Service level">
        <div>
          <span className="muted small">Acknowledge within {targets.ack_minutes} min</span>
          <strong>{t.acknowledged_at ? `Done in ${duration(t.sla.minutes_to_ack)}` : t.sla.ack === 'breached' ? 'Overdue' : 'Pending'}</strong>
        </div>
        <div>
          <span className="muted small">Resolve within {duration(targets.resolve_minutes)}</span>
          <strong>{t.sla.minutes_to_resolve !== null ? `Done in ${duration(t.sla.minutes_to_resolve)}` : t.sla.resolve === 'breached' ? 'Overdue' : `By ${timeOfDay(t.sla.resolve_due)}`}</strong>
        </div>
      </section>

      <dl className="facts">
        {facts.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>

      {t.description && t.description !== t.title && (
        <section aria-label="Details"><h3>Details</h3><p className="pre-wrap">{t.description}</p></section>
      )}
      {(t.root_cause || t.resolution) && (
        <section aria-label="Resolution" className="resolution">
          <h3>Resolution</h3>
          <p><strong>Root cause:</strong> {t.root_cause}</p>
          <p><strong>Fix:</strong> {t.resolution}</p>
        </section>
      )}
      {t.duplicates.length > 0 && (
        <section aria-label="Duplicates">
          <h3>Duplicate reports</h3>
          <ul className="plain-list">
            {t.duplicates.map((d) => (
              <li key={d.id}>
                <button type="button" className="link" onClick={() => onOpen(d.id)}>#{d.id}</button> {d.title}
                {d.reporter_name ? ` — ${d.reporter_name}` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Activity">
        <h3>Activity</h3>
        <ol className="timeline">
          {t.activity.map((a) => {
            const text = describe(a, meta)
            return (
              <li key={a.id} className={a.kind === 'comment' ? `comment ${a.visibility}` : undefined}>
                <div className="timeline-meta">
                  <strong>{a.user}</strong> · <time dateTime={a.at}>{timeOfDay(a.at)}</time>
                  {a.kind === 'comment' && (
                    <span className="badge-pill comment-kind">{a.visibility === 'reporter' ? 'Sent to reporter' : 'Internal note'}</span>
                  )}
                </div>
                {text && <div>{text}</div>}
                {a.kind === 'comment' && <div className="pre-wrap">{a.body}</div>}
                {a.kind === 'status' && a.body && <div className="pre-wrap muted small">{a.body}</div>}
              </li>
            )
          })}
        </ol>
        {canEdit && (
          <form className="comment-form" onSubmit={addComment}>
            <label htmlFor={commentId} className="field-label">Add a note</label>
            <textarea id={commentId} rows={3} value={comment} maxLength={4000} onChange={(e) => setComment(e.target.value)} />
            <div className="row space-between">
              {t.source === 'whatsapp' ? (
                <fieldset className="radio-row">
                  <legend className="visually-hidden">Who sees this note</legend>
                  <label><input type="radio" name="visibility" value="internal" checked={visibility === 'internal'}
                    onChange={() => setVisibility('internal')} /> Internal note</label>
                  <label><input type="radio" name="visibility" value="reporter" checked={visibility === 'reporter'}
                    onChange={() => setVisibility('reporter')} /> Reply to reporter on WhatsApp</label>
                </fieldset>
              ) : <span className="muted small">Visible to the ICT team</span>}
              <button type="submit" disabled={busy || !comment.trim()}>
                {visibility === 'reporter' && t.source === 'whatsapp' ? 'Send update' : 'Add note'}
              </button>
            </div>
          </form>
        )}
      </section>
    </article>
  )
}
