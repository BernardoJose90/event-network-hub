import { useState } from 'react'
import { useFeatures } from '../features.js'
import { loadIdentity, saveIdentity } from '../identity.js'
import TopBar from './TopBar.jsx'

// Shows the page only while its feature is on; otherwise a short "paused" page.
export default function FeatureGate({ feature, title, children }) {
  const features = useFeatures()
  const [identity, setIdentityState] = useState(loadIdentity)
  const setIdentity = (next) => {
    setIdentityState(next)
    saveIdentity(next)
  }
  if (features === null) return null // a moment while the server answers
  if (features[feature]) return children
  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <TopBar identity={identity} onChange={setIdentity} current={feature} />
      <main id="main" className="paused-page" tabIndex={-1}>
        <h1>{title} is paused</h1>
        <p>This area is switched off for now. Its data is kept and nothing has been deleted.</p>
        <p><a href="/">Go to Events</a></p>
      </main>
    </>
  )
}
