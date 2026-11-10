import { memo, useMemo, useState, type MouseEvent } from 'react'
import type { Msg } from '../store/store'
import { retry, toast } from '../store/store'
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
  const html = useMemo(() => renderMarkdown(msg.text), [msg.text])
  const thinking = msg.streaming && !msg.text && !msg.error

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
        {thinking && (
          <div className="thinking">
            <span className="shimmer">
              {msg.searches.length ? `Searching “${msg.searches[msg.searches.length - 1]}”` : msg.sources.length ? 'Reading the pages' : 'Searching your documents'}
            </span>
          </div>
        )}
        {!!msg.searches.length && !thinking && (
          <div className="searches">
            {msg.searches.map((q, i) => <span key={i} className="search-chip"><Icon name="search" size={12} />{q}</span>)}
          </div>
        )}
        {msg.text && (
          <div
            className={`md${msg.streaming ? ' cursor' : ''}`}
            onClick={onClick}
            dangerouslySetInnerHTML={{ __html: html }}
          />
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
        {!msg.streaming && (
          <div className={`actions${last ? ' show' : ''}`}>
            {msg.text && <button type="button" className="icon-btn sm" onClick={copy} title="Copy" aria-label="Copy answer"><Icon name="copy" size={15} /></button>}
            <button type="button" className="icon-btn sm" onClick={() => void retry(chatId, msg.id)} title="Retry" aria-label="Retry"><Icon name="retry" size={15} /></button>
          </div>
        )}
      </div>
    </div>
  )
}

export const Message = memo(function Message({ msg, chatId, last }: { msg: Msg; chatId: string; last: boolean }) {
  return msg.role === 'user' ? <UserMessage msg={msg} /> : <BotMessage msg={msg} chatId={chatId} last={last} />
})
