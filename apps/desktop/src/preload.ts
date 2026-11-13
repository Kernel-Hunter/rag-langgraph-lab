// The only bridge between the page and Electron: native pickers for folders and files, and the
// path of a file dropped onto the window. The page gets no other Node.js or Electron access.

import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('vesperDesktop', {
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('vesper:pick-folder'),
  pickFiles: (): Promise<string[]> => ipcRenderer.invoke('vesper:pick-files'),
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
})
