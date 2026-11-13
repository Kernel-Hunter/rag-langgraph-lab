import { memo, useMemo, useState, type MouseEvent } from 'react'
import type { Task } from '../connection/api'
import type { Msg, Thought } from '../store/store'
import { retry, runPlan, toast, undoChanges, useStore } from '../store/store'
import { TOOL_VERBS, ToolRow } from './ToolRow'
import { Icon } from '../ui-primitives/Icon'
import { LogoMark } from '../ui-primitives/Logo'
import { renderMarkdown } from './markdown'

function SourceCards({ msg, open, onToggle, highlight }: { msg: Msg; open: boolean; onToggle: () => void; highlight: string | null }) {
  const unique = useMemo(() => {
    const seen = new Set<string>()
    return msg.sources.filter(s => {
      const key = `${s.source ?? ''}|${s.page ?? ''}|${s.text.slice(0, 60)}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [msg.sources])
  if (!unique.length) return null
  return (
    <div className="sources">
      <button type="button" className="sources-toggle" onClick={onToggle} aria-expanded={open}>
        <Icon name="doc" size={14} />
        {unique.length} source{unique.length === 1 ? '' : 's'}
        <Icon name="chevron" size={14} className={open ? 'rot' : ''} />
      </button>
      {open && (
        <div className="source-list">
          {unique.map((s, i) => {
            // Same numbering the model sees in its context, so [page N] chips match these cards.
            const page = s.page === null || s.page === undefined ? null : String(s.page)
            return (
              <div key={i} className={`source-card${highlight !== null && page === highlight ? ' hl' : ''}`}>
                <div className="source-head">
                  <span className="source-name">{s.source ?? 'document'}</span>
                  {page && <span className="source-page">p.{page}</span>}
                </div>
                <p>{s.text.length > 420 ? `${s.text.slice(0, 420)}…` : s.text}</p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const count = (n: number): string => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
const seconds = (s: number): string => (s < 60 ? `${s < 10 ? s.toFixed(1) : Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`)

/** The model's thinking. Open while it is being written and in transcript view, one line otherwise. */
function ThoughtBlock({ thought, live, expanded }: { thought: Thought; live: boolean; expanded: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null)
  const shown = open ?? (live || expanded)
  const words = thought.text.trim().split(/\s+/).length
  return (
    <div className={`thought${live ? ' live' : ''}`}>
      <button type="button" className="thought-head" onClick={() => setOpen(!shown)} aria-expanded={shown}>
        <Icon name="spark" size={13} />
        <span className={live ? 'shimmer' : ''}>{live ? 'Thinking' : `Thought (${words} word${words === 1 ? '' : 's'})`}</span>
        <Icon name="chevron" size={13} className={`tool-chev${shown ? ' rot' : ''}`} />
      </button>
      {shown && <div className="thought-text">{thought.text.trim()}</div>}
    </div>
  )
}

/** The agent's task list: what is done, what it is doing, what is left. */
function TaskList({ items }: { items: Task[] }) {
  const done = items.filter(t => t.status === 'done').length
  return (
    <div className="tasks" aria-label={`Tasks, ${done} of ${items.length} done`}>
      {items.map((t, i) => (
        <div key={i} className={`task ${t.status}`}>
          <span className="mark" aria-hidden="true">{t.status === 'done' ? '✓' : t.status === 'active' ? '▸' : '○'}</span>
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  )
}

function UserMessage({ msg }: { msg: Msg }) {
  return (
    <div className="msg user">
      <span className="prompt" aria-hidden="true">guest@vesper:~$</span>
      <div className="bubble">{msg.text}</div>
    </div>
  )
}

function BotMessage({ msg, chatId, last }: { msg: Msg; chatId: string; last: boolean }) {
  const [open, setOpen] = useState(false)
  const [hl, setHl] = useState<string | null>(null)
  const transcript = useStore(s => s.transcript)
  const planning = useStore(s => s.server?.settings.agent_mode === 'plan')
  const busy = useStore(s => s.streamingId !== null)
  const edited = !msg.undone && msg.tools.some(t => (t.name === 'edit_file' || t.name === 'write_file') && t.ok)
  // Text, thinking and tool calls in the order they happened.
  const parts = useMemo(() => {
    type Part = { kind: 'text'; html: string; key: string } | { kind: 'tool'; index: number; key: string }
      | { kind: 'thought'; index: number; key: string }
    const out: Part[] = []
    let from = 0
    const steps = [
      ...msg.tools.map((t, index) => ({ kind: 'tool' as const, at: t.at, n: t.n, index, key: `t${t.id}` })),
      ...msg.thoughts.map((t, index) => ({ kind: 'thought' as const, at: t.at, n: t.n, index, key: `h${index}` })),
    ].sort((a, b) => a.at - b.at || a.n - b.n)
    for (const step of steps) {
      const at = Math.min(Math.max(step.at, from), msg.text.length)
      if (at > from && msg.text.slice(from, at).trim()) out.push({ kind: 'text', html: renderMarkdown(msg.text.slice(from, at)), key: `x${from}` })
      out.push({ kind: step.kind, index: step.index, key: step.key })
      from = at
    }
    if (msg.text.slice(from).trim()) out.push({ kind: 'text', html: renderMarkdown(msg.text.slice(from)), key: `x${from}` })
    return out
  }, [msg.text, msg.tools, msg.thoughts])
  const lastText = parts.map(p => p.kind).lastIndexOf('text')
  const running = msg.tools.some(t => t.output === undefined)
  const lastKind = parts[parts.length - 1]?.kind
  const thinking = msg.streaming && !msg.error && !running && (parts.length === 0 || lastKind === 'tool' || !!msg.draft)

  const onClick = (e: MouseEvent<HTMLDivElement>): void => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('.cite')
    if (!el) return
    setOpen(true)
    setHl(el.dataset.page ?? null)
  }

  const copy = (): void => {
    void navigator.clipboard.writeText(msg.text).then(() => toast('Copied'), () => toast('Copy failed', 'error'))
  }

  return (
    <div className="msg bot">
      <div className="avatar" aria-hidden="true"><LogoMark size={22} /></div>
      <div className="body">
        {!!msg.searches.length && !thinking && (
          <div className="searches">
            {msg.searches.map((q, i) => <span key={i} className="search-chip"><Icon name="search" size={12} />{q}</span>)}
          </div>
        )}
        {parts.map((p, i) => (p.kind === 'tool'
          ? <ToolRow key={p.key} step={msg.tools[p.index]!} chatId={chatId} msgId={msg.id} expanded={transcript} />
          : p.kind === 'thought'
            ? (
              <ThoughtBlock
                key={p.key} thought={msg.thoughts[p.index]!} expanded={transcript}
                live={!!msg.streaming && i === parts.length - 1 && !msg.draft}
              />
            )
            : (
            <div
              key={p.key}
              className={`md${msg.streaming && i === lastText && i === parts.length - 1 ? ' cursor' : ''}`}
              onClick={onClick}
              dangerouslySetInnerHTML={{ __html: p.html }}
            />
          )))}
        {!!msg.tasks?.length && <TaskList items={msg.tasks} />}
        {thinking && (
          <div className="thinking">
            <span className="shimmer">
              {msg.draft ? `Preparing ${TOOL_VERBS[msg.draft.name] ?? msg.draft.name} (${count(msg.draft.chars)} characters)`
                : msg.tools.length ? 'Thinking'
                : msg.searches.length ? `Searching “${msg.searches[msg.searches.length - 1]}”`
                  : msg.sources.length ? 'Reading the pages' : 'Working on it'}
            </span>
          </div>
        )}
        {msg.error && (
          <div className="msg-error" role="alert">
            <Icon name="warn" size={15} />
            <span>{msg.error}</span>
          </div>
        )}
        {!msg.streaming && msg.supported !== null && (
          <div className={`verdict ${msg.supported ? 'ok' : 'bad'}`}>
            <Icon name={msg.supported ? 'check' : 'warn'} size={13} />
            {msg.supported ? 'Supported by the sources' : 'Not fully supported by the sources'}
          </div>
        )}
        <SourceCards msg={msg} open={open} onToggle={() => { setOpen(o => !o); setHl(null) }} highlight={hl} />
        {!msg.streaming && msg.usage && (
          <div className="usage">
            {seconds(msg.usage.seconds)}
            {msg.usage.tools ? ` · ${msg.usage.tools} tool call${msg.usage.tools === 1 ? '' : 's'}` : ''}
            {` · ${msg.usage.estimated ? '~' : ''}${count(msg.usage.input)} in${msg.usage.cached ? ` (${count(msg.usage.cached)} cached)` : ''}, ${msg.usage.estimated ? '~' : ''}${count(msg.usage.output)} out`}
            {msg.undone ? ' · changes undone' : ''}
          </div>
        )}
        {last && !msg.streaming && !msg.error && msg.plan && planning && !busy && (
          <div className="plan-bar">
            <button type="button" className="btn primary" onClick={() => void runPlan()}>Run this plan</button>
            <span className="dim">or say what to change. Nothing has been changed yet.</span>
          </div>
        )}
        {!msg.streaming && (
          <div className={`actions${last ? ' show' : ''}`}>
            {msg.text && <button type="button" className="icon-btn sm" onClick={copy} title="Copy" aria-label="Copy answer"><Icon name="copy" size={15} /></button>}
            <button type="button" className="icon-btn sm" onClick={() => void retry(chatId, msg.id)} title="Retry" aria-label="Retry"><Icon name="retry" size={15} /></button>
            {last && edited && !busy && (
              <button type="button" className="icon-btn sm" onClick={() => void undoChanges()} title="Undo the file changes of this answer" aria-label="Undo file changes"><Icon name="undo" size={15} /></button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export const Message = memo(function Message({ msg, chatId, last }: { msg: Msg; chatId: string; last: boolean }) {
  return msg.role === 'user' ? <UserMessage msg={msg} /> : <BotMessage msg={msg} chatId={chatId} last={last} />
})
