import { useCallback, useEffect, useRef } from 'react'

// Delete with Undo: the row disappears at once, but the DELETE is only sent after
// UNDO_MS, so Undo simply cancels it. Leaving the tab or page sends pending deletes
// straight away (keepalive requests survive navigation).

export const UNDO_MS = 6000

export function usePendingDeletes(api, type, notify, onChanged, reload) {
  const pending = useRef(new Map()) // record id -> { record, timer }
  // Latest callbacks, so the unmount clean-up below runs once and never early.
  const deps = useRef({ api, type, notify, onChanged, reload })
  deps.current = { api, type, notify, onChanged, reload }

  const send = useCallback(async (record) => {
    const { api, type, notify, onChanged, reload } = deps.current
    pending.current.delete(record.id)
    try {
      await api.remove(type, record.id, record.revision)
      onChanged()
    } catch (err) {
      notify(err.message, 'error', err.status === 409 ? reload : undefined)
      reload()
    }
  }, [])

  const schedule = useCallback((record, message) => {
    const timer = setTimeout(() => send(record), UNDO_MS)
    pending.current.set(record.id, { record, timer })
    deps.current.notify(message, 'info', undefined, {
      label: 'Undo',
      run: () => {
        const entry = pending.current.get(record.id)
        if (!entry) return
        clearTimeout(entry.timer)
        pending.current.delete(record.id)
        deps.current.reload()
      },
    }, UNDO_MS)
  }, [send])

  useEffect(() => {
    const flush = () => {
      for (const { record, timer } of [...pending.current.values()]) {
        clearTimeout(timer)
        send(record)
      }
    }
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      flush()
    }
  }, [send])

  const isPending = useCallback((id) => pending.current.has(id), [])

  return { schedule, isPending }
}
