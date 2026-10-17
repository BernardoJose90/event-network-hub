import { useEffect, useRef, useState } from 'react'

// Durations like "4 min", "1 h 20 min", "2 d 3 h".
export function duration(minutes) {
  if (minutes === null || minutes === undefined) return '—'
  const m = Math.max(0, Math.round(minutes))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`
}

export const minutesSince = (iso, now = Date.now()) => (iso ? (now - new Date(iso).getTime()) / 60000 : null)

export function timeOfDay(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

// Countdown to an SLA due time: "due in 3 min" / "12 min overdue".
export function dueText(dueIso, now = Date.now()) {
  const left = (new Date(dueIso).getTime() - now) / 60000
  return left >= 0 ? `due in ${duration(left)}` : `${duration(-left)} overdue`
}

// The SLA that matters now: acknowledge while unacknowledged, else resolve.
export function slaFocus(ticket) {
  if (ticket.merged_into) return { kind: 'n/a', state: 'n/a' }
  if (ticket.sla.ack === 'pending' || (ticket.sla.ack === 'breached' && !ticket.acknowledged_at)) {
    return { kind: 'Acknowledge', state: ticket.sla.ack, due: ticket.sla.ack_due }
  }
  return { kind: 'Resolve', state: ticket.sla.resolve, due: ticket.sla.resolve_due }
}

// Re-render every `ms` so ages and countdowns stay current.
export function useNow(ms = 30000) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

// Call fn now and every `ms` while the page is visible (paused in background tabs).
export function usePolling(fn, ms, deps) {
  const saved = useRef(fn)
  saved.current = fn
  useEffect(() => {
    let timer = null
    const tick = () => {
      if (document.visibilityState === 'visible') saved.current()
    }
    saved.current()
    timer = setInterval(tick, ms)
    const onVisible = () => document.visibilityState === 'visible' && saved.current()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
}
