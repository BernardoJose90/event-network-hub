// Thin wrapper around the backend API.
//
// TEST ONLY: identity is sent as X-Role / X-User headers chosen in the UI.
// Replace with real authentication (session cookie or bearer token) in production.

function identityHeaders({ role, user }) {
  const headers = {}
  if (role) headers['X-Role'] = role
  if (user) headers['X-User'] = user
  return headers
}

async function errorMessage(res) {
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join('; ')
    if (body.message) return body.message
  } catch {
    // not JSON
  }
  return `Request failed (HTTP ${res.status})`
}

async function json(res) {
  if (!res.ok) throw new Error(await errorMessage(res))
  return res.json()
}

export async function listDocuments() {
  return json(await fetch('/api/documents'))
}

export async function getDocument(documentId) {
  return json(await fetch(`/api/documents/${documentId}`))
}

async function uploadResult(res) {
  // created / unchanged / failed (502) all carry an upload result body.
  if (res.ok || res.status === 502) {
    const body = await res.json().catch(() => null)
    if (body && body.outcome) return body
  }
  throw new Error(await errorMessage(res))
}

// Admin only. The file must be named like 'LIS26 _ Event_Network_Requirements_V1.xlsx':
// the event code picks (or creates) the document and V<n> becomes that version.
export async function uploadDocument(file, identity) {
  const form = new FormData()
  form.append('file', file)
  return uploadResult(
    await fetch('/api/documents/upload', {
      method: 'POST',
      headers: identityHeaders(identity),
      body: form,
    }),
  )
}

// Item-level changelog of a published version (against the previous one, or `from`).
export async function getChangelog(documentId, params, identity) {
  return json(await fetch(`/api/events/${documentId}/changelog?${new URLSearchParams(params)}`,
    { headers: identityHeaders(identity) }))
}

// Event templates, for "Start from" when creating a new event.
export async function listTemplates(identity) {
  return json(await fetch('/api/templates', { headers: identityHeaders(identity) }))
}

// Admin only: create an event without a workbook, empty or from a template.
export async function createEvent({ eventCode, templateId }, identity) {
  return json(
    await fetch('/api/events', {
      method: 'POST',
      headers: { ...identityHeaders(identity), 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_code: eventCode, template_id: templateId }),
    }),
  )
}

// Admin only: archived events are read-only and listed separately; restore undoes it.
export async function archiveDocument(documentId, identity) {
  return json(await fetch(`/api/documents/${documentId}/archive`, { method: 'POST', headers: identityHeaders(identity) }))
}

export async function restoreDocument(documentId, identity) {
  return json(await fetch(`/api/documents/${documentId}/restore`, { method: 'POST', headers: identityHeaders(identity) }))
}

// Admin only. Events created from the template are not affected.
export async function deleteTemplate(templateId, identity) {
  const res = await fetch(`/api/templates/${templateId}`, { method: 'DELETE', headers: identityHeaders(identity) })
  if (!res.ok) throw new Error(await errorMessage(res))
}

// Admin only: deletes the document with all versions, files and download history.
export async function deleteDocument(documentId, identity) {
  const res = await fetch(`/api/documents/${documentId}`, {
    method: 'DELETE',
    headers: identityHeaders(identity),
  })
  if (!res.ok) throw new Error(await errorMessage(res))
}

export async function listVersions(documentId) {
  return json(await fetch(`/api/documents/${documentId}/versions`))
}

// Resolves with the upload result for created / unchanged / failed outcomes,
// rejects for validation and other errors.

function filenameFrom(res, fallback) {
  const header = res.headers.get('Content-Disposition') || ''
  const match = header.match(/filename="([^"]+)"/)
  return match ? match[1] : fallback
}

// format: 'pdf' (converted PDF) or 'xlsx' (the original upload).
// Resolves with { blob, filename, version }; every fetch is logged by the backend.
export async function fetchFile(documentId, version, format, identity) {
  const params = new URLSearchParams({ format })
  if (version) params.set('version', version)
  const res = await fetch(`/api/documents/${documentId}/download?${params}`, {
    headers: identityHeaders(identity),
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  return {
    blob: await res.blob(),
    filename: filenameFrom(res, `document.${format}`),
    version: res.headers.get('X-Document-Version'),
  }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function downloadFile(documentId, version, format, identity) {
  const { blob, filename } = await fetchFile(documentId, version, format, identity)
  saveBlob(blob, filename)
}

// Admin only: open a version in the in-browser editor (Collabora Online).
// Resolves with { editor_url, access_token, access_token_ttl, base_version }.
export async function startEdit(documentId, version, identity) {
  return json(
    await fetch(`/api/documents/${documentId}/versions/${version}/edit`, {
      method: 'POST',
      headers: identityHeaders(identity),
    }),
  )
}

// --- Live sheet (admin only) -------------------------------------------------

async function failed(res) {
  const err = new Error(await errorMessage(res))
  err.status = res.status
  return err
}

// Resolves with the live sheet, or null if the document doesn't have one yet.
export async function getSheet(documentId, identity) {
  const res = await fetch(`/api/documents/${documentId}/sheet`, { headers: identityHeaders(identity) })
  if (res.status === 404) return null
  if (!res.ok) throw await failed(res)
  return res.json()
}

// Create the live sheet from the latest ready version, or start it again.
export async function startSheet(documentId, identity) {
  const res = await fetch(`/api/documents/${documentId}/sheet`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...identityHeaders(identity) },
    body: '{}',
  })
  if (!res.ok) throw await failed(res)
  return res.json()
}

// Rejects with err.status === 409 when someone else saved in the meantime.
export async function saveSheet(documentId, data, revision, identity) {
  const res = await fetch(`/api/documents/${documentId}/sheet`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...identityHeaders(identity) },
    body: JSON.stringify({ data, revision }),
  })
  if (!res.ok) throw await failed(res)
  return res.json()
}

export async function downloadSheet(documentId, format, identity) {
  const res = await fetch(`/api/documents/${documentId}/sheet/download?format=${format}`, {
    headers: identityHeaders(identity),
  })
  if (!res.ok) throw await failed(res)
  saveBlob(await res.blob(), filenameFrom(res, `live-sheet.${format}`))
}
