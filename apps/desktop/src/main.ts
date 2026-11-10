// Pagewise desktop shell: Electron main process + the Python engine as a
// separate child process + the engine's web UI as the renderer.
//
// createWindow() and the edit context menu are adapted from the desktop app in
// deepseek-ai/deepseek-harness (MIT, Copyright (c) 2026 DeepSeek); see
// THIRD_PARTY_NOTICES.md.

import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  app,
  BrowserWindow,
  dialog,
  Menu,
  nativeTheme,
  session,
  shell,
  type MenuItemConstructorOptions,
} from 'electron'
import { Engine, EngineError, type EngineInfo } from './engine.js'

const SMOKE = process.argv.includes('--smoke')
const SMOKE_TIMEOUT_MS = 90_000
const WINDOWS_TITLEBAR_HEIGHT = 44
const ALLOWED_PERMISSIONS = new Set(['clipboard-sanitized-write'])

let smokeHome: string | undefined
let engineEnv: NodeJS.ProcessEnv = process.env
if (SMOKE) {
  // Throwaway data folders and a fake chat model: the smoke test needs no
  // network, no API key, and leaves nothing behind.
  smokeHome = mkdtempSync(join(tmpdir(), 'pagewise-smoke-'))
  // Chromium writes a few files into userData while it shuts down, after any
  // cleanup could run, so it gets one fixed folder that each smoke run resets.
  const smokeUserData = join(tmpdir(), 'pagewise-smoke-electron')
  rmSync(smokeUserData, { recursive: true, force: true })
  app.setPath('userData', smokeUserData)
  app.commandLine.appendSwitch('disable-http-cache')
  engineEnv = { ...process.env, RAG_LAB_FAKE_LLM: '1', RAG_LAB_HOME: smokeHome, PAGEWISE_HOME: smokeHome }
}

const engine = new Engine(engineEnv)
let mainWindow: BrowserWindow | undefined
let quitting = false

function titleBarSymbolColor(): string {
  return nativeTheme.shouldUseDarkColors ? '#f9fafb' : '#0f1115'
}

function sameOrigin(url: string): boolean {
  try {
    return engine.info !== undefined && new URL(url).origin === engine.info.origin
  } catch {
    return false
  }
}

function openExternally(url: string): void {
  try {
    if (['http:', 'https:'].includes(new URL(url).protocol)) void shell.openExternal(url)
  } catch {
    // not a URL; ignore
  }
}

function createWindow(info: EngineInfo, show = true): BrowserWindow {
  const window = new BrowserWindow({
    title: 'Pagewise',
    width: 1280,
    height: 820,
    minWidth: 860,
    minHeight: 560,
    show: false,
    ...(process.platform === 'win32' ? {
      titleBarStyle: 'hidden' as const,
      titleBarOverlay: { height: WINDOWS_TITLEBAR_HEIGHT, color: '#00000000', symbolColor: titleBarSymbolColor() },
    } : {}),
    // hiddenInset places the traffic lights inside the sidebar; sidebar
    // vibrancy needs a transparent window background to show through the page.
    ...(process.platform === 'darwin' ? {
      titleBarStyle: 'hiddenInset' as const,
      trafficLightPosition: { x: 16, y: 18 },
      vibrancy: 'sidebar' as const,
      visualEffectState: 'active' as const,
      backgroundColor: '#00000000',
    } : {}),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      devTools: !app.isPackaged,
      spellcheck: false,
    },
  })
  if (show) window.once('ready-to-show', () => window.show())

  window.webContents.setWindowOpenHandler(({ url }) => {
    openExternally(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (sameOrigin(url)) return
    event.preventDefault()
    openExternally(url)
  })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  window.webContents.on('context-menu', (_event, { isEditable, selectionText, editFlags }) => {
    const items: MenuItemConstructorOptions[] = []
    if (isEditable) {
      items.push(
        { role: 'undo', enabled: editFlags.canUndo },
        { role: 'redo', enabled: editFlags.canRedo },
        { type: 'separator' },
        { role: 'cut', enabled: editFlags.canCut },
        { role: 'copy', enabled: editFlags.canCopy },
        { role: 'paste', enabled: editFlags.canPaste },
        { type: 'separator' },
        { role: 'selectAll', enabled: editFlags.canSelectAll },
      )
    } else if (selectionText.length > 0) {
      items.push({ role: 'copy', enabled: editFlags.canCopy })
    }
    if (items.length > 0) Menu.buildFromTemplate(items).popup({ window })
  })
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = undefined
  })

  void window.loadURL(info.url)
  return window
}

function lockDownSession(): void {
  const ses = session.defaultSession
  ses.setPermissionRequestHandler((_wc, permission, callback) => callback(ALLOWED_PERMISSIONS.has(permission)))
  ses.setPermissionCheckHandler((_wc, permission) => ALLOWED_PERMISSIONS.has(permission))
}

