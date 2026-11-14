import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react'
import {
  attachFiles, COMMANDS, currentChat, isDesktop, openSettings, pickAttachFolder, pickAttachments, pickFolder, removeAttachment,
  runCommand, send, stop, togglePlan, useStore,
} from '../store/store'
import { Icon, type IconName } from '../ui-primitives/Icon'
import { ModelPicker } from '../ui-model-selection/ModelPicker'
import { useDictation } from './useDictation'

const MAX_LEN = 100_000
const MODE_LABEL = { ask: 'Ask first', 'auto-edit': 'Auto-edit', yolo: 'Full access', plan: 'Read only' } as const

function AddMenu({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onDown = (e: PointerEvent): void => {
      const t = e.target as Element
      if (ref.current && !ref.current.contains(t) && !t.closest?.('.add-btn')) onClose()
    }
    const onKey = (e: globalThis.KeyboardEvent): void => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey, true) }
  }, [onClose])
  const desktop = isDesktop()
  const item = (icon: IconName, label: string, hint: string, run: () => void, disabled = false) => (
    <button type="button" role="menuitem" className="menu-item" disabled={disabled} onClick={() => { onClose(); run() }}>
      <Icon name={icon} size={16} />
      <span><strong>{label}</strong><small>{hint}</small></span>
    </button>
  )
  return (
    <div className="add-menu" ref={ref} role="menu" aria-label="Add to the message">
      {item('paperclip', 'Files', 'Code, text, PDFs or images', () => void pickAttachments(), !desktop)}
      {item('folder', 'A folder', 'Its file list; Vesper can open what is inside', () => void pickAttachFolder(), !desktop)}
      {item('home', 'Work in a folder', 'Make a project the working folder', () => void pickFolder())}
      {!desktop && <p className="menu-note">In the browser, paste images or type @path in the message.</p>}
    </div>
  )
}

