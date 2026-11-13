import { currentChat, folderName, pickFolder, refresh, toggleSidebar, toggleTranscript, useStore } from '../store/store'
import { Icon } from '../ui-primitives/Icon'

export function Topbar() {
  const title = useStore(s => currentChat(s)?.title ?? '')
  const workspace = useStore(s => s.server?.settings.workspace ?? '')
  const home = useStore(s => s.server?.home ?? '')
  const connected = useStore(s => s.connected)
  const sidebar = useStore(s => s.sidebar)
  const transcript = useStore(s => s.transcript)
  const mac = /Mac/.test(navigator.userAgent)
  const where = workspace || home
  return (
    <header className="topbar drag">
      <button type="button" className={`icon-btn sidebar-toggle${sidebar ? '' : ' closed'}`} onClick={toggleSidebar} aria-label={sidebar ? 'Hide sidebar' : 'Show sidebar'} aria-expanded={sidebar}>
        <Icon name="sidebar" size={18} />
      </button>
      <div className="top-title" title={title}>{title}</div>
      <div className="top-right">
        {!connected && (
          <button type="button" className="status err" onClick={() => void refresh()}><span className="pulse" />Engine offline · retry</button>
        )}
        <button type="button" className={`status view${transcript ? ' ok' : ''}`} onClick={toggleTranscript} aria-pressed={transcript}
          title={`Transcript view: show thinking, commands, edits and output in full (${mac ? '⌘' : 'Ctrl+'}O)`}>
          <Icon name={transcript ? 'eye' : 'eyeOff'} size={13} />Transcript
        </button>
        <button type="button" className={`status folder${workspace ? ' ok' : ''}`} onClick={() => void pickFolder()}
          title={`${where || 'Choose a working folder'}\nClick to work in another folder`}>
          <Icon name={workspace ? 'folder' : 'home'} size={13} />{workspace ? folderName(workspace) : 'Home folder'}
        </button>
      </div>
    </header>
  )
}
