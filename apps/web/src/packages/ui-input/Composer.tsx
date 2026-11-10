import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { currentChat, saveSettings, send, stop, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'
import { ModelPicker } from '../ui-model-selection/ModelPicker'

const MAX_LEN = 8000

export function Composer() {
  const [text, setText] = useState('')
  const [picker, setPicker] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const streaming = useStore(s => s.streamingId !== null)
  const settings = useStore(s => s.server?.settings)
  const providers = useStore(s => s.server?.providers)
  const chatId = useStore(s => currentChat(s)?.id ?? null)
  const theme = useStore(s => s.appearance.theme)

  const provider = providers?.find(p => p.id === settings?.provider)
  const model = settings?.model || provider?.chat_model || ''

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
    window.addEventListener('pagewise:focus-composer', focus)
    return () => window.removeEventListener('pagewise:focus-composer', focus)
  }, [])

  const submit = (): void => {
    const q = text.trim()
    if (!q || streaming) return
    if (q.length > MAX_LEN) return
    setText('')
    void send(q)
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const tooLong = text.length > MAX_LEN
  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={e => { e.preventDefault(); submit() }}>
        {theme === 'terminal' && <span className="composer-prompt" aria-hidden="true">$</span>}
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={theme === 'terminal' ? 'query --docs ./all' : 'Ask about your documents'}
          aria-label="Question"
          spellCheck
        />
        <div className="composer-bar">
          <div className="composer-left">
            <button type="button" className="chip-btn" onClick={() => setPicker(p => !p)} aria-haspopup="dialog" aria-expanded={picker}>
              <Icon name="bolt" size={14} />
              <span className="chip-label">{provider ? `${provider.label} · ${model}` : 'Choose model'}</span>
              <Icon name="chevron" size={13} />
            </button>
            <button
              type="button"
              className={`chip-btn toggle${settings?.check ? ' on' : ''}`}
              onClick={() => void saveSettings({ check: !settings?.check })}
              title="Ask a second pass to check the answer against the sources"
              aria-pressed={!!settings?.check}
              disabled={!settings}
            >
              <Icon name="check" size={14} /> Verify
            </button>
          </div>
          <div className="composer-right">
            {tooLong && <span className="count err">{text.length}/{MAX_LEN}</span>}
            {streaming ? (
              <button type="button" className="send stop" onClick={stop} aria-label="Stop">
                <span className="stop-square" />
              </button>
            ) : (
              <button type="submit" className="send" disabled={!text.trim() || tooLong} aria-label="Send">
                <Icon name="up" size={17} />
              </button>
            )}
          </div>
        </div>
        {picker && <ModelPicker onClose={() => setPicker(false)} />}
      </form>
      <p className="hint">Answers come only from your documents. Check important details on the cited page.</p>
    </div>
  )
}
