// The only bridge between the page and Electron: a native "open folder" dialog.
// The page gets no Node.js or Electron access beyond this one function.

import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('vesperDesktop', {
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('vesper:pick-folder'),
})
