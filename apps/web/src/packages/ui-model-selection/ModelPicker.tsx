import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../connection/api'
import { openSettings, saveSettings, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'

const modelCache = new Map<string, string[]>()

export function ModelPicker({ onClose }: { onClose: () => void }) {
  const server = useStore(s => s.server)
  const [active, setActive] = useState(server?.settings.provider ?? '')
  const [query, setQuery] = useState('')
  const [live, setLive] = useState<string[] | null>(modelCache.get(active) ?? null)
  const [loading, setLoading] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)

  const provider = server?.providers.find(p => p.id === active)
  const locked = !!provider && provider.needs_key && !provider.has_key

  useEffect(() => { search.current?.focus() }, [active])
  useEffect(() => { ref.current?.querySelector('.picker-provider.active')?.scrollIntoView({ block: 'nearest' }) }, [])

  // Close on outside click / Escape.
  useEffect(() => {
    const onDown = (e: PointerEvent): void => {
      const el = ref.current
      const t = e.target as Node
      if (el && !el.contains(t) && !(t instanceof Element && t.closest('.chip-btn'))) onClose()
    }
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey, true) }
  }, [onClose])

  // Fetch the provider's live model list once per session.
  useEffect(() => {
    if (!provider || locked) { setLive(null); return }
    const hit = modelCache.get(provider.id)
    if (hit) { setLive(hit); return }
    let alive = true
    setLive(null)
    setLoading(true)
    api.models(provider.id)
      .then(models => { modelCache.set(provider.id, models); if (alive) setLive(models) })
      .catch(() => { if (alive) setLive([]) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [provider?.id, locked]) // eslint-disable-line react-hooks/exhaustive-deps

  const models = useMemo(() => {
    const all = [...new Set([...(provider?.suggestions ?? []), ...(live ?? []).filter(m => !/embed/i.test(m))])]
    const q = query.trim().toLowerCase()
    return q ? all.filter(m => m.toLowerCase().includes(q)) : all
  }, [provider, live, query])

  if (!server) return null
  const current = server.settings

  const choose = async (model: string): Promise<void> => {
    if (!provider) return
    const ok = await saveSettings({ provider: provider.id, model }, `Using ${provider.label} · ${model}`)
    if (ok) onClose()
  }

  const custom = query.trim()
  const showCustom = !!custom && !models.includes(custom) && /^[\w.:/@+-]{1,200}$/.test(custom)

  return (
    <div className="picker" ref={ref} role="dialog" aria-label="Choose a model">
      <div className="picker-providers" role="listbox" aria-label="Providers">
        {server.providers.map(p => (
          <button
            type="button" key={p.id} role="option" aria-selected={p.id === active}
            className={`picker-provider${p.id === active ? ' active' : ''}`}
            onClick={() => { setActive(p.id); setQuery('') }}
          >
            <span className={`dot ${p.local ? 'local' : p.has_key || !p.needs_key ? 'ok' : 'off'}`} />
            <span className="pp-label">{p.label}</span>
            {p.id === current.provider && <Icon name="check" size={13} />}
          </button>
        ))}
      </div>
      <div className="picker-models">
        {locked ? (
          <div className="picker-locked">
            <Icon name="key" size={22} />
            <p>{provider?.label} needs an API key.</p>
            <button type="button" className="btn primary" onClick={() => { onClose(); openSettings('models', provider?.id ?? null) }}>
              Add key
            </button>
          </div>
        ) : (
          <>
            <div className="picker-search">
              <Icon name="search" size={14} />
              <input
                ref={search} value={query} onChange={e => setQuery(e.target.value)}
                placeholder="Search or type a model id" aria-label="Search models"
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    const pick = showCustom ? custom : models[0]
                    if (pick) void choose(pick)
                  }
                }}
              />
              {loading && <span className="spinner" aria-label="Loading models" />}
            </div>
            <div className="picker-list">
              {showCustom && (
                <button type="button" className="picker-model" onClick={() => void choose(custom)}>
                  <Icon name="plus" size={13} /> Use “{custom}”
                </button>
              )}
              {models.map(m => (
                <button
                  type="button" key={m}
                  className={`picker-model${m === current.model && active === current.provider ? ' active' : ''}`}
                  onClick={() => void choose(m)}
                >
                  <span>{m}</span>
                  {m === current.model && active === current.provider && <Icon name="check" size={13} />}
                </button>
              ))}
              {!models.length && !showCustom && !loading && <p className="picker-empty">No models found. Type a model id.</p>}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
