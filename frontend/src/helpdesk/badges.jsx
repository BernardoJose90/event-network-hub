import { dueText, slaFocus } from './format.js'

// Badges carry text (never colour alone), so they read the same to everyone.

export function SeverityBadge({ severity, meta }) {
  return (
    <span className={`badge-pill sev-${severity}`} title={meta?.severities[severity]?.meaning}>
      {meta?.severities[severity]?.label || severity}
    </span>
  )
}

export function StatusBadge({ status, meta }) {
  return <span className={`badge-pill status-${status}`}>{meta?.statuses[status] || status}</span>
}

export function SlaBadge({ ticket, now }) {
  const focus = slaFocus(ticket)
  if (focus.state === 'n/a') return <span className="muted small">—</span>
  if (focus.state === 'met') return <span className="badge-pill sla-met">{focus.kind === 'Resolve' ? 'Resolved in SLA' : 'In SLA'}</span>
  const text = focus.state === 'breached' && ticket.status !== 'resolved' && ticket.status !== 'closed'
    ? `${focus.kind}: ${dueText(focus.due, now)}`
    : focus.state === 'breached' ? 'SLA missed' : `${focus.kind} ${dueText(focus.due, now)}`
  return <span className={`badge-pill sla-${focus.state}`}>{text}</span>
}

export function SourceTag({ source }) {
  return source === 'whatsapp'
    ? <span className="badge-pill source-whatsapp">WhatsApp</span>
    : <span className="badge-pill source-manual">ICT</span>
}
