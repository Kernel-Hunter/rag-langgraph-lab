import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { currentChat, folderName, pickFolder, send, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'
import { LogoMark } from '../ui-primitives/Logo'
import { Message } from './Message'

const SUGGESTIONS = [
  'Explain what this project does and how it is organized',
  'Find a bug in this code and fix it',
  'Write tests for the most important part',
  'Set up a new project here: ask me what it should do',
]

function Empty() {
  const workspace = useStore(s => s.server?.settings.workspace ?? '')
  const terminal = useStore(s => s.appearance.theme === 'terminal')
  return (
    <div className="empty">
      <LogoMark size={64} className="empty-logo" blink />
      {terminal ? (
        <pre className="boot" aria-label="Vesper ready">{`VESPER :: coding agent
> cwd ................ ${workspace || '~'}
> tools .............. read, search, edit, run, web
> type a task and press ENTER`}</pre>
      ) : (
        <>
          <h1>{workspace ? `What are we building in ${folderName(workspace)}?` : 'What are we building today?'}</h1>
          <p className="sub">
            Vesper reads and searches your files, edits code, runs commands and looks things up on the web.
            It asks before it changes anything. Add files, folders or screenshots with <strong>+</strong>, or drop them here.
          </p>
        </>
      )}
      <div className="suggestions">
        {SUGGESTIONS.map(q => (
          <button type="button" key={q} className="suggestion" onClick={() => void send(q)}>
            <Icon name="spark" size={14} />{q}
          </button>
        ))}
      </div>
      {!workspace && (
        <button type="button" className="btn ghost" onClick={() => void pickFolder()}>
          <Icon name="folder" size={15} /> Work in a project folder
        </button>
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
