// Field lists for the help desk dialogs (see ui/Dialog.jsx form()).

const opts = (map) => Object.entries(map).map(([value, label]) => ({ value, label }))

export function ticketFields(meta, { initial = {}, locations = [], partners = [], events = [], withEvent = false,
  withOwner = false } = {}) {
  const fields = [
    { name: 'title', label: 'What is the problem?', required: true, maxLength: 200, full: true, initial: initial.title,
      hint: 'A short summary, e.g. "No internet on stand A12"' },
    { name: 'category', label: 'Category', type: 'select', required: true, options: opts(meta.categories),
      initial: initial.category || 'wifi' },
    { name: 'severity', label: 'Severity', type: 'select', required: true, initial: initial.severity || 'P3',
      options: Object.entries(meta.severities).map(([value, s]) => ({ value, label: `${s.label} — ${s.meaning}` })) },
    { name: 'location', label: 'Location / area', maxLength: 200, list: locations, initial: initial.location },
    { name: 'stand', label: 'Stand', maxLength: 200, initial: initial.stand },
    { name: 'partner', label: 'Partner', maxLength: 200, list: partners, initial: initial.partner },
    { name: 'team', label: 'Team', type: 'select', required: true, options: opts(meta.teams),
      initial: initial.team || 'network' },
    { name: 'reporter_name', label: 'Reported by', maxLength: 200, initial: initial.reporter_name },
    { name: 'reporter_phone', label: 'Reporter phone', type: 'tel', maxLength: 40, initial: initial.reporter_phone },
    { name: 'description', label: 'Details', type: 'textarea', maxLength: 4000, initial: initial.description },
  ]
  if (withEvent) {
    fields.splice(1, 0, { name: 'event_id', label: 'Event', type: 'select', required: false,
      options: events.filter((e) => !e.archived).map((e) => ({ value: String(e.id), label: e.code })),
      initial: initial.event_id ? String(initial.event_id) : '' })
  }
  if (withOwner) {
    fields.push({ name: 'assignee', label: 'Owner', maxLength: 200, list: meta.assignees, initial: initial.assignee,
      hint: 'Leave empty to leave it unassigned' })
  }
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