export function Composer() {
  const [text, setText] = useState('')
  const [picker, setPicker] = useState(false)
  const [adding, setAdding] = useState(false)
  const [cmdIndex, setCmdIndex] = useState(0)
  const ref = useRef<HTMLTextAreaElement>(null)
  const streaming = useStore(s => s.streamingId !== null)
  const settings = useStore(s => s.server?.settings)
  const providers = useStore(s => s.server?.providers)
  const attachments = useStore(s => s.attachments)
  const chatId = useStore(s => currentChat(s)?.id ?? null)
  const theme = useStore(s => s.appearance.theme)
  const dictation = useDictation(chatId, spoken => {
    setText(previous => previous + (previous && !/\s$/.test(previous) ? ' ' : '') + spoken)
    ref.current?.focus()
  })

  const provider = providers?.find(p => p.id === settings?.provider)
  const model = settings?.model || provider?.chat_model || ''
  const mode = settings?.agent_mode ?? 'ask'

  // Slash commands: suggestions while the message is a single /word.
  const slash = /^\/\S*$/.test(text) ? COMMANDS.filter(c => c.name.startsWith(text.toLowerCase())) : []

  // Autosize up to 40% of the viewport.
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    const max = Math.round(window.innerHeight * 0.4)
    el.style.height = `${Math.min(el.scrollHeight, max)}px`
    el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
  }, [text])

  useEffect(() => { ref.current?.focus() }, [chatId])
  useEffect(() => {
    const focus = (): void => ref.current?.focus()
    window.addEventListener('vesper:focus-composer', focus)
    return () => window.removeEventListener('vesper:focus-composer', focus)
  }, [])
  useEffect(() => setCmdIndex(0), [text])

  const submit = (): void => {
    const q = text.trim()
    if ((!q && !attachments.length) || streaming || q.length > MAX_LEN || dictation.phase !== 'idle') return
    if (q.startsWith('/') && runCommand(q)) { setText(''); return }
    setText('')
    void send(q)
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (slash.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault()
      setCmdIndex(i => (i + (e.key === 'ArrowDown' ? 1 : slash.length - 1)) % slash.length)
      return
    }
    if (slash.length && e.key === 'Tab') {
      e.preventDefault()
      setText(slash[cmdIndex]!.name + ' ')
      return
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (slash.length && text !== slash[cmdIndex]!.name) { setText(slash[cmdIndex]!.name + ' '); return }
      submit()
    }
    if (e.key === 'Backspace' && !text && attachments.length) removeAttachment(attachments[attachments.length - 1]!.key)
  }

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>): void => {
    const files = [...e.clipboardData.files]
    if (files.length) {
      e.preventDefault()
      void attachFiles(files)
    }
  }

  const tooLong = text.length > MAX_LEN
  return (
    <div className="composer-wrap">
      {!!slash.length && (
        <div className="slash-menu" role="listbox" aria-label="Commands">
          {slash.map((c, i) => (
            <button type="button" key={c.name} role="option" aria-selected={i === cmdIndex}
              className={`slash-item${i === cmdIndex ? ' on' : ''}`}
              onMouseEnter={() => setCmdIndex(i)} onClick={() => { setText(c.name + ' '); ref.current?.focus() }}>
              <code>{c.name}</code><span>{c.hint}</span>
            </button>
          ))}
        </div>
      )}
      <form className="composer" onSubmit={e => { e.preventDefault(); submit() }}>
        {!!attachments.length && (
          <div className="att-row">
            {attachments.map(a => (
              <span key={a.key} className={`att-chip ${a.kind}`} title={a.path ?? a.name}>
                {a.preview ? <img src={a.preview} alt="" /> : <Icon name={a.kind === 'image' ? 'image' : a.kind === 'folder' ? 'folder' : 'doc'} size={14} />}
                <span>{a.name}</span>
                <button type="button" className="att-x" onClick={() => removeAttachment(a.key)} aria-label={`Remove ${a.name}`}>
                  <Icon name="close" size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        {theme === 'terminal' && <span className="composer-prompt" aria-hidden="true">$</span>}
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          placeholder={theme === 'terminal' ? 'task --here' : 'Ask Vesper to build, fix, explain or run something · / for commands'}
          aria-label="Message"
          spellCheck
        />
        <div className="composer-bar">
          <div className="composer-left">
            <button type="button" className={`icon-btn add-btn${adding ? ' on' : ''}`} onClick={() => setAdding(a => !a)}
              aria-haspopup="menu" aria-expanded={adding} aria-label="Add files, a folder or images" title="Add files, a folder or images">
              <Icon name="plus" size={17} />
            </button>
            <button type="button" className="chip-btn" onClick={() => setPicker(p => !p)} aria-haspopup="dialog" aria-expanded={picker}>
              <Icon name="bolt" size={14} />
              <span className="chip-label">{provider ? `${provider.label} · ${model}` : 'Choose model'}</span>
              <Icon name="chevron" size={13} />
            </button>
            <button type="button" className={`chip-btn${mode === 'yolo' ? ' warn' : ''}`} onClick={() => openSettings('agent')} title="What Vesper may do without asking">
              <Icon name={mode === 'yolo' ? 'warn' : 'shield'} size={14} />
              <span className="chip-label">{MODE_LABEL[mode]}</span>
            </button>
            <button type="button" className={`chip-btn toggle${mode === 'plan' ? ' on' : ''}`} onClick={() => void togglePlan()}
              aria-pressed={mode === 'plan'} disabled={streaming}
              title="Plan mode: Vesper reads and proposes a plan, and changes nothing until you approve it">
              <Icon name="list" size={14} /> Plan
            </button>
          </div>
          <div className="composer-right">
            {dictation.phase !== 'idle' && <span className="dictation-status" role="status">
              {dictation.phase === 'recording' ? 'Recording · click mic to finish' : dictation.phase === 'requesting' ? 'Opening microphone…' : 'Transcribing locally…'}
            </span>}
            <button type="button" className={`mic-btn${dictation.phase === 'recording' ? ' recording' : ''}`}
              onClick={() => void dictation.toggle()} disabled={streaming || (dictation.phase !== 'idle' && dictation.phase !== 'recording')}
              aria-label={dictation.phase === 'recording' ? 'Finish dictation' : 'Dictate a message'}
              aria-pressed={dictation.phase === 'recording'} title={dictation.phase === 'recording' ? 'Finish dictation' : 'Dictate a message locally'}>
              <Icon name="mic" size={17} />
            </button>
            {tooLong && <span className="count err">{text.length}/{MAX_LEN}</span>}
            {streaming ? (
              <button type="button" className="send stop" onClick={stop} aria-label="Stop" title="Stop (Esc)">
                <span className="stop-square" />
              </button>
            ) : (
              <button type="submit" className="send" disabled={(!text.trim() && !attachments.length) || tooLong || dictation.phase !== 'idle'} aria-label="Send">
                <Icon name="up" size={17} />
              </button>
            )}
          </div>
        </div>
        {picker && <ModelPicker onClose={() => setPicker(false)} />}
        {adding && <AddMenu onClose={() => setAdding(false)} />}
      </form>
      <p className="hint">Vesper can make mistakes. Read each change before you allow it.</p>
    </div>
  )
}
