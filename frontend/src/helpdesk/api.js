// Client for the help desk API (/api/tickets, /api/helpdesk).
// TEST ONLY identity headers, as in ../api.js.

function headers(identity, json = false) {
  const h = {}
  if (identity?.role) h['X-Role'] = identity.role
  if (identity?.user) h['X-User'] = identity.user
  if (json) h['Content-Type'] = 'application/json'
  return h
}

async function call(method, path, identity, body) {
  const res = await fetch(path, {
    method,
    headers: headers(identity, body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let payload = null
  try {
    payload = await res.json()
  } catch {
    // empty or non-JSON body
  }
  if (!res.ok) {
    let message = `Request failed (HTTP ${res.status})`
    if (typeof payload?.detail === 'string') message = payload.detail
    else if (Array.isArray(payload?.detail)) message = payload.detail.map((d) => d.msg).join('; ')
    const err = new Error(message)
    err.status = res.status
    throw err
  }
  return payload
}

const query = (params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export function helpdeskApi(identity) {
  const t = '/api/tickets'
  return {
    meta: () => call('GET', `${t}/meta`, identity),
    list: (params = {}) => call('GET', `${t}${query(params)}`, identity),
    get: (id) => call('GET', `${t}/${id}`, identity),
    dependencies: (id) => call('GET', `${t}/${id}/dependencies`, identity),
    create: (body) => call('POST', t, identity, body),
    update: (id, revision, changes) => call('PATCH', `${t}/${id}`, identity, { revision, changes }),
    acknowledge: (id, revision) => call('POST', `${t}/${id}/acknowledge`, identity, { revision }),
    assign: (id, revision, assignee, team) => call('POST', `${t}/${id}/assign`, identity, { revision, assignee, team }),
    setStatus: (id, revision, status, extra = {}) => call('POST', `${t}/${id}/status`, identity, { revision, status, ...extra }),
    comment: (id, body, visibility) => call('POST', `${t}/${id}/comments`, identity, { body, visibility }),
    merge: (id, revision, into) => call('POST', `${t}/${id}/merge`, identity, { revision, into }),
    report: (params) => call('GET', `${t}/report${query(params)}`, identity),
    recurring: () => call('GET', `${t}/recurring`, identity),
    exportUrl: (params) => `${t}/export${query(params)}`,
    setLiveEvent: (eventId) => call('PUT', '/api/helpdesk/live-event', identity, { event_id: eventId }),
    eventLists: (eventId) => call('GET', `/api/events/${eventId}/overview`, identity),
    partnerOrders: (eventId) => call('GET', `/api/events/${eventId}/records/partner`, identity),
  }
}

// Downloads go through fetch so the TEST ONLY headers are sent, then save via a blob link.
export async function download(url, identity) {
  const res = await fetch(url, { headers: headers(identity) })
  if (!res.ok) throw new Error(`Export failed (HTTP ${res.status})`)
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') || '')?.[1] || 'tickets'
  const href = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = href
  a.download = name
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}
