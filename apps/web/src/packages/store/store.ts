import { useSyncExternalStore } from 'react'
import { api, ApiError, type ServerState, type Source, type StreamEvent } from '../connection/api'
import { applyAppearance, loadAppearance, saveAppearance, type Appearance } from '../ui-theme/appearance'

export interface Msg {
  id: string
  role: 'user' | 'bot'
  text: string
  sources: Source[]
  searches: string[]
  supported: boolean | null
  error: string | null
  streaming?: boolean
}

export interface Chat {
  id: string
  title: string
  updated: number
  messages: Msg[]
}

export type SettingsTab = 'models' | 'documents' | 'retrieval' | 'appearance'

export interface Toast {
  id: number
  text: string
  kind: 'info' | 'error'
}

export interface State {
  server: ServerState | null
  connected: boolean
  chats: Chat[]
  currentId: string | null
  streamingId: string | null
  appearance: Appearance
  settings: { open: boolean; tab: SettingsTab; focusProvider: string | null }
  sidebar: boolean
  toasts: Toast[]
}

const CHATS_KEY = 'vesper.chats'
const MAX_CHATS = 200

const uid = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`

function loadChats(): Chat[] {
  try {
    const raw = JSON.parse(localStorage.getItem(CHATS_KEY) ?? localStorage.getItem('raglab.chats') ?? '[]') as unknown
    if (!Array.isArray(raw)) return []
    return raw.flatMap((c: Partial<Chat>): Chat[] => {
      if (!c || typeof c.id !== 'string' || !Array.isArray(c.messages)) return []
      return [{
        id: c.id,
        title: String(c.title ?? 'New chat').slice(0, 80),
        updated: Number(c.updated) || Date.now(),
        messages: c.messages.map((m: Partial<Msg>) => ({
          id: typeof m.id === 'string' ? m.id : uid(),
          role: m.role === 'user' ? 'user' : 'bot',
          text: String(m.text ?? ''),
          sources: Array.isArray(m.sources) ? m.sources : [],
          searches: Array.isArray(m.searches) ? m.searches : [],
          supported: typeof m.supported === 'boolean' ? m.supported : null,
          error: typeof m.error === 'string' ? m.error : null,
        })),
      }]
    }).slice(0, MAX_CHATS)
  } catch {
    return []
  }
}

let state: State = {
  server: null,
  connected: true,
  chats: loadChats(),
  currentId: null,
  streamingId: null,
  appearance: loadAppearance(),
  settings: { open: false, tab: 'models', focusProvider: null },
  sidebar: true,
  toasts: [],
}
state.currentId = state.chats[0]?.id ?? null

const listeners = new Set<() => void>()
function set(patch: Partial<State> | ((s: State) => Partial<State>)): void {
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
  listeners.forEach(l => l())
}
const subscribe = (l: () => void): (() => void) => { listeners.add(l); return () => listeners.delete(l) }

export function useStore<T>(select: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => select(state))
}
export const getState = (): State => state

let saveTimer: ReturnType<typeof setTimeout> | undefined
function persistChats(): void {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    try {
      const clean = state.chats.map(c => ({ ...c, messages: c.messages.map(({ streaming: _s, ...m }) => m) }))
      localStorage.setItem(CHATS_KEY, JSON.stringify(clean))
    } catch {
      toast('Could not save chat history (storage full).', 'error')
    }
  }, 300)
}

// ---------------- toasts ----------------
let toastId = 0
export function toast(text: string, kind: Toast['kind'] = 'info'): void {
  const id = ++toastId
  set(s => ({ toasts: [...s.toasts.filter(t => t.text !== text), { id, text, kind }].slice(-4) }))
  setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })), kind === 'error' ? 7000 : 3200)
}

// ---------------- server state ----------------
let pollTimer: ReturnType<typeof setTimeout> | undefined
let lastIndexError: string | null = null

export async function refresh(): Promise<void> {
  clearTimeout(pollTimer)
  try {
    const server = await api.state()
    if (server.index.status === 'error' && server.index.error && server.index.error !== lastIndexError) {
      toast(server.index.error, 'error')
    }
    lastIndexError = server.index.error
    set({ server, connected: true })
  } catch (e) {
    set({ connected: false })
    if (e instanceof ApiError && e.status === 401) {
      toast('Session expired. Restart Vesper.', 'error')
      return
    }
  }
  const s = state.server
  const busy = !s || !state.connected || ['idle', 'loading', 'embedding'].includes(s.index.status)
  if (!document.hidden || busy) pollTimer = setTimeout(() => void refresh(), busy ? 800 : 8000)
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh() })

export async function saveSettings(values: Record<string, unknown>, okText?: string): Promise<boolean> {
  try {
    const server = await api.settings(values)
    set({ server })
    if (okText) toast(okText)
    void refresh()
    return true
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'error')
    return false
  }
}

export async function uploadFiles(files: File[]): Promise<void> {
  for (const file of files) {
    try {
      set({ server: await api.upload(file) })
      toast(`Added ${file.name}`)
    } catch (e) {
      toast(`${file.name}: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }
  void refresh()
}

export async function removeDoc(name: string): Promise<void> {
  try {
    set({ server: await api.removeDoc(name) })
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'error')
  }
  void refresh()
}

// ---------------- UI ----------------
export function openSettings(tab: SettingsTab = 'models', focusProvider: string | null = null): void {
  set({ settings: { open: true, tab, focusProvider } })
}
export const closeSettings = (): void => set(s => ({ settings: { ...s.settings, open: false } }))
export const setSettingsTab = (tab: SettingsTab): void => set(s => ({ settings: { ...s.settings, tab, focusProvider: null } }))
export const toggleSidebar = (): void => set(s => ({ sidebar: !s.sidebar }))

