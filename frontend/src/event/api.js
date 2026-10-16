// Client for /api/events/{documentId}/... (structured requirements).
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
    keepalive: method === 'DELETE', // pending deletes are sent while leaving the page
    headers: headers(identity, body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.status === 204) return null
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

export function eventApi(documentId, identity) {
  const base = `/api/events/${documentId}`
  return {
    overview: () => call('GET', `${base}/overview`, identity),
    importVersion: (replace = false) => call('POST', `${base}/import`, identity, { replace }),
    records: (type) => call('GET', `${base}/records/${type}`, identity),
    create: (type, body) => call('POST', `${base}/records/${type}`, identity, body),
    update: (type, id, revision, data) => call('PATCH', `${base}/records/${type}/${id}`, identity, { revision, data }),
    remove: (type, id, revision) => call('DELETE', `${base}/records/${type}/${id}?revision=${revision}`, identity),
    move: (type, id, revision, beforeId) =>
      call('POST', `${base}/records/${type}/${id}/move`, identity, { revision, before_id: beforeId }),
    reveal: (id) => call('POST', `${base}/records/wifi/${id}/reveal`, identity),
    addListValue: (kind, value) => call('POST', `${base}/lists/${kind}`, identity, { value }),
    renameListValue: (id, value) => call('PATCH', `${base}/lists/item/${id}`, identity, { value }),
    acceptListValue: (id) => call('POST', `${base}/lists/item/${id}/accept`, identity),
    deleteListValue: (id) => call('DELETE', `${base}/lists/item/${id}`, identity),
    addVenue: (name) => call('POST', `${base}/venues`, identity, { name }),
    renameVenue: (id, name) => call('PATCH', `${base}/venues/${id}`, identity, { name }),
    deleteVenue: (id) => call('DELETE', `${base}/venues/${id}`, identity),
    addGroup: (kind, name) => call('POST', `${base}/groups/${kind}`, identity, { name }),
    renameGroup: (id, name) => call('PATCH', `${base}/groups/${id}`, identity, { name }),
    deleteGroup: (id) => call('DELETE', `${base}/groups/${id}`, identity),
    history: (params = {}) => {
      const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v))
      return call('GET', `${base}/history?${q}`, identity)
    },
    changes: () => call('GET', `${base}/changes`, identity),
    publish: () => call('POST', `${base}/publish`, identity),
    saveAsTemplate: (name) => call('POST', '/api/templates', identity, { name, event_id: Number(documentId) }),
  }
}
