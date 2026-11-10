import { currentChat, folderName, openSettings, pickFolder, refresh, saveSettings, toggleSidebar, useStore } from '../store/store'
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
  const workspace = useStore(s => s.server?.settings.workspace ?? '')
  const sidebar = useStore(s => s.sidebar)
  return (
    <header className="topbar drag">
      <button type="button" className={`icon-btn sidebar-toggle${sidebar ? '' : ' closed'}`} onClick={toggleSidebar} aria-label={sidebar ? 'Hide sidebar' : 'Show sidebar'} aria-expanded={sidebar}>
        <Icon name="sidebar" size={18} />
      </button>
      <div className="top-title" title={title}>{title}</div>
      <div className="top-right">
        {mode === 'folder' ? (
          <button type="button" className={`status folder${workspace ? ' ok' : ''}`} onClick={() => void pickFolder()} title={workspace || 'Open a folder'}>
            <Icon name="folder" size={13} />{workspace ? folderName(workspace) : 'Open folder'}
          </button>
        ) : <Status />}
        {mode && (
          <div className="segmented mini" role="group" aria-label="Work on">
            <button type="button" className={mode === 'folder' ? 'on' : ''} aria-pressed={mode === 'folder'}
              onClick={() => void saveSettings({ mode: 'folder' })} title="Read, edit and run things in a folder on your computer">
              Folder
            </button>
            <button type="button" className={mode !== 'folder' ? 'on' : ''} aria-pressed={mode !== 'folder'}
              onClick={() => { if (mode === 'folder') void saveSettings({ mode: 'graph' }) }} title="Answer from the documents you added, with page citations">
              Docs
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