export function setAppearance(patch: Partial<Appearance>): void {
  const appearance = { ...state.appearance, ...patch }
  saveAppearance(appearance)
  applyAppearance(appearance)
  set({ appearance })
}
if (typeof matchMedia === 'function') {
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => applyAppearance(state.appearance))
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => applyAppearance(state.appearance))
}

// ---------------- chats ----------------
export const currentChat = (s: State): Chat | null => s.chats.find(c => c.id === s.currentId) ?? null

export function newChat(): void {
  if (state.streamingId) return
  set({ currentId: null })
}
export function selectChat(id: string): void {
  if (state.streamingId) return
  set({ currentId: id })
}
export function deleteChat(id: string): void {
  if (state.streamingId === id) abort?.abort()
  set(s => {
    const chats = s.chats.filter(c => c.id !== id)
    return { chats, currentId: s.currentId === id ? (chats[0]?.id ?? null) : s.currentId }
  })
  persistChats()
}
export function renameChat(id: string, title: string): void {
  const clean = title.trim().slice(0, 80)
  if (!clean) return
  set(s => ({ chats: s.chats.map(c => (c.id === id ? { ...c, title: clean } : c)) }))
  persistChats()
}

function patchMessage(chatId: string, msgId: string, patch: (m: Msg) => Partial<Msg>): void {
  set(s => ({
    chats: s.chats.map(c => (c.id !== chatId ? c : {
      ...c,
      updated: Date.now(),
      messages: c.messages.map(m => (m.id === msgId ? { ...m, ...patch(m) } : m)),
    })),
  }))
}

let abort: AbortController | null = null

export function stop(): void {
  abort?.abort()
}

/** True when a question can be sent now; otherwise explains why with a toast. */
function readyToSend(): boolean {
  const s = state
  if (s.streamingId) return false
  if (!s.server) { toast('Still connecting to the engine…', 'error'); return false }
  if (s.server.index.status !== 'ready') {
    toast(s.server.index.error ?? (s.server.index.status === 'empty' ? 'Add a document first.' : 'Documents are still indexing. One moment.'), 'error')
    if (s.server.index.status === 'empty') openSettings('documents')
    return false
  }
  const p = s.server.providers.find(x => x.id === s.server?.settings.provider)
  if (p && p.needs_key && !p.has_key) {
    openSettings('models', p.id)
    toast(`Add your ${p.label} key first.`, 'error')
    return false
  }
  return true
}

export async function send(text: string): Promise<void> {
  const question = text.trim()
  const s = state
  if (!question || !readyToSend()) return

  let chatId = s.currentId
  if (!chatId || !s.chats.some(c => c.id === chatId)) {
    chatId = uid()
    const chat: Chat = { id: chatId, title: question.slice(0, 60), updated: Date.now(), messages: [] }
    set(st => ({ chats: [chat, ...st.chats].slice(0, MAX_CHATS), currentId: chatId }))
  }
  const user: Msg = { id: uid(), role: 'user', text: question, sources: [], searches: [], supported: null, error: null }
  const bot: Msg = { id: uid(), role: 'bot', text: '', sources: [], searches: [], supported: null, error: null, streaming: true }
  const cid = chatId
  set(st => ({
    streamingId: cid,
    chats: st.chats.map(c => (c.id === cid ? { ...c, updated: Date.now(), messages: [...c.messages, user, bot] } : c))
      .sort((a, b) => b.updated - a.updated),
  }))

  // Tokens arrive fast; batch them into one render per animation frame.
  let pending = ''
  let frame = 0
  const flush = (): void => {
    frame = 0
    if (!pending) return
    const add = pending
    pending = ''
    patchMessage(cid, bot.id, m => ({ text: m.text + add }))
  }

  abort = new AbortController()
  try {
    await api.chat(question, (e: StreamEvent) => {
      switch (e.event) {
        case 'token':
          pending += e.data.text
          if (!frame) frame = requestAnimationFrame(flush)
          break
        case 'sources':
          patchMessage(cid, bot.id, m => ({ sources: [...m.sources, ...e.data.items] }))
          break
        case 'search':
          patchMessage(cid, bot.id, m => ({ searches: [...m.searches, e.data.query] }))
          break
        case 'check':
          patchMessage(cid, bot.id, () => ({ supported: e.data.supported }))
          break
        case 'error':
          patchMessage(cid, bot.id, () => ({ error: e.data.message }))
          break
        default:
          break
      }
    }, abort.signal)
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) {
      patchMessage(cid, bot.id, () => ({ error: e instanceof Error ? e.message : String(e) }))
    }
  } finally {
    if (frame) cancelAnimationFrame(frame)
    flush()
    patchMessage(cid, bot.id, m => ({ streaming: false, text: m.text || (m.error ? '' : '_Stopped._') }))
    abort = null
    set({ streamingId: null })
    persistChats()
  }
}

export async function retry(chatId: string, botId: string): Promise<void> {
  const chat = state.chats.find(c => c.id === chatId)
  if (!chat || state.streamingId) return
  const i = chat.messages.findIndex(m => m.id === botId)
  const question = i > 0 ? chat.messages[i - 1] : undefined
  if (!question || question.role !== 'user') return
  if (!readyToSend()) return // keep the old answer if we cannot ask again
  set(s => ({
    currentId: chatId,
    chats: s.chats.map(c => (c.id === chatId ? { ...c, messages: c.messages.slice(0, i - 1) } : c)),
  }))
  await send(question.text)
}
