// An item-level changelog (from GET /api/events/{id}/changelog): what was added, removed and
// changed between two versions, with old → new values and Ethernet point totals.

const KIND = { added: 'Added', removed: 'Removed', changed: 'Changed' }

// headingLevel: the level of its section headings (3 inside a dialog, 4 inside a panel with an h3).
export default function Changelog({ log, compact = false, limit, headingLevel = 3 }) {
  const H = `h${headingLevel}`
  if (!log) return null
  const notices = (log.notices || []).map((n) => <p key={n} className="changelog-note"><strong>Note:</strong> {n}</p>)
  if (log.first) {
    return (
      <div className="changelog">
        {notices}
        {log.note && <p className="changelog-note"><strong>Note:</strong> {log.note}</p>}
        <p>First version: {log.items} items.</p>
      </div>
    )
  }
  const { added, removed, changed } = log.counts
  if (!added && !removed && !changed) {
    return (
      <div className="changelog">
        {notices}
        {log.note && <p className="changelog-note"><strong>Note:</strong> {log.note}</p>}
        <p className="muted">No changes to the requirements{log.from_version ? ` since V${log.from_version}` : ''}.</p>
      </div>
    )
  }
  let shown = 0
  const cap = (items) => {
    if (!limit) return items
    const room = Math.max(0, limit - shown)
    shown += Math.min(items.length, room)
    return items.slice(0, room)
  }
  const total = added + removed + changed
  return (
    <div className={`changelog${compact ? ' compact' : ''}`}>
      {notices}
      {log.note && <p className="changelog-note"><strong>Note:</strong> {log.note}</p>}
      <p className="changelog-counts">
        <span className="badge-pill status-resolved">{added} added</span>{' '}
        <span className="badge-pill sev-P1">{removed} removed</span>{' '}
        <span className="badge-pill status-acknowledged">{changed} changed</span>
        {log.from_version ? <span className="muted small"> since V{log.from_version}</span> : null}
      </p>
      {log.sections.map((s) => {
        const rows = [
          ...cap(s.added).map((i) => ['added', i]),
          ...cap(s.removed).map((i) => ['removed', i]),
          ...cap(s.changed).map((i) => ['changed', i]),
        ]
        if (!rows.length) return null
        return (
          <section key={s.type} aria-label={s.title}>
            <H className="cl-heading">{s.title}</H>
            <ul className="changelog-list">
              {rows.map(([kind, i], n) => (
                <li key={`${kind}-${n}`} className={`cl-${kind}`}>
                  <span className="cl-kind">{KIND[kind]}</span>{' '}
                  <strong>{i.name}</strong>
                  {i.where && <span className="muted small"> · {i.where}</span>}
                  {kind !== 'changed' && i.summary && <span className="small"> — {i.summary}</span>}
                  {kind === 'changed' && (
                    <ul className="cl-fields">
                      {i.fields.map((f) => (
                        <li key={f.field}>
                          {f.label}: {f.old ? <><del>{f.old}</del> → </> : null}<ins>{f.new}</ins>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )
      })}
      {limit && total > limit && <p className="muted small">…and {total - limit} more</p>}
      {log.points.length > 0 && (
        <section aria-label="Ethernet points">
          <H className="cl-heading">Ethernet points</H>
          <ul className="changelog-list">
            {log.points.map((p) => (
              <li key={p.venue}>{p.venue}: {p.old ?? '—'} → <strong>{p.new}</strong>
                {p.old !== null && p.old !== undefined && (
                  <span className="muted small"> ({p.new - p.old >= 0 ? '+' : ''}{p.new - p.old})</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
