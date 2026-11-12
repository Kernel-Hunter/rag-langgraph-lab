import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { currentChat, folderName, openSettings, pickFolder, send, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'
import { LogoMark } from '../ui-primitives/Logo'
import { Message } from './Message'

const SUGGESTIONS = [
  'Summarize the main rules in this document',
  'What are the important deadlines?',
  'What happens if I miss a session?',
  'List the grading criteria with page numbers',
]

const FOLDER_SUGGESTIONS = [
  'Explain what this project does and how it is organized',
  'Find bugs in this code and fix the worst one',
  'Summarize every PDF in this folder',
  'Write a README for this folder',
]

function FolderEmpty({ workspace, terminal }: { workspace: string; terminal: boolean }) {
  return (
    <div className="empty">
      <LogoMark size={56} className="empty-logo" />
      {terminal ? (
        <pre className="boot" aria-label="Vesper ready">{`VESPER v0.4 :: folder agent
> cwd ................ ${workspace || '(none)'}
> tools .............. read, search, edit, run
> type a task and press ENTER`}</pre>
      ) : (
        <>
          <h1>{workspace ? `What should we do in ${folderName(workspace)}?` : 'Open a folder to start'}</h1>
          <p className="sub">
            {workspace
              ? 'Vesper can read and search every file here, edit code and documents, and run commands. It asks before changing anything.'
              : 'Pick a project, a folder of notes or a folder of PDFs. Vesper starts there and works like a teammate at your keyboard.'}
          </p>
        </>
      )}
      {workspace ? (
        <div className="suggestions">
          {FOLDER_SUGGESTIONS.map(q => (
            <button type="button" key={q} className="suggestion" onClick={() => void send(q)}>
              <Icon name="spark" size={14} />{q}
            </button>
          ))}
        </div>
      ) : (
        <button type="button" className="btn primary" onClick={() => void pickFolder()}>
          <Icon name="folder" size={15} /> Open folder
        </button>
      )}
    </div>
  )
}

function Empty() {
  const server = useStore(s => s.server)
  const theme = useStore(s => s.appearance.theme)
  const idx = server?.index
  const docs = server?.docs.length ?? 0
  if (server?.settings.mode === 'folder') return <FolderEmpty workspace={server.settings.workspace} terminal={theme === 'terminal'} />
  return (
    <div className="empty">
      <LogoMark size={56} className="empty-logo" />
      {theme === 'terminal' ? (
        <pre className="boot" aria-label="Vesper ready">{`VESPER v0.4 :: docs shell
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
