import { useMemo, useState } from 'react'
import type { SavedSession } from '../connection/api'
import {
  deleteChat, deleteSession, folderName, newChat, openSession, openSettings, renameChat, selectChat, useStore, type Chat,
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

function ChatRow({ chat, active, busy }: { chat: Chat; active: boolean; busy: boolean }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(chat.title)
  const [confirm, setConfirm] = useState(false)

  if (editing) {
    return (
      <form className="chat-row editing" onSubmit={e => { e.preventDefault(); renameChat(chat.id, title); setEditing(false) }}>
        <input
          className="input" value={title} autoFocus maxLength={80} aria-label="Chat title"
          onChange={e => setTitle(e.target.value)}
          onBlur={() => { renameChat(chat.id, title); setEditing(false) }}
          onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setTitle(chat.title); setEditing(false) } }}
        />
      </form>
    )
  }
  return (
    <div className={`chat-row${active ? ' active' : ''}`} onMouseLeave={() => setConfirm(false)}>
      <button type="button" className="chat-title" onClick={() => selectChat(chat.id)} disabled={busy && !active} title={chat.title}>
        {chat.title}
      </button>
      <div className="chat-tools">
        {confirm ? (
          <button type="button" className="icon-btn sm danger-on" onClick={() => deleteChat(chat.id)} aria-label="Confirm delete" title="Click again to delete">
            <Icon name="check" size={14} />
          </button>
        ) : (
          <>
            <button type="button" className="icon-btn sm" onClick={() => { setTitle(chat.title); setEditing(true) }} aria-label="Rename chat"><Icon name="edit" size={14} /></button>
            <button type="button" className="icon-btn sm" onClick={() => setConfirm(true)} aria-label="Delete chat"><Icon name="trash" size={14} /></button>
          </>
        )}
      </div>
    </div>
  )
}

/** A conversation from the session database that is not open here yet (often one from the terminal). */
function SessionRow({ session, busy }: { session: SavedSession; busy: boolean }) {
  const [confirm, setConfirm] = useState(false)
  const where = `${session.source === 'desktop' ? 'app' : 'terminal'} · ${folderName(session.cwd)}`
  return (
    <div className="chat-row saved" onMouseLeave={() => setConfirm(false)}>
      <button type="button" className="chat-title" onClick={() => void openSession(session.id)} disabled={busy}
        title={`${session.title || 'Untitled'}\n${session.cwd}`}>
        <span className="saved-title">{session.title || 'Untitled'}</span>
        <span className="saved-where">{where}</span>
      </button>
      <div className="chat-tools">
        {confirm ? (
          <button type="button" className="icon-btn sm danger-on" onClick={() => void deleteSession(session.id)} aria-label="Confirm delete" title="Click again to delete">
            <Icon name="check" size={14} />
          </button>
        ) : (
          <button type="button" className="icon-btn sm" onClick={() => setConfirm(true)} aria-label="Delete conversation"><Icon name="trash" size={14} /></button>
        )}
      </div>
    </div>
  )
}

export function Sidebar() {
  const chats = useStore(s => s.chats)
  const history = useStore(s => s.history)
  const currentId = useStore(s => s.currentId)
  const busy = useStore(s => s.streamingId !== null)
  const docs = useStore(s => s.server?.docs.length ?? 0)
  const [query, setQuery] = useState('')

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = q ? chats.filter(c => c.title.toLowerCase().includes(q) || c.messages.some(m => m.text.toLowerCase().includes(q))) : chats
    const out = new Map<string, Chat[]>()
    for (const c of list) {
      const g = group(c.updated)
      out.set(g, [...(out.get(g) ?? []), c])
    }
    return [...out.entries()]
  }, [chats, query])
  const saved = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? history.filter(h => h.title.toLowerCase().includes(q) || h.cwd.toLowerCase().includes(q)) : history
  }, [history, query])

  return (
    <aside className="sidebar">
      <div className="side-top drag">
        <Wordmark />
      </div>
      <button type="button" className="new-chat" onClick={newChat} disabled={busy}>
        <Icon name="plus" size={16} /> New chat
        <kbd>{/Mac/.test(navigator.userAgent) ? '⌘N' : 'Ctrl N'}</kbd>
      </button>
      <label className="side-search">
        <Icon name="search" size={14} />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search chats" aria-label="Search chats" />
      </label>
      <nav className="chat-list" aria-label="Chats">
        {groups.map(([label, list]) => (
          <div key={label} className="chat-group">
            <div className="group-label">{label}</div>
            {list.map(c => <ChatRow key={c.id} chat={c} active={c.id === currentId} busy={busy} />)}
          </div>
        ))}
        {!!saved.length && (
          <div className="chat-group">
            <div className="group-label" title="Conversations saved by the terminal app or an earlier install. Click one to continue it here.">
              Session history
            </div>
            {saved.map(h => <SessionRow key={h.id} session={h} busy={busy} />)}
          </div>
        )}
        {!chats.length && !saved.length && <p className="side-empty">Your chats show up here.</p>}
        {(!!chats.length || !!history.length) && !groups.length && !saved.length && <p className="side-empty">No chats match.</p>}
      </nav>
      <div className="side-bottom">
        <button type="button" className="side-link" onClick={() => openSettings('documents')}>
          <Icon name="doc" size={16} /> Documents <span className="count-pill">{docs}</span>
        </button>
        <button type="button" className="side-link" onClick={() => openSettings('models')}>
          <Icon name="gear" size={16} /> Settings
        </button>
      </div>
    </aside>
  )
}
