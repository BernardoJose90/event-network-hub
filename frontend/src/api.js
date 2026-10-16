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

async function uploadResult(res) {
  // created / unchanged / failed (502) all carry an upload result body.
  if (res.ok || res.status === 502) {
    const body = await res.json().catch(() => null)
    if (body && body.outcome) return body
  }
  throw new Error(await errorMessage(res))
}

// Admin only: creates a document named after the file, with the file as version 1.
export async function uploadNewDocument(file, identity) {
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

export async function downloadFile(documentId, version, format, identity) {
  const { blob, filename } = await fetchFile(documentId, version, format, identity)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
