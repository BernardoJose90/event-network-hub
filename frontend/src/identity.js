// TEST ONLY: role switcher standing in for real authentication.
export const ROLES = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'network-admin', label: 'Network admin' },
]

// TEST ONLY identity is remembered per browser so a page reload (e.g. a Vite
// restart) does not silently switch the user back to the viewer role.
const IDENTITY_KEY = 'portal-pdf-test.identity'

export function loadIdentity() {
  try {
    const saved = JSON.parse(localStorage.getItem(IDENTITY_KEY) || 'null')
    if (saved && ROLES.some((r) => r.value === saved.role) && typeof saved.user === 'string') return saved
  } catch {
    // storage unavailable or corrupt: fall back to defaults
  }
  return { role: 'viewer', user: '' }
}

export function saveIdentity(identity) {
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity))
  } catch {
    // storage unavailable: identity just won't survive reloads
  }
}
