import { useEffect, useRef, useState } from 'react'
import { closeSettings, getState, newChat, openSettings, refresh, stop, toast, toggleSidebar, uploadFiles, useStore } from './packages/store/store'
import { Effects } from './packages/ui-effects/Effects'
import { Composer } from './packages/ui-input/Composer'
import { Sidebar } from './packages/ui-layout/Sidebar'
import { Topbar } from './packages/ui-layout/Topbar'
import { Thread } from './packages/ui-conversation/Thread'
import { Settings } from './packages/ui-settings/Settings'
import { Icon } from './packages/ui-primitives/Icon'
import { Toasts } from './packages/ui-primitives/Toasts'

import './packages/ui-layout/layout.css'
import './packages/ui-conversation/conversation.css'
import './packages/ui-input/input.css'
import './packages/ui-model-selection/model.css'
import './packages/ui-settings/settings.css'
import './packages/ui-effects/effects.css'

const ACCEPT = /\.(pdf|md|txt)$/i

export function App() {
  const sidebar = useStore(s => s.sidebar)
  const [dragging, setDragging] = useState(false)
  const depth = useRef(0)

  useEffect(() => {
    void refresh()
    if (window.innerWidth < 820 && getState().sidebar) toggleSidebar()
  }, [])

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && !e.shiftKey && e.key.toLowerCase() === 'n') { e.preventDefault(); newChat(); window.dispatchEvent(new Event('pagewise:focus-composer')) }
      else if (mod && e.key === ',') { e.preventDefault(); if (getState().settings.open) closeSettings(); else openSettings() }
      else if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); toggleSidebar() }
      else if (mod && e.key === 'k') { e.preventDefault(); (document.querySelector('.side-search input') as HTMLInputElement | null)?.focus() }
      else if (e.key === 'Escape' && getState().streamingId && !getState().settings.open) stop()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Drop files anywhere to add them.
  useEffect(() => {
    const hasFiles = (e: DragEvent): boolean => !!e.dataTransfer?.types.includes('Files')
    const enter = (e: DragEvent): void => { if (!hasFiles(e)) return; e.preventDefault(); depth.current++; setDragging(true) }
    const over = (e: DragEvent): void => { if (hasFiles(e)) e.preventDefault() }
    const leave = (e: DragEvent): void => { if (!hasFiles(e)) return; depth.current = Math.max(0, depth.current - 1); if (!depth.current) setDragging(false) }
    const drop = (e: DragEvent): void => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current = 0
      setDragging(false)
      if (getState().settings.open) return // the dialog's own drop zone handles it
      const files = [...(e.dataTransfer?.files ?? [])]
      const ok = files.filter(f => ACCEPT.test(f.name))
      if (ok.length < files.length) {
        toast('Only PDF, Markdown and text files can be added.', 'error')
      }
      if (ok.length) void uploadFiles(ok)
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragover', over)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  }, [])

  return (
    <>
      <div className={`app${sidebar ? '' : ' no-side'}`} id="app">
        <Sidebar />
        <main className="main" onClick={e => { if (window.innerWidth < 820 && sidebar && e.target === e.currentTarget) toggleSidebar() }}>
          <Effects />
          <Topbar />
          <Thread />
          <Composer />
        </main>
      </div>
      <Settings />
      {dragging && (
        <div className="drop-overlay"><div><Icon name="upload" size={34} />Drop to add to your documents</div></div>
      )}
      <Toasts />
    </>
  )
}
