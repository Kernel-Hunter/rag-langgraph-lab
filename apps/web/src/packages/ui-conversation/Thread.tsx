import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { currentChat, openSettings, send, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'
import { LogoMark } from '../ui-primitives/Logo'
import { Message } from './Message'

const SUGGESTIONS = [
  'Summarize the main rules in this document',
  'What are the important deadlines?',
  'What happens if I miss a session?',
  'List the grading criteria with page numbers',
]

function Empty() {
  const server = useStore(s => s.server)
  const theme = useStore(s => s.appearance.theme)
  const idx = server?.index
  const docs = server?.docs.length ?? 0
  return (
    <div className="empty">
      <LogoMark size={56} className="empty-logo" />
      {theme === 'terminal' ? (
        <pre className="boot" aria-label="Vesper ready">{`VESPER v0.3 :: retrieval shell
> docs loaded ........ ${docs}
> chunks indexed ..... ${idx?.chunks ?? 0}
> status ............. ${(idx?.status ?? 'connecting').toUpperCase()}
> type a question and press ENTER`}</pre>
      ) : (
        <>
          <h1>What do you want to know?</h1>
          <p className="sub">
            {idx?.status === 'ready'
              ? `Answers come from your ${docs} document${docs === 1 ? '' : 's'}, with page citations.`
              : idx?.status === 'empty' ? 'Add a PDF, Markdown or text file to get started.'
                : 'Indexing your documents…'}
          </p>
        </>
      )}
      {idx?.status === 'empty' ? (
        <button type="button" className="btn primary" onClick={() => openSettings('documents')}>
          <Icon name="upload" size={15} /> Add documents
        </button>
      ) : (
        <div className="suggestions">
          {SUGGESTIONS.map(q => (
            <button type="button" key={q} className="suggestion" onClick={() => void send(q)} disabled={idx?.status !== 'ready'}>
              <Icon name="spark" size={14} />{q}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Thread() {
  const chat = useStore(currentChat)
  const streaming = useStore(s => s.streamingId)
  const ref = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const [showJump, setShowJump] = useState(false)

  const onScroll = (): void => {
    const el = ref.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    stick.current = near
    setShowJump(!near)
  }

  // New chat: jump to bottom.
  useLayoutEffect(() => {
    stick.current = true
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
    setShowJump(false)
  }, [chat?.id])

  // Follow the stream only while the user stays near the bottom.
  useEffect(() => {
    const el = ref.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  })

  const jump = (): void => {
    const el = ref.current
    if (!el) return
    stick.current = true
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  if (!chat || !chat.messages.length) {
    return <div className="thread"><Empty /></div>
  }
  return (
    <div className="thread" ref={ref} onScroll={onScroll}>
      <div className="col" role="log" aria-live="polite" aria-busy={!!streaming}>
        {chat.messages.map((m, i) => (
          <Message key={m.id} msg={m} chatId={chat.id} last={i === chat.messages.length - 1} />
        ))}
      </div>
      {showJump && (
        <button type="button" className="jump" onClick={jump} aria-label="Scroll to latest">
          <Icon name="chevron" size={16} />
        </button>
      )}
    </div>
  )
}
