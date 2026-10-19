import { useState } from 'react'
import { useDialog } from '../ui/Dialog.jsx'
import { TITLES, recordName } from './columns.js'

// Comments on the requirements: anyone can comment on an item or the event and reply;
// network admins resolve. Each comment says which published version it was raised on.

const TYPE_TITLE = { wifi: 'Wi-Fi network', ethernet: 'Ethernet requirement', partner: 'Partner order' }

function when(iso) {
  return new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function Byline({ c }) {
  return (
    <p className="comment-by small">
      <strong>{c.user}</strong>
      {c.role === 'network-admin' && <span className="muted"> (network admin)</span>}
      <span className="muted"> · <time dateTime={c.created_at}>{when(c.created_at)}</time></span>
    </p>
  )
}

function Thread({ thread, showItem, actions }) {
  const resolved = !!thread.resolved_at
  return (
    <article className={`comment-thread${resolved ? ' resolved' : ''}`}
      aria-label={`Comment by ${thread.user}${thread.item_label ? ` on ${thread.item_label}` : ''}`}>
      <div className="comment-head">
        {showItem && (
          <span className="comment-item">
            {thread.record_type
              ? <>{TYPE_TITLE[thread.record_type]}: <strong>{thread.item_label}</strong>
                {thread.item_where && <span className="muted"> · {thread.item_where}</span>}</>
              : <strong>General</strong>}
          </span>
        )}
        <span className="tag">{thread.version ? `On V${thread.version}` : 'Before V1'}</span>
        <span className={`badge-pill ${resolved ? 'status-resolved' : 'status-acknowledged'}`}>
          {resolved ? 'Resolved' : 'Open'}
        </span>
      </div>
      <Byline c={thread} />
      <p className="comment-body">{thread.body}</p>
      {thread.replies.length > 0 && (
        <ul className="comment-replies" aria-label="Replies">
          {thread.replies.map((r) => (
            <li key={r.id}>
              <Byline c={r} />
              <p className="comment-body">{r.body}</p>
            </li>
          ))}
        </ul>
      )}
      {resolved && <p className="muted small">Resolved by {thread.resolved_by} · {when(thread.resolved_at)}</p>}
      {actions && <div className="row comment-actions">{actions}</div>}
    </article>
  )
}

const NAME_HINT = 'Your name is shown with your comment. Set it in "Your name" on the Events page.'

// Opens one item's comments in a dialog, with a box to add one (when commenting is allowed).
export function useItemComments({ api, canComment, notify, onCommentsChanged }) {
  const dialog = useDialog()
  return async function open(type, record) {
    const name = recordName(type, record.data)
    let threads
    try {
      threads = (await api.comments({ record_id: record.id })).threads
    } catch (err) {
      notify(err.message, 'error')
      return
    }
    const body = (
      <>
        {threads.length === 0
          ? <p className="muted">No comments on this item yet.</p>
          : (
            <div className="dialog-changelog comment-list" tabIndex={0} role="region" aria-label="Comments on this item">
              {threads.map((t) => <Thread key={t.id} thread={t} />)}
            </div>
          )}
        {threads.length > 0 && <p className="muted small">Replies and resolving are on the Comments page.</p>}
      </>
    )
    const title = `Comments: ${name}`
    if (!canComment) {
      await dialog.confirm({ title, wide: true, body, cancelLabel: null, confirmLabel: 'Close' })
      return
    }
    const values = await dialog.form({
      title, wide: true, body, confirmLabel: 'Add comment', cancelLabel: 'Close',
      fields: [{ name: 'body', label: 'Add a comment', type: 'textarea', rows: 3, required: true, maxLength: 2000,
        hint: `About ${TITLES[type].toLowerCase().replace(/s$/, '')} "${name}". ${NAME_HINT}` }],
    })
    if (!values) return
    try {
      await api.addComment(values.body, record.id)
      notify('Comment added. Network admins have been notified.', 'ok')
      onCommentsChanged()
    } catch (err) {
      notify(err.message, 'error')
    }
  }
}

const FILTERS = [['open', 'Open'], ['resolved', 'Resolved'], ['all', 'All']]

export function CommentsPanel({ api, comments, isAdmin, canComment, notify, onCommentsChanged }) {
  const dialog = useDialog()
  const [filter, setFilter] = useState('open')
  const [busy, setBusy] = useState(false)
  if (!comments) return <div className="skeleton-block" aria-hidden="true" />

  async function run(fn, message) {
    setBusy(true)
    try {
      await fn()
      if (message) notify(message, 'ok')
      onCommentsChanged()
    } catch (err) {
      notify(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function addGeneral() {
    const values = await dialog.form({
      title: 'Add a comment', confirmLabel: 'Add comment',
      body: `About the event in general${comments.latest_version ? `, on V${comments.latest_version}` : ''}. To comment on one item, use the comment button on its row.`,
      fields: [{ name: 'body', label: 'Comment', type: 'textarea', rows: 4, required: true, maxLength: 2000, hint: NAME_HINT }],
    })
    if (values) await run(() => api.addComment(values.body, null), 'Comment added. Network admins have been notified.')
  }

  async function reply(thread) {
    const values = await dialog.form({
      title: `Reply to ${thread.user}`, confirmLabel: 'Reply',
      body: <blockquote className="comment-quote">{thread.body}</blockquote>,
      fields: [{ name: 'body', label: 'Reply', type: 'textarea', rows: 3, required: true, maxLength: 2000 }],
    })
    if (values) await run(() => api.replyComment(thread.id, values.body), 'Reply added.')
  }

  const shown = comments.threads.filter((t) => filter === 'all' || (filter === 'open') === !t.resolved_at)
  const counts = { open: comments.open, resolved: comments.threads.filter((t) => t.resolved_at).length, all: comments.threads.length }
  return (
    <div className="comments-panel">
      <div className="row space-between comments-bar">
        <div className="segmented" role="group" aria-label="Show">
          {FILTERS.map(([key, label]) => (
            <button key={key} type="button" className={filter === key ? 'active' : ''} aria-pressed={filter === key}
              onClick={() => setFilter(key)}>
              {label} <span className="nav-count">{counts[key]}</span>
            </button>
          ))}
        </div>
        {canComment && <button type="button" onClick={addGeneral} disabled={busy}>Add general comment</button>}
      </div>
      <p className="muted small">
        {isAdmin
          ? 'Reply to say what changed, then resolve. Open comments stay open across versions.'
          : `Comments are about the latest published version${comments.latest_version ? ` (V${comments.latest_version})` : ''}. Use the comment button on a row to comment on one item.`}
      </p>
      {shown.length === 0 ? (
        <p className="muted">{filter === 'open' ? 'No open comments.' : 'Nothing here yet.'}</p>
      ) : (
        <div className="comment-list">
          {shown.map((t) => (
            <Thread key={t.id} thread={t} showItem actions={canComment && (
              <>
                <button type="button" className="secondary" onClick={() => reply(t)} disabled={busy}>Reply</button>
                {isAdmin && (t.resolved_at
                  ? <button type="button" className="secondary" disabled={busy}
                    onClick={() => run(() => api.reopenComment(t.id), 'Comment reopened.')}>Reopen</button>
                  : <button type="button" disabled={busy}
                    onClick={() => run(() => api.resolveComment(t.id), 'Comment resolved.')}>Resolve</button>)}
              </>
            )} />
          ))}
        </div>
      )}
    </div>
  )
}
