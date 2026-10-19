import { useEffect, useState } from 'react'

// Optional areas (help desk, reports) can be paused on the server (FEATURE_HELPDESK /
// FEATURE_REPORTS). Fetched once per page load; until it arrives, nothing optional is shown.
let request = null

export function loadFeatures() {
  request ??= fetch('/api/features')
    .then((res) => (res.ok ? res.json() : { helpdesk: false, reports: false }))
    .catch(() => ({ helpdesk: false, reports: false }))
  return request
}

export function useFeatures() {
  const [features, setFeatures] = useState(null)
  useEffect(() => {
    let live = true
    loadFeatures().then((f) => { if (live) setFeatures(f) })
    return () => { live = false }
  }, [])
  return features
}