function installMenu(): void {
  if (process.platform !== 'darwin') {
    // Chromium handles Ctrl+C/V/X/Z/A in text fields by itself on Windows and
    // Linux; the right-click edit menu covers the rest.
    Menu.setApplicationMenu(null)
    return
  }
  const template: MenuItemConstructorOptions[] = [
    { role: 'appMenu' },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        ...(app.isPackaged ? [] : [{ role: 'toggleDevTools' } as MenuItemConstructorOptions]),
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { role: 'windowMenu' },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function showStartupError(err: unknown): void {
  const message = err instanceof Error ? err.message : String(err)
  const log = err instanceof EngineError && err.log !== '' ? `\n\nLast engine output:\n${err.log.split('\n').slice(-12).join('\n')}` : ''
  dialog.showErrorBox('Pagewise could not start',
    `${message}\n\nTry opening Pagewise again. If this keeps happening, reinstall it or report the problem with the details below.${log}`)
}

function cleanupSmoke(): void {
  if (smokeHome !== undefined) rmSync(smokeHome, { recursive: true, force: true })
}

async function finishSmoke(code: number, detail: string): Promise<void> {
  if (code === 0) console.log('PAGEWISE_SMOKE ok')
  else console.error(`PAGEWISE_SMOKE failed: ${detail}`)
  quitting = true
  await engine.stop()
  mainWindow?.destroy()
  cleanupSmoke()
  app.exit(code)
}

async function runSmoke(): Promise<void> {
  const deadline = Date.now() + SMOKE_TIMEOUT_MS
  const timer = setTimeout(() => void finishSmoke(1, 'timed out after 90 s'), SMOKE_TIMEOUT_MS)
  try {
    const info = await engine.start()
    mainWindow = createWindow(info, false)
    const contents = mainWindow.webContents
    await new Promise<void>((resolveLoad, rejectLoad) => {
      contents.once('did-finish-load', () => resolveLoad())
      contents.once('did-fail-load', (_e, code, description) => rejectLoad(new Error(`page load failed: ${description} (${code})`)))
    })
    // The UI may render a moment after the load event; poll until it shows text.
    const check = "document.querySelector('#root, #app') !== null && document.body.innerText.length > 0"
    while (Date.now() < deadline) {
      if (await contents.executeJavaScript(check) === true) {
        clearTimeout(timer)
        await finishSmoke(0, '')
        return
      }
      await new Promise(r => setTimeout(r, 500))
    }
  } catch (err) {
    clearTimeout(timer)
    const log = err instanceof EngineError && err.log !== '' ? `\n${err.log}` : ''
    await finishSmoke(1, `${err instanceof Error ? err.message : String(err)}${log}`)
  }
}

async function launch(): Promise<void> {
  let info: EngineInfo
  try {
    info = await engine.start()
  } catch (err) {
    showStartupError(err)
    quitting = true
    app.exit(1)
    return
  }
  mainWindow = createWindow(info)
}

engine.on('restarted', (info: EngineInfo) => {
  for (const window of BrowserWindow.getAllWindows()) void window.loadURL(info.url)
})
engine.on('failed', (err: EngineError) => {
  if (quitting) return
  if (SMOKE) {
    void finishSmoke(1, err.message)
    return
  }
  showStartupError(err)
  quitting = true
  app.exit(1)
})

if (!SMOKE && !app.requestSingleInstanceLock()) {
  app.exit(0)
} else {
  app.on('second-instance', () => {
    if (mainWindow === undefined) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })

  nativeTheme.on('updated', () => {
    if (process.platform !== 'win32') return
    for (const window of BrowserWindow.getAllWindows()) {
      window.setTitleBarOverlay({ color: '#00000000', symbolColor: titleBarSymbolColor(), height: WINDOWS_TITLEBAR_HEIGHT })
    }
  })

  app.on('web-contents-created', (_event, contents) => {
    contents.on('will-attach-webview', event => event.preventDefault())
  })

  // macOS convention: closing the last window keeps the app (and engine)
  // running until the user quits; elsewhere closing the window quits.
  app.on('window-all-closed', () => {
    if (SMOKE) return
    if (process.platform !== 'darwin') app.quit()
  })

  app.on('activate', () => {
    if (SMOKE || quitting) return
    if (mainWindow === undefined && engine.info !== undefined) mainWindow = createWindow(engine.info)
  })

  app.on('before-quit', (event) => {
    if (quitting) return
    event.preventDefault()
    quitting = true
    void engine.stop().finally(() => app.quit())
  })

  void app.whenReady().then(() => {
    lockDownSession()
    installMenu()
    if (SMOKE) void runSmoke()
    else void launch()
  })
}
