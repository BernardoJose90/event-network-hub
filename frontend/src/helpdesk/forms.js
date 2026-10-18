// Field lists for the help desk dialogs (see ui/Dialog.jsx form()).

const opts = (map) => Object.entries(map).map(([value, label]) => ({ value, label }))

export function ticketFields(meta, { initial = {}, locations = [], partners = [], events = [], withEvent = false,
  withOwner = false } = {}) {
  // Grouped into short sections so the form fits a laptop screen; the dialog scrolls inside if it must.
  const problem = 'The problem'
  const where = 'Where'
  const who = 'Who'
  const fields = [
    { section: problem, name: 'title', label: 'What is the problem?', required: true, maxLength: 200, full: true,
      initial: initial.title, hint: 'A short summary, e.g. "No internet on stand A12"' },
    { section: problem, name: 'category', label: 'Category', type: 'select', required: true,
      options: opts(meta.categories), initial: initial.category || 'wifi' },
    { section: problem, name: 'severity', label: 'Severity', type: 'select', required: true,
      initial: initial.severity || 'P3',
      options: Object.entries(meta.severities).map(([value, s]) => ({ value, label: `${s.label} — ${s.meaning}` })) },
    { section: problem, name: 'description', label: 'Details', type: 'textarea', rows: 2, maxLength: 4000,
      initial: initial.description },
    ...(withEvent ? [{ section: where, columns: 4, name: 'event_id', label: 'Event', type: 'select', required: false,
      options: events.filter((e) => !e.archived).map((e) => ({ value: String(e.id), label: e.code })),
      initial: initial.event_id ? String(initial.event_id) : '' }] : []),
    { section: where, columns: 4, name: 'location', label: 'Location / area', maxLength: 200, list: locations,
      initial: initial.location },
    { section: where, name: 'stand', label: 'Stand', maxLength: 200, initial: initial.stand },
    { section: where, name: 'partner', label: 'Partner', maxLength: 200, list: partners, initial: initial.partner },
    { section: who, columns: 4, name: 'team', label: 'Team', type: 'select', required: true, options: opts(meta.teams),
      initial: initial.team || 'network' },
    ...(withOwner ? [{ section: who, name: 'assignee', label: 'Owner', maxLength: 200, list: meta.assignees,
      initial: initial.assignee, placeholder: 'Unassigned' }] : []),
    { section: who, name: 'reporter_name', label: 'Reported by', maxLength: 200, initial: initial.reporter_name },
    { section: who, name: 'reporter_phone', label: 'Reporter phone', type: 'tel', maxLength: 40,
      initial: initial.reporter_phone },
  ]
  return fields
}

export function resolveFields(ticket, closing) {
  return [
    { name: 'root_cause', label: 'Root cause', type: 'textarea', required: true, maxLength: 4000,
      initial: ticket.root_cause, hint: 'What caused it, e.g. "Access point lost PoE after the stand build"' },
    { name: 'resolution', label: closing ? 'Resolution' : 'What fixed it', type: 'textarea', required: true,
      maxLength: 4000, initial: ticket.resolution, hint: 'What was done, so the next event can learn from it' },
  ]
}
