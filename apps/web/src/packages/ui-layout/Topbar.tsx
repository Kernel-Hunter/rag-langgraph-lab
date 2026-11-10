import { currentChat, openSettings, refresh, saveSettings, toggleSidebar, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'

function Status() {
  const connected = useStore(s => s.connected)
  const idx = useStore(s => s.server?.index)
  if (!connected) {
    return <button type="button" className="status err" onClick={() => void refresh()}><span className="pulse" />Engine offline · retry</button>
  }
  if (!idx) return <span className="status"><span className="pulse" />Connecting</span>
  switch (idx.status) {
    case 'ready':
      return (
        <button type="button" className={`status ok${idx.warnings?.length ? ' warn' : ''}`} onClick={() => openSettings('documents')}>
          <span className="led" />{idx.chunks} chunks{idx.warnings?.length ? ` · ${idx.warnings.length} skipped` : ''}
        </button>
      )
    case 'empty':
      return <button type="button" className="status" onClick={() => openSettings('documents')}><span className="led off" />No documents</button>
    case 'error':
      return <button type="button" className="status err" onClick={() => openSettings('documents')}><Icon name="warn" size={13} />Index error</button>
    default:
      return <span className="status"><span className="pulse" />{idx.status === 'embedding' ? 'Embedding' : 'Reading'}</span>
  }
}

export function Topbar() {
  const title = useStore(s => currentChat(s)?.title ?? '')
  const mode = useStore(s => s.server?.settings.mode)
  const sidebar = useStore(s => s.sidebar)
  return (
    <header className="topbar drag">
      <button type="button" className={`icon-btn sidebar-toggle${sidebar ? '' : ' closed'}`} onClick={toggleSidebar} aria-label={sidebar ? 'Hide sidebar' : 'Show sidebar'} aria-expanded={sidebar}>
        <Icon name="sidebar" size={18} />
      </button>
      <div className="top-title" title={title}>{title}</div>
      <div className="top-right">
        <Status />
        {mode && (
          <div className="segmented mini" role="group" aria-label="Answer mode">
            {(['graph', 'agent'] as const).map(m => (
              <button type="button" key={m} className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => void saveSettings({ mode: m })}
                title={m === 'graph' ? 'Retrieve, then answer (LangGraph StateGraph)' : 'The model decides when to search (create_agent)'}>
                {m === 'graph' ? 'Graph' : 'Agent'}
              </button>
            ))}
          </div>
        )}
      </div>
    </header>
  )
}
