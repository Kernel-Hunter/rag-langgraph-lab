import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import type { Provider } from '../connection/api'
import {
  closeSettings, getState, removeDoc, saveSettings, setAppearance, setSettingsTab, uploadFiles, useStore, type SettingsTab,
} from '../store/store'
import { EFFECTS, type EffectChoice, type Phosphor, type ThemeChoice } from '../ui-theme/appearance'
import { Icon, type IconName } from '../ui-primitives/Icon'

const TABS: { id: SettingsTab; label: string; icon: IconName }[] = [
  { id: 'models', label: 'Models', icon: 'key' },
  { id: 'documents', label: 'Documents', icon: 'doc' },
  { id: 'retrieval', label: 'Retrieval', icon: 'sliders' },
  { id: 'appearance', label: 'Appearance', icon: 'palette' },
]

function Section({ title, desc, children }: { title: string; desc?: string; children: ReactNode }) {
  return (
    <section className="set-section">
      <h3>{title}</h3>
      {desc && <p className="set-desc">{desc}</p>}
      {children}
    </section>
  )
}

// ---------------- Models ----------------
function KeyRow({ p, focus }: { p: Provider; focus: boolean }) {
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (focus) { ref.current?.scrollIntoView({ block: 'center' }); ref.current?.focus() }
  }, [focus])

  const save = async (): Promise<void> => {
    const key = value.trim()
    if (!key) return
    if (await saveSettings({ keys: { [p.id]: key } }, `${p.label} key saved`)) setValue('')
  }
  return (
    <div className={`key-row${focus ? ' focus' : ''}`}>
      <div className="key-head">
        <span className={`dot ${p.has_key ? 'ok' : 'off'}`} />
        <strong>{p.label}</strong>
        {p.has_key && <span className="badge ok">Saved</span>}
        {p.key_url && (
          <a className="key-link" href={p.key_url} target="_blank" rel="noopener noreferrer">
            Get a key <Icon name="external" size={12} />
          </a>
        )}
      </div>
      <form className="key-input" onSubmit={e => { e.preventDefault(); void save() }}>
        <input
          ref={ref} className="input" type={show ? 'text' : 'password'} value={value}
          onChange={e => setValue(e.target.value)} autoComplete="off" spellCheck={false}
          placeholder={p.has_key ? '•••••••••••• (saved, paste to replace)' : 'Paste API key'}
          aria-label={`${p.label} API key`}
        />
        <button type="button" className="icon-btn sm" onClick={() => setShow(s => !s)} aria-label={show ? 'Hide key' : 'Show key'}>
          <Icon name={show ? 'eyeOff' : 'eye'} size={14} />
        </button>
        <button type="submit" className="btn primary" disabled={!value.trim()}>Save</button>
        {p.has_key && (
          <button type="button" className="btn ghost danger" onClick={() => void saveSettings({ keys: { [p.id]: '' } }, `${p.label} key removed`)}>
            Remove
          </button>
        )}
      </form>
    </div>
  )
}

function ModelsTab() {
  const server = useStore(s => s.server)
  const focus = useStore(s => s.settings.focusProvider)
  const [url, setUrl] = useState(server?.custom_base_url ?? '')
  if (!server) return null
  const cloud = server.providers.filter(p => p.needs_key)
  const local = server.providers.filter(p => p.local)
  return (
    <>
      <Section title="Answer mode" desc="Graph runs retrieve then generate. Agent lets the model decide when and what to search.">
        <div className="segmented">
          {(['graph', 'agent'] as const).map(m => (
            <button type="button" key={m} className={server.settings.mode === m ? 'on' : ''} onClick={() => void saveSettings({ mode: m })}>
              {m === 'graph' ? 'Graph (fast)' : 'Agent (tool use)'}
            </button>
          ))}
        </div>
      </Section>
      <Section title="API keys" desc="Keys stay on this computer, in a private file only your user can read. They are never sent anywhere except to that provider.">
        <div className="key-list">
          {cloud.map(p => <KeyRow key={p.id} p={p} focus={focus === p.id} />)}
        </div>
      </Section>
      <Section title="Local and custom" desc={`Run models on your own machine with ${local.map(p => p.label).join(', ')}, or point Pagewise at any OpenAI-compatible server.`}>
        <form className="key-input" onSubmit={e => { e.preventDefault(); void saveSettings({ custom_base_url: url.trim() }, 'Custom server saved') }}>
          <input className="input" value={url} onChange={e => setUrl(e.target.value)} placeholder="http://localhost:8000/v1" aria-label="Custom base URL" spellCheck={false} />
          <button type="submit" className="btn primary" disabled={url.trim() === server.custom_base_url}>Save</button>
        </form>
      </Section>
    </>
  )
}

