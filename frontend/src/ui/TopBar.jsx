import { ROLES } from '../identity.js'

// The app's top bar: brand, the areas (Events, Help desk, Reports) and the TEST ONLY identity.
const AREAS = [
  ['events', 'Events', '/'],
  ['helpdesk', 'Help desk', '/helpdesk'],
  ['reports', 'Reports', '/reports'],
]

export default function TopBar({ identity, onChange, current }) {
  return (
    <header className="topbar">
      <div className="topbar-start">
        <p className="brand">
          <img src="/websummit-mark.png" alt="Web Summit" className="brand-mark" />
          Event Network Hub
        </p>
        <nav aria-label="Main" className="app-nav">
          {AREAS.map(([key, label, href]) => (
            <a key={key} href={href} className={current === key ? 'active' : undefined}
              aria-current={current === key ? 'page' : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </div>
      <div className="identity">
        <span className="badge" title="Fake identity for local testing; replaced by real auth in production">
          TEST ONLY
        </span>
        <label>
          Your name
          <input
            value={identity.user}
            onChange={(e) => onChange({ ...identity, user: e.target.value })}
            placeholder="e.g. alice"
            maxLength={100}
          />
        </label>
        <label>
          Role
          <select value={identity.role} onChange={(e) => onChange({ ...identity, role: e.target.value })}>
            {ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  )
}
