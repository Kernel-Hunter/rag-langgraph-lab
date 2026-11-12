import { useSyncExternalStore } from 'react'
import {
  api, ApiError, type ApprovalAnswer, type ApprovalRequest, type ChatTurn, type ServerState, type Source, type StreamEvent,
} from '../connection/api'
import { applyAppearance, loadAppearance, saveAppearance, type Appearance } from '../ui-theme/appearance'

/** One tool call the folder agent made, drawn inline at text offset `at`. */
export interface ToolStep {
  id: string
  name: string
  args: Record<string, unknown>
  at: number
  output?: string
  ok?: boolean
  approval?: ApprovalRequest & { answer?: ApprovalAnswer; sending?: boolean }
}

export interface Msg {
  id: string
  role: 'user' | 'bot'
  text: string
  sources: Source[]
  searches: string[]
  supported: boolean | null
  error: string | null
  tools: ToolStep[]
  streaming?: boolean
}

export interface Chat {
  id: string
  title: string
  updated: number
  messages: Msg[]
}

export type SettingsTab = 'folder' | 'models' | 'documents' | 'retrieval' | 'appearance'

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

// Also the engine's chat id, so only letters, digits and dashes.
const uid = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`

const MAX_TOOL_OUTPUT = 4000 // kept per tool call in saved chats

function loadTools(raw: unknown): ToolStep[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((t: Partial<ToolStep>): ToolStep[] => {
    if (!t || typeof t.name !== 'string') return []
    return [{
      id: typeof t.id === 'string' ? t.id : uid(),
      name: t.name,
      args: t.args && typeof t.args === 'object' ? t.args : {},
      at: Number(t.at) || 0,
      output: typeof t.output === 'string' ? t.output : undefined,
      ok: typeof t.ok === 'boolean' ? t.ok : undefined,
      // A saved approval can never be answered again: the engine has moved on.
      approval: t.approval ? { ...t.approval, answer: t.approval.answer ?? 'no', sending: false } : undefined,
    }]
  })
}

function loadChats(): Chat[] {
  try {
    const raw = JSON.parse(
      localStorage.getItem(CHATS_KEY) ?? localStorage.getItem('pagewise.chats') ?? localStorage.getItem('raglab.chats') ?? '[]',
    ) as unknown
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
          tools: loadTools(m.tools),
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
      const clean = state.chats.map(c => ({
        ...c,
        messages: c.messages.map(({ streaming: _s, ...m }) => ({
          ...m,
          tools: m.tools.map(t => ({ ...t, output: t.output?.slice(0, MAX_TOOL_OUTPUT) })),
        })),
      }))
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
  api.forgetChat(id).catch(() => undefined) // the engine drops its copy of the conversation
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
  if (s.server.settings.mode === 'folder') {
    if (!s.server.settings.workspace) {
      toast('Open a folder first.', 'error')
      void pickFolder()
      return false
    }
  } else if (s.server.index.status !== 'ready') {
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
  const history: ChatTurn[] = (state.chats.find(c => c.id === chatId)?.messages ?? [])
    .filter(m => m.text && !m.error)
    .map(m => ({ role: m.role, text: m.text }))
  const user: Msg = { id: uid(), role: 'user', text: question, sources: [], searches: [], supported: null, error: null, tools: [] }
  const bot: Msg = { id: uid(), role: 'bot', text: '', sources: [], searches: [], supported: null, error: null, tools: [], streaming: true }
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
    const patchTool = (match: (t: ToolStep) => boolean, patch: (t: ToolStep) => Partial<ToolStep>): void =>
      patchMessage(cid, bot.id, m => {
        // Newest match first: the same tool can run several times in one answer.
        const i = m.tools.map(match).lastIndexOf(true)
        if (i < 0) return {}
        const tools = m.tools.slice()
        tools[i] = { ...tools[i]!, ...patch(tools[i]!) }
        return { tools }
      })
    await api.chat({ question, chat_id: cid, history }, (e: StreamEvent) => {
      switch (e.event) {
        case 'tool_start':
          flush()
          patchMessage(cid, bot.id, m => ({
            tools: [...m.tools, { id: e.data.id ?? uid(), name: e.data.name, args: e.data.args ?? {}, at: m.text.length }],
          }))
          break
        case 'tool_end':
          patchTool(t => (e.data.id ? t.id === e.data.id : t.name === e.data.name && t.output === undefined),
            () => ({ output: e.data.output, ok: e.data.ok }))
          break
        case 'approval':
          patchTool(t => t.name === e.data.tool && t.output === undefined && !t.approval, () => ({ approval: e.data }))
          break
        case 'approval_done':
          patchTool(t => t.approval?.id === e.data.id, t => ({ approval: { ...t.approval!, answer: e.data.answer, sending: false } }))
          if (e.data.timeout) toast('No answer for 10 minutes, so the action was skipped.', 'error')
          break
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
        case 'cwd': // the agent moved to another folder: show it in the top bar
          void refresh()
          break
        case 'notice':
          toast(e.data.text)
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
    patchMessage(cid, bot.id, m => ({
      streaming: false,
      text: m.text || (m.error || m.tools.length ? '' : '_Stopped._'),
      // Anything still waiting was refused by the engine when the stream closed.
      tools: m.tools.map(t => (t.approval && !t.approval.answer ? { ...t, approval: { ...t.approval, answer: 'no' as const } } : t)),
    }))
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
  // The engine keeps its own copy of folder chats; make it start again from what the UI shows.
  await api.forgetChat(chatId).catch(() => undefined)
  set(s => ({
    currentId: chatId,
    chats: s.chats.map(c => (c.id === chatId ? { ...c, messages: c.messages.slice(0, i - 1) } : c)),
  }))
  await send(question.text)
}

// ---------------- folder agent ----------------
/** Answers an approval card. The agent is paused until this arrives. */
export async function respond(chatId: string, msgId: string, approvalId: string, answer: ApprovalAnswer): Promise<void> {
  const setApproval = (patch: Partial<NonNullable<ToolStep['approval']>>): void =>
    patchMessage(chatId, msgId, m => ({
      tools: m.tools.map(t => (t.approval?.id === approvalId ? { ...t, approval: { ...t.approval, ...patch } } : t)),
    }))
  setApproval({ sending: true })
  try {
    await api.approve(approvalId, answer)
    setApproval({ answer, sending: false })
  } catch (e) {
    setApproval({ answer: 'no', sending: false })
    toast(e instanceof Error ? e.message : String(e), 'error')
  }
}

interface DesktopBridge { pickFolder?: () => Promise<string | null> }
const desktop = (): DesktopBridge | undefined => (window as unknown as { vesperDesktop?: DesktopBridge }).vesperDesktop
export const canPickFolder = (): boolean => typeof desktop()?.pickFolder === 'function'

/** Native folder dialog in the desktop app; the Folder settings tab everywhere else. */
export async function pickFolder(): Promise<void> {
  const bridge = desktop()
  if (!bridge?.pickFolder) {
    openSettings('folder')
    return
  }
  const path = await bridge.pickFolder()
  if (path) await openFolder(path)
}

export async function openFolder(path: string): Promise<boolean> {
  const ok = await saveSettings({ workspace: path, mode: 'folder' })
  if (ok) {
    toast(`Working in ${folderName(path)}`)
    if (state.currentId && currentChat(state)?.messages.length) newChat()
  }
  return ok
}

export const folderName = (path: string): string => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path
