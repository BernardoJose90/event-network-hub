import { useCallback, useEffect, useId, useState } from 'react'
import { useDialog } from '../ui/Dialog.jsx'

// ICT requests for one event: links per team, deadline, and review. Approving a request
// creates the Ethernet item or Wi-Fi network in this event's requirements.

const STATUS_CLASS = { submitted: 'status-new', needs_info: 'status-waiting', approved: 'status-resolved', declined: 'sev-P4' }

function unknowns(r) {
  const d = r.data
  if (r.kind === 'wired') {
    return [d.ports ? null : 'ports', d.poe && d.poe !== 'not_sure' ? null : 'PoE', d.bandwidth ? null : 'bandwidth'].filter(Boolean)
  }
  return [d.devices ? null : 'devices'].filter(Boolean)
}

function needSummary(r) {
  const d = r.data
  if (r.kind === 'wired') {
    return [d.ports ? `${d.ports} port${d.ports === 1 ? '' : 's'}` : 'ports not sure', d.device_type,
      d.poe === 'yes' ? 'PoE' : null, d.bandwidth].filter(Boolean).join(' · ')
  }
  return [d.network_name && `“${d.network_name}”`, d.devices ? `${d.devices} devices` : 'devices not sure',
    d.password === 'no' ? 'open' : 'password'].filter(Boolean).join(' · ')
}

