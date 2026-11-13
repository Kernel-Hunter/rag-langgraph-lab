import { useMemo, useState } from 'react'
import {
  currentChat, deleteSession, folderName, newChat, openSession, openSettings, renameSession, selectChat, useStore,
} from '../store/store'
import { Icon } from '../ui-primitives/Icon'
import { Wordmark } from '../ui-primitives/Logo'

function group(updated: number): string {
  const day = 86_400_000
  const start = new Date(); start.setHours(0, 0, 0, 0)
  const t = start.getTime()
  if (updated >= t) return 'Today'
  if (updated >= t - day) return 'Yesterday'
  if (updated >= t - 7 * day) return 'Previous 7 days'
  if (updated >= t - 30 * day) return 'Previous 30 days'
  return 'Older'
}

interface Row {
  /** Session id once saved; the open chat's id before that. */
  id: string
  title: string
  updated: number
  cwd: string
  source: string
  saved: boolean
}

function ChatRow({ row, active, busy }: { row: Row; active: boolean; busy: boolean }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(row.title)
  const [confirm, setConfirm] = useState(false)
  const done = (): void => { setEditing(false); if (title.trim() && title.trim() !== row.title) void renameSession(row.id, title) }

  if (editing) {
    return (
      <form className="chat-row editing" onSubmit={e => { e.preventDefault(); done() }}>
        <input
          className="input" value={title} autoFocus maxLength={80} aria-label="Conversation title"
          onChange={e => setTitle(e.target.value)}
          onBlur={done}
          onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setTitle(row.title); setEditing(false) } }}
        />
      </form>
    )
  }
  const where = row.cwd ? `${row.source === 'desktop' ? '' : 'terminal · '}${folderName(row.cwd)}` : ''
  return (
    <div className={`chat-row${active ? ' active' : ''}`} onMouseLeave={() => setConfirm(false)}>
      <button type="button" className="chat-title" disabled={busy && !active} title={`${row.title}${row.cwd ? `\n${row.cwd}` : ''}`}
        onClick={() => { if (row.saved) void openSession(row.id); else selectChat(row.id) }} onDoubleClick={() => { setTitle(row.title); setEditing(true) }}>
        <span className="saved-title">{row.title || 'Untitled'}</span>
        {where && <span className="saved-where">{row.source !== 'desktop' && <Icon name="terminal" size={11} />}{where}</span>}
      </button>
      <div className="chat-tools">
        {confirm ? (
          <>
            <button type="button" className="icon-btn sm danger-on" onClick={() => { setConfirm(false); void deleteSession(row.id) }}
              aria-label="Confirm delete" title="Delete for good (also from the terminal's history)">
              <Icon name="trash" size={14} />
            </button>
            <button type="button" className="icon-btn sm" onClick={() => setConfirm(false)} aria-label="Keep it"><Icon name="close" size={14} /></button>
          </>
        ) : (
          <>
            <button type="button" className="icon-btn sm" onClick={() => { setTitle(row.title); setEditing(true) }} aria-label="Rename"><Icon name="edit" size={14} /></button>
            <button type="button" className="icon-btn sm" onClick={() => setConfirm(true)} aria-label="Delete"><Icon name="trash" size={14} /></button>
          </>
        )}
      </div>
    </div>
  )
}

export function Sidebar() {
  const history = useStore(s => s.history)
  const loaded = useStore(s => s.historyLoaded)
  const open = useStore(currentChat)
  const chats = useStore(s => s.chats)
  const busy = useStore(s => s.streamingId !== null)
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<'all' | 'here'>('all')
  const workspace = useStore(s => s.server?.settings.workspace ?? '')

  const groups = useMemo(() => {
    // Saved conversations, plus open chats the engine has not saved yet.
    const rows: Row[] = history.map(h => ({
      id: h.id, title: h.title, updated: h.updated_at * 1000, cwd: h.cwd, source: h.source, saved: true,
    }))
    const listed = new Set(history.map(h => h.id))
    for (const c of chats) { // open chats the list does not have yet (saved a moment ago, or not saved)
      if (c.messages.length && !(c.sessionId && listed.has(c.sessionId))) {
        rows.unshift({ id: c.sessionId ?? c.id, title: c.title, updated: c.updated, cwd: workspace, source: 'desktop', saved: !!c.sessionId })
      }
    }
    const q = query.trim().toLowerCase()
    const text = (r: Row): string => {
      const chat = chats.find(c => c.sessionId === r.id || c.id === r.id)
      return `${r.title} ${r.cwd} ${chat ? chat.messages.map(m => m.text).join(' ') : ''}`.toLowerCase()
    }
    const list = rows
      .filter(r => scope === 'all' || r.cwd === workspace)
      .filter(r => !q || text(r).includes(q))
    const out = new Map<string, Row[]>()
    for (const r of list) out.set(group(r.updated), [...(out.get(group(r.updated)) ?? []), r])
    return [...out.entries()]
  }, [history, chats, query, scope, workspace])

  const activeId = open?.sessionId ?? open?.id
  return (
    <aside className="sidebar">
      <div className="side-top drag">
        <Wordmark />
      </div>
      <button type="button" className="new-chat" onClick={() => { newChat(); window.dispatchEvent(new Event('vesper:focus-composer')) }} disabled={busy}>
        <Icon name="plus" size={16} /> New chat
        <kbd>{/Mac/.test(navigator.userAgent) ? '⌘N' : 'Ctrl N'}</kbd>
      </button>
      <label className="side-search">
        <Icon name="search" size={14} />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search conversations" aria-label="Search conversations" />
      </label>
      {!!workspace && (
        <div className="segmented mini side-scope" role="group" aria-label="Which conversations">
          <button type="button" className={scope === 'all' ? 'on' : ''} onClick={() => setScope('all')}>All</button>
          <button type="button" className={scope === 'here' ? 'on' : ''} onClick={() => setScope('here')} title={workspace}>This folder</button>
        </div>
      )}
      <nav className="chat-list" aria-label="Conversations">
        {groups.map(([label, list]) => (
          <div key={label} className="chat-group">
            <div className="group-label">{label}</div>
            {list.map(r => <ChatRow key={r.id} row={r} active={r.id === activeId} busy={busy} />)}
          </div>
        ))}
        {loaded && !groups.length && (
          <p className="side-empty">{query || scope === 'here' ? 'No conversations match.' : 'Your conversations from the app and the terminal show up here.'}</p>
        )}
      </nav>
      <div className="side-bottom">
        <button type="button" className="side-link" onClick={() => openSettings('models')}>
          <Icon name="gear" size={16} /> Settings
        </button>
      </div>
    </aside>
  )
}
