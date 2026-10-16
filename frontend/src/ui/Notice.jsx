// Status messages (WCAG 2.2 SC 4.1.3): both live regions are always in the page, so
// screen readers announce new messages without focus moving. Success and info go to
// role="status" (polite); errors to role="alert". Each message has an icon and a text
// label, so its meaning doesn't rely on colour alone.

const LABELS = { ok: 'Success', info: 'Note', error: 'Error' }

function Icon({ tone }) {
  const paths = {
    ok: 'M20 6 9 17l-5-5',
    info: 'M12 16v-4m0-4h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
    error: 'M12 8v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z',
  }
  return (
    <svg className="notice-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path d={paths[tone] || paths.info} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
        strokeLinejoin="round" />
    </svg>
  )
}

function Message({ notice, onDismiss }) {
  return (
    <div className={`notice ${notice.tone} toast`}>
      <Icon tone={notice.tone} />
      <div className="toast-text">
        <strong className="toast-label">{LABELS[notice.tone] || LABELS.info}</strong>{' '}
        <span>{notice.message}</span>
      </div>
      <div className="toast-actions">
        {notice.actions?.map((a) => (
          <button key={a.label} type="button" className="link" onClick={() => { a.run(); onDismiss() }}>{a.label}</button>
        ))}
        <button type="button" className="icon-button" onClick={onDismiss} aria-label="Dismiss message">
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
            <path d="M18 6 6 18M6 6l12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </div>
  )
}

// notice: { message, tone: 'ok' | 'info' | 'error', actions?: [{ label, run }] } or null
export function StatusMessages({ notice, onDismiss, className = '' }) {
  return (
    <div className={`status-messages ${className}`}>
      <div role="status" aria-live="polite" aria-atomic="true">
        {notice && notice.tone !== 'error' && <Message notice={notice} onDismiss={onDismiss} />}
      </div>
      <div role="alert" aria-atomic="true">
        {notice && notice.tone === 'error' && <Message notice={notice} onDismiss={onDismiss} />}
      </div>
    </div>
  )
}