export function RequestsPanel({ api, overview, isAdmin, notify, onChanged }) {
  const dialog = useDialog()
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('open')
  const [copied, setCopied] = useState(null)
  const ids = { deadline: useId(), instructions: useId(), status: useId() }

  const load = useCallback(async () => {
    try {
      setData(await api.ictRequests())
    } catch (err) {
      notify(err.message, 'error')
    }
  }, [api, notify])
  useEffect(() => { load() }, [load])

  async function act(fn, message) {
    try {
      await fn()
      if (message) notify(message, 'ok')
      await load()
      onChanged()
    } catch (err) {
      notify(err.message, 'error', err.status === 409 ? load : undefined)
    }
  }

  async function copy(link) {
    try {
      await navigator.clipboard.writeText(link.url)
      setCopied(link.id)
      notify(`Link for ${data.teams[link.team]} copied.`, 'ok')
    } catch {
      setCopied(link.id) // clipboard blocked: the link is shown for manual copying
    }
  }

  async function emailLink(link) {
    const email = await dialog.prompt({
      title: `Email the ${data.teams[link.team]} link`,
      body: 'Sends the request link to the team contact. Deadline reminders (7 days and 1 day before) go to them too.',
      label: 'Email address', initial: link.contact_email || '', maxLength: 200, confirmLabel: 'Send link',
      validate: (v) => (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v) ? null : 'Enter an email address, like name@example.com'),
    })
    if (email) await act(() => api.emailRequestLink(link.id, email), `Link sent to ${email}.`)
  }

  async function saveSettings(e) {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    await act(() => api.saveRequestSettings(form.get('deadline') || null, form.get('instructions') || null), 'Request settings saved.')
  }

  async function approve(r) {
    const kind = r.kind === 'wired' ? 'ethernet' : 'wifi'
    const groups = overview.groups.filter((g) => g.kind === kind)
    const vlans = overview.lists.filter((i) => i.kind === 'vlan').map((i) => i.value)
    const areas = [...new Set(overview.lists.filter((i) => i.kind === 'location' || i.kind === 'building').map((i) => i.value))]
    const fields = [
      { name: 'venue_id', label: 'Venue', type: 'select', required: true, initial: String(overview.venues[0]?.id ?? ''),
        options: overview.venues.map((v) => ({ value: String(v.id), label: v.name })) },
      { name: 'group_id', label: kind === 'ethernet' ? 'Ethernet section' : 'Wi-Fi group', type: 'select', required: true,
        initial: String(groups[0]?.id ?? ''), options: groups.map((g) => ({ value: String(g.id), label: g.name })) },
      { name: 'vlan', label: 'VLAN', type: 'select', required: false, options: vlans.map((v) => ({ value: v, label: v })) },
      { name: 'name', label: kind === 'ethernet' ? 'Purpose (as it will appear)' : 'SSID', maxLength: 200,
        initial: kind === 'ethernet' ? `${data.teams[r.team]}: ${r.data.purpose}` : r.data.network_name },
    ]
    if (kind === 'ethernet') {
      fields.push({ name: 'location', label: 'Location', required: true, list: areas, initial: r.data.area,
        hint: 'Must be in the event\'s Setup lists', validate: (v) => (areas.includes(v) ? null : 'Choose a location from the Setup lists') })
      fields.push({ name: 'ports', label: 'Ethernet points', type: 'number', required: !r.data.ports,
        initial: r.data.ports ? String(r.data.ports) : '', hint: r.data.ports ? undefined : 'The requester wasn\'t sure: agree a number with them',
        validate: (v) => (!v || /^\d+$/.test(String(v)) ? null : 'Enter a whole number') })
    }
    fields.push({ name: 'note', label: 'Note to the requester', type: 'textarea', maxLength: 2000 })
    const values = await dialog.form({
      title: `Approve request #${r.id}`, wide: true, confirmLabel: 'Approve and add to requirements',
      body: `Adds ${kind === 'ethernet' ? 'an Ethernet item' : 'a Wi-Fi network'} to this event's requirements. It goes out with the next published version.`,
      fields,
    })
    if (!values) return
    const body = {
      revision: r.revision, venue_id: Number(values.venue_id), group_id: Number(values.group_id), vlan: values.vlan,
      name: values.name, note: values.note, location: values.location, ports: values.ports ? Number(values.ports) : null,
    }
    await act(() => api.approveRequest(r.id, body), `Request #${r.id} approved and added to the requirements.`)
  }

  async function review(r, kind) {
    const note = await dialog.prompt({
      title: kind === 'decline' ? `Decline request #${r.id}` : `Ask for more information on #${r.id}`,
      label: kind === 'decline' ? 'Reason' : 'What do you need to know?', maxLength: 2000,
      hint: 'The requester sees this on their request link',
      confirmLabel: kind === 'decline' ? 'Decline' : 'Send question', danger: kind === 'decline',
    })
    if (!note) return
    await act(() => (kind === 'decline' ? api.declineRequest(r.id, r.revision, note) : api.requestNeedsInfo(r.id, r.revision, note)),
      kind === 'decline' ? `Request #${r.id} declined.` : `Question sent for request #${r.id}.`)
  }

  if (!data) return <div className="skeleton-block" aria-hidden="true" />

  const activeLinks = data.links.filter((l) => !l.revoked_at)
  const shown = data.requests.filter((r) => (status === 'open' ? ['submitted', 'needs_info'].includes(r.status)
    : status === 'all' ? true : r.status === status))
  const s = data.summary

  return (
    <div className="requests">
      <p className="muted">
        Internal teams request wired connections and private Wi-Fi per workspace through their own link, instead of ICT
        guessing cable numbers. Approving a request adds it to the requirements.
      </p>

      <div className="tiles">
        {Object.entries(s.by_team).map(([key, t]) => (
          <div key={key} className="panel tile">
            <span className="tile-label">{t.label}</span>
            <span className="tile-value">{t.requests}</span>
            <span className="small">{t.open} to review · {t.ports_requested} ports requested{t.ports_unknown ? ` · ${t.ports_unknown} not sure` : ''}</span>
            {t.late > 0 && <span className="small flag-text">{t.late} after the deadline</span>}
          </div>
        ))}
        <div className={`panel tile${s.unknowns ? ' warn' : ''}`}>
          <span className="tile-label">With unknowns</span>
          <span className="tile-value">{s.unknowns}</span>
          <span className="small">Follow up before the build</span>
        </div>
      </div>

      <div className="board-columns">
        <section className="panel" aria-labelledby="req-links">
          <h3 id="req-links">Request links</h3>
          <p className="muted small">Send each team its own link. Anyone with the link can submit for that team, so turn
            off links you no longer need.</p>
          <ul className="plain-list link-list">
            {Object.entries(data.teams).map(([team, label]) => {
              const link = activeLinks.find((l) => l.team === team)
              return (
                <li key={team}>
                  <strong>{label}</strong>
                  {link ? (
                    <span className="row nowrap">
                      <button type="button" className="secondary" onClick={() => copy(link)}>Copy link</button>
                      {isAdmin && <button type="button" className="secondary" onClick={() => emailLink(link)}
                        aria-label={`Email the ${label} link`}>Email link…</button>}
                      {isAdmin && <button type="button" className="link danger-link" onClick={() => act(() => api.revokeRequestLink(link.id), `Link for ${label} turned off.`)}>Turn off</button>}
                    </span>
                  ) : isAdmin ? (
                    <button type="button" className="secondary" onClick={() => act(() => api.createRequestLink(team), `Link for ${label} created.`)}>Create link</button>
                  ) : <span className="muted small">No link</span>}
                  {link?.contact_email && <span className="muted small link-contact">Contact: {link.contact_email}</span>}
                  {link && copied === link.id && <input className="link-url" readOnly value={link.url} aria-label={`Request link for ${label}`} onFocus={(e) => e.target.select()} />}
                </li>
              )
            })}
          </ul>
        </section>
        <section className="panel" aria-labelledby="req-settings">
          <h3 id="req-settings">Deadline and instructions</h3>
          {isAdmin ? (
            <form className="stack" onSubmit={saveSettings}>
              <label className="field-label" htmlFor={ids.deadline}>Requests due by</label>
              <input id={ids.deadline} name="deadline" type="date" defaultValue={data.settings.deadline || ''} />
              <label className="field-label" htmlFor={ids.instructions}>Instructions shown on the form</label>
              <textarea id={ids.instructions} name="instructions" rows={3} maxLength={2000} defaultValue={data.settings.instructions || ''} />
              <button type="submit">Save</button>
            </form>
          ) : (
            <p>{data.settings.deadline ? `Requests due by ${data.settings.deadline}.` : 'No deadline set.'}</p>
          )}
          {data.late_now && <p className="small flag-text">The deadline has passed: new requests are marked late.</p>}
        </section>
      </div>

      <div className="row space-between req-head">
        <h3>Requests</h3>
        <label className="inline-label" htmlFor={ids.status}>Show
          <select id={ids.status} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">To review</option>
            {Object.entries(data.statuses).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            <option value="all">All</option>
          </select>
        </label>
      </div>
      <div className="table-wrap panel">
        <table className="hd-table">
          <caption className="visually-hidden">ICT requests, newest first</caption>
          <thead>
            <tr><th scope="col">#</th><th scope="col">Team</th><th scope="col">Request</th><th scope="col">Needs</th>
              <th scope="col">Requested by</th><th scope="col">Status</th>{isAdmin && <th scope="col"><span className="visually-hidden">Actions</span></th>}</tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const gaps = unknowns(r)
              return (
                <tr key={r.id}>
                  <td className="num">{r.id}</td>
                  <td>{data.teams[r.team]}</td>
                  <td>
                    <strong>{data.kinds[r.kind]}</strong>: {r.data.purpose}
                    <div className="muted small">{[r.data.area, r.data.room, r.data.needed_by && `needed by ${r.data.needed_by}`].filter(Boolean).join(' · ')}</div>
                    {r.data.notes && <div className="small pre-wrap">{r.data.notes}</div>}
                  </td>
                  <td>
                    {needSummary(r)}
                    {gaps.length > 0 && <div className="small flag-text">Not sure: {gaps.join(', ')}</div>}
                    {r.late && <div className="small flag-text">After the deadline</div>}
                  </td>
                  <td>{r.requester_name}<div className="muted small">{r.requester_contact}</div></td>
                  <td>
                    <span className={`badge-pill ${STATUS_CLASS[r.status]}`}>{data.statuses[r.status]}</span>
                    {r.review_note && <div className="muted small pre-wrap">{r.review_note}</div>}
                  </td>
                  {isAdmin && (
                    <td className="nowrap-cell">
                      {['submitted', 'needs_info'].includes(r.status) && (
                        <span className="row nowrap">
                          <button type="button" onClick={() => approve(r)} aria-label={`Approve request ${r.id}`}>Approve…</button>
                          <button type="button" className="secondary" onClick={() => review(r, 'info')} aria-label={`Ask for more information on request ${r.id}`}>Ask…</button>
                          <button type="button" className="danger" onClick={() => review(r, 'decline')} aria-label={`Decline request ${r.id}`}>Decline…</button>
                        </span>
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
            {shown.length === 0 && <tr><td colSpan={isAdmin ? 7 : 6} className="empty-cell muted">No requests here.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