// ---------------- Documents ----------------
function DocumentsTab() {
  const server = useStore(s => s.server)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  if (!server) return null
  const idx = server.index
  const onDrop = (e: DragEvent): void => {
    e.preventDefault()
    e.stopPropagation()
    setOver(false)
    void uploadFiles([...e.dataTransfer.files])
  }
  const fmt = (n: number): string => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`)
  return (
    <>
      <div
        className={`dropzone${over ? ' over' : ''}`}
        onDragOver={e => { e.preventDefault(); e.stopPropagation(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        role="button" tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.current?.click() } }}
      >
        <Icon name="upload" size={24} />
        <strong>Drop files here or click to browse</strong>
        <span>PDF, Markdown or text · up to 50 MB each</span>
        <input
          ref={input} type="file" multiple accept=".pdf,.md,.txt" hidden
          onChange={e => { void uploadFiles([...(e.target.files ?? [])]); e.target.value = '' }}
        />
      </div>
      <div className={`index-status ${idx.status}`}>
        {['loading', 'embedding', 'idle'].includes(idx.status) && <span className="spinner" />}
        {idx.status === 'ready' && <Icon name="check" size={14} />}
        {idx.status === 'error' && <Icon name="warn" size={14} />}
        <span>
          {idx.status === 'ready' ? `${idx.chunks} chunks from ${idx.pages} pages, ready`
            : idx.status === 'embedding' ? 'Embedding chunks…'
              : idx.status === 'loading' || idx.status === 'idle' ? 'Reading documents…'
                : idx.status === 'empty' ? 'No documents yet'
                  : idx.error}
        </span>
      </div>
      {!!idx.warnings?.length && (
        <div className="warnings">
          {idx.warnings.map((w, i) => (
            <div key={i} className="warning"><Icon name="warn" size={13} /><span><b>{w.source}</b>: {w.message}</span></div>
          ))}
        </div>
      )}
      <ul className="doc-list">
        {server.docs.map(d => (
          <li key={d.name}>
            <Icon name="doc" size={16} />
            <span className="doc-name" title={d.name}>{d.name}</span>
            <span className="doc-size">{fmt(d.size)}</span>
            <button type="button" className="icon-btn sm" aria-label={`Remove ${d.name}`} onClick={() => void removeDoc(d.name)}>
              <Icon name="trash" size={14} />
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}

// ---------------- Retrieval ----------------
function Slider({ label, value, min, max, step, onCommit, hint }: {
  label: string; value: number; min: number; max: number; step: number; onCommit: (v: number) => void; hint: string
}) {
  const [v, setV] = useState(value)
  useEffect(() => setV(value), [value])
  const commit = (): void => { if (v !== value) onCommit(v) }
  return (
    <label className="slider">
      <div className="slider-head"><span>{label}</span><output>{v}</output></div>
      <input
        type="range" min={min} max={max} step={step} value={v}
        onChange={e => setV(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit}
      />
      <small>{hint}</small>
    </label>
  )
}

function RetrievalTab() {
  const server = useStore(s => s.server)
  if (!server) return null
  const s = server.settings
  const embedOptions = [
    { id: 'local', label: 'On-device (free, private)' },
    ...server.providers.filter(p => p.embeddings).map(p => ({ id: p.id, label: `${p.label}${p.needs_key && !p.has_key ? ' (needs key)' : ''}` })),
  ]
  return (
    <>
      <Section title="Embeddings" desc="Turns text into vectors for search. Changing it re-indexes your documents.">
        <select className="input" value={s.embeddings} onChange={e => void saveSettings({ embeddings: e.target.value, embed_model: '' }, 'Re-indexing with new embeddings')}>
          {embedOptions.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
          {!embedOptions.some(o => o.id === s.embeddings) && <option value={s.embeddings}>{s.embeddings}</option>}
        </select>
      </Section>
      <Section title="Chunking and search">
        <div className="sliders">
          <Slider label="Chunk size" value={s.chunk_size} min={200} max={3000} step={50} hint="Characters per chunk. Smaller is more precise, larger keeps more context."
            onCommit={v => void saveSettings({ chunk_size: v, chunk_overlap: Math.round(v / 5) }, 'Re-indexing')} />
          <Slider label="Overlap" value={s.chunk_overlap} min={0} max={Math.min(1000, s.chunk_size - 1)} step={10} hint="Characters shared between neighboring chunks."
            onCommit={v => void saveSettings({ chunk_overlap: v }, 'Re-indexing')} />
          <Slider label="Chunks per answer (k)" value={s.k} min={1} max={12} step={1} hint="How many chunks the model reads for each question."
            onCommit={v => void saveSettings({ k: v })} />
        </div>
      </Section>
    </>
  )
}

// ---------------- Appearance ----------------
const THEMES: { id: ThemeChoice; label: string }[] = [
  { id: 'system', label: 'System' }, { id: 'dark', label: 'Dark' }, { id: 'light', label: 'Light' }, { id: 'terminal', label: 'Terminal' },
]
const PHOSPHORS: { id: Phosphor; label: string; color: string }[] = [
  { id: 'green', label: 'Green', color: '#39ff7a' }, { id: 'amber', label: 'Amber', color: '#ffb000' },
  { id: 'cyan', label: 'Cyan', color: '#3df2ff' }, { id: 'white', label: 'White', color: '#e8f0ff' }, { id: 'red', label: 'Red', color: '#ff3b5c' },
]

function ThemePreview({ id }: { id: ThemeChoice }) {
  return (
    <div className={`theme-preview tp-${id}`} aria-hidden="true">
      <div className="tp-side" />
      <div className="tp-main">
        {id === 'terminal' ? <><i>$ query</i><i className="tp-dim">&gt; ok [p.3]</i></> : <><b /><b className="tp-short" /></>}
      </div>
    </div>
  )
}

function AppearanceTab() {
  const a = useStore(s => s.appearance)
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
  return (
    <>
      <Section title="Theme">
        <div className="theme-grid">
          {THEMES.map(t => (
            <button type="button" key={t.id} className={`theme-card${a.theme === t.id ? ' on' : ''}`} onClick={() => setAppearance({ theme: t.id })} aria-pressed={a.theme === t.id}>
              <ThemePreview id={t.id} />
              <span>{t.id === 'terminal' && <Icon name="terminal" size={13} />}{t.label}</span>
            </button>
          ))}
        </div>
      </Section>
      {a.theme === 'terminal' && (
        <Section title="Phosphor" desc="Color of the terminal glow.">
          <div className="swatches">
            {PHOSPHORS.map(p => (
              <button type="button" key={p.id} className={`swatch${a.phosphor === p.id ? ' on' : ''}`} onClick={() => setAppearance({ phosphor: p.id })} aria-pressed={a.phosphor === p.id} title={p.label}>
                <span style={{ background: p.color, boxShadow: `0 0 12px ${p.color}` }} />{p.label}
              </button>
            ))}
          </div>
        </Section>
      )}
      <Section title="Background effect" desc={reduced ? 'Your system asks for reduced motion, so effects stay off.' : 'Runs behind the chat at low frame rate and pauses when the window is hidden.'}>
        <div className="effect-grid">
          {EFFECTS.map(e => (
            <button type="button" key={e.id} className={`effect-card${a.effect === e.id ? ' on' : ''}`} onClick={() => setAppearance({ effect: e.id as EffectChoice })} title={e.hint} aria-pressed={a.effect === e.id}>
              <span className={`fx-thumb fx-${e.id}`} aria-hidden="true" />
              {e.label}
            </button>
          ))}
        </div>
        {a.effect !== 'none' && (
          <label className="slider">
            <div className="slider-head"><span>Intensity</span><output>{a.intensity}%</output></div>
            <input type="range" min={10} max={100} step={5} value={a.intensity} onChange={e => setAppearance({ intensity: Number(e.target.value) })} />
          </label>
        )}
      </Section>
    </>
  )
}

// ---------------- Dialog ----------------
export function Settings() {
  const { open, tab } = useStore(s => s.settings)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    if (!getState().settings.focusProvider) ref.current?.querySelector<HTMLElement>('.set-tab.on')?.focus()
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') { e.preventDefault(); closeSettings() }
      if (e.key === 'Tab' && ref.current) { // keep focus inside the dialog
        const items = [...ref.current.querySelectorAll<HTMLElement>('button, input, select, a[href], [tabindex="0"]')].filter(el => !el.hasAttribute('disabled') && el.offsetParent !== null)
        const first = items[0]; const last = items[items.length - 1]
        if (!first || !last) return
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [open])

  if (!open) return null
  return (
    <div className="scrim" onPointerDown={e => { if (e.target === e.currentTarget) closeSettings() }}>
      <div className="settings" role="dialog" aria-modal="true" aria-label="Settings" ref={ref}>
        <nav className="set-nav">
          <h2>Settings</h2>
          {TABS.map(t => (
            <button type="button" key={t.id} className={`set-tab${tab === t.id ? ' on' : ''}`} onClick={() => setSettingsTab(t.id)} aria-current={tab === t.id ? 'page' : undefined}>
              <Icon name={t.icon} size={16} />{t.label}
            </button>
          ))}
        </nav>
        <div className="set-body">
          <header className="set-head">
            <h2>{TABS.find(t => t.id === tab)?.label}</h2>
            <button type="button" className="icon-btn" onClick={closeSettings} aria-label="Close settings"><Icon name="close" size={16} /></button>
          </header>
          <div className="set-scroll">
            {tab === 'models' && <ModelsTab />}
            {tab === 'documents' && <DocumentsTab />}
            {tab === 'retrieval' && <RetrievalTab />}
            {tab === 'appearance' && <AppearanceTab />}
          </div>
        </div>
      </div>
    </div>
  )
}
