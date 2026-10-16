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

export async function createDocument(name, identity) {
  return json(
    await fetch('/api/documents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...identityHeaders(identity) },
      body: JSON.stringify({ name }),
    }),
  )
}

export async function listVersions(documentId) {
  return json(await fetch(`/api/documents/${documentId}/versions`))
}

// Resolves with the upload result for created / unchanged / failed outcomes,
// rejects for validation and other errors.
export async function uploadVersion(documentId, file, identity) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`/api/documents/${documentId}/versions`, {
    method: 'POST',
    headers: identityHeaders(identity),
    body: form,
  })
  if (res.ok || res.status === 502) {
    const body = await res.json().catch(() => null)
    if (body && body.outcome) return body
  }
  throw new Error(await errorMessage(res))
}

function filenameFrom(res, fallback) {
  const header = res.headers.get('Content-Disposition') || ''
  const match = header.match(/filename="([^"]+)"/)
  return match ? match[1] : fallback
}

export async function downloadPdf(documentId, version, identity) {
  const query = version ? `?version=${encodeURIComponent(version)}` : ''
  const res = await fetch(`/api/documents/${documentId}/download${query}`, {
    headers: identityHeaders(identity),
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filenameFrom(res, 'document.pdf')
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
