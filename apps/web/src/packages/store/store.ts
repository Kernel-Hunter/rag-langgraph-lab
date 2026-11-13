import { useSyncExternalStore } from 'react'
import {
  api, ApiError, type AgentMode, type ApprovalAnswer, type ApprovalRequest, type ChatTurn, type SavedSession, type ServerState,
  type Source, type StreamEvent, type Task, type Usage,
} from '../connection/api'
import { applyAppearance, loadAppearance, saveAppearance, type Appearance } from '../ui-theme/appearance'
import { blankMsg, MAX_TOOL_OUTPUT, turnsToMessages, uid } from './history'

/** One tool call the folder agent made, drawn inline at text offset `at`. */
export interface ToolStep {
  id: string
  name: string
  args: Record<string, unknown>
  at: number
  /** Order among the tool calls and thoughts of one answer (several can share one text offset). */
  n: number
  output?: string
  ok?: boolean
  ms?: number
  /** The change an edit made, when it ran without an approval card. */
  diff?: string
  /** Output of a command that is still running. Not saved. */
  live?: string
  approval?: ApprovalRequest & { answer?: ApprovalAnswer; sending?: boolean }
}

/** A stretch of the model's thinking, drawn inline at text offset `at`. */
export interface Thought {
  at: number
  n: number
  text: string
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
  thoughts: Thought[]
  usage?: Usage
  /** The agent's task list, as last updated in this answer. */
  tasks?: Task[]
  /** Written in plan mode: the answer is a plan waiting for a yes. */
  plan?: boolean
  /** The files this answer changed were put back. */
  undone?: boolean
  /** The tool call the model is writing right now ("Edit, 340 characters"). Not saved. */
  draft?: { name: string; chars: number }
  streaming?: boolean
}

export interface Chat {
  id: string
  title: string
  updated: number
  messages: Msg[]
  /** Its row in the session database, once the engine has answered in Folder mode. */
  sessionId?: string
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
  /** Saved conversations that are not open as a chat here (started in the terminal, or before a reset). */
  history: SavedSession[]
  /** Transcript view: thinking, commands, edits and output are shown in full. */
  transcript: boolean
}

const CHATS_KEY = 'vesper.chats'
const MAX_CHATS = 200


function loadTools(raw: unknown): ToolStep[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((t: Partial<ToolStep>): ToolStep[] => {
    if (!t || typeof t.name !== 'string') return []
    return [{
      id: typeof t.id === 'string' ? t.id : uid(),
      name: t.name,
      args: t.args && typeof t.args === 'object' ? t.args : {},
      at: Number(t.at) || 0,
      n: Number(t.n) || 0,
      output: typeof t.output === 'string' ? t.output : undefined,
      ok: typeof t.ok === 'boolean' ? t.ok : undefined,
      ms: typeof t.ms === 'number' ? t.ms : undefined,
      diff: typeof t.diff === 'string' ? t.diff : undefined,
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
        sessionId: typeof c.sessionId === 'string' ? c.sessionId : undefined,
        messages: c.messages.map((m: Partial<Msg>) => ({
          id: typeof m.id === 'string' ? m.id : uid(),
          role: m.role === 'user' ? 'user' : 'bot',
          text: String(m.text ?? ''),
          sources: Array.isArray(m.sources) ? m.sources : [],
          searches: Array.isArray(m.searches) ? m.searches : [],
          supported: typeof m.supported === 'boolean' ? m.supported : null,
          error: typeof m.error === 'string' ? m.error : null,
          tools: loadTools(m.tools),
          thoughts: Array.isArray(m.thoughts)
            ? m.thoughts.filter(t => t && typeof t.text === 'string').map(t => ({ at: Number(t.at) || 0, n: Number(t.n) || 0, text: t.text }))
            : [],
          usage: m.usage && typeof m.usage === 'object' ? m.usage : undefined,
          tasks: Array.isArray(m.tasks) ? m.tasks.filter(t => t && typeof t.text === 'string') : undefined,
          plan: m.plan === true || undefined,
          undone: m.undone === true || undefined,
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
  history: [],
  transcript: (() => { try { return localStorage.getItem('vesper.transcript') === '1' } catch { return false } })(),
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
        messages: c.messages.map(({ streaming: _s, draft: _d, ...m }) => ({
          ...m,
          tools: m.tools.map(({ live: _l, ...t }) => ({ ...t, output: t.output?.slice(0, MAX_TOOL_OUTPUT) })),
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
    const first = !state.server
    set({ server, connected: true })
    if (first) void loadHistory()
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
document.addEventListener('visibilitychange', () => { if (!document.hidden) { void refresh(); void loadHistory() } })

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
export function toggleTranscript(): void {
  set(s => ({ transcript: !s.transcript }))
  try { localStorage.setItem('vesper.transcript', state.transcript ? '1' : '0') } catch { /* private mode */ }
  toast(state.transcript ? 'Transcript view: thinking, commands and output in full' : 'Transcript view off')
}

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
  const saved = state.chats.find(c => c.id === id)?.sessionId
  if (saved) api.deleteSession(saved).catch(() => undefined) // and its row in the session database
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
  const user: Msg = { ...blankMsg('user'), text: question }
  const settings = state.server?.settings
  const bot: Msg = { ...blankMsg('bot'), streaming: true, plan: (settings?.mode === 'folder' && settings.agent_mode === 'plan') || undefined }
  const sessionId = state.chats.find(c => c.id === chatId)?.sessionId
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
    let thinking = false // the last thing that arrived was thinking: more of it joins the same block
    await api.chat({ question, chat_id: cid, history, session_id: sessionId }, (e: StreamEvent) => {
      if (e.event !== 'reasoning' && e.event !== 'output') thinking = false
      switch (e.event) {
        case 'session':
          set(st => ({
            chats: st.chats.map(c => (c.id === cid ? { ...c, sessionId: e.data.id } : c)),
            history: st.history.filter(h => h.id !== e.data.id),
          }))
          break
        case 'reasoning': {
          flush()
          const more = thinking
          thinking = true
          patchMessage(cid, bot.id, m => {
            const last = m.thoughts[m.thoughts.length - 1]
            if (more && last) return { thoughts: [...m.thoughts.slice(0, -1), { ...last, text: last.text + e.data.text }] }
            return { thoughts: [...m.thoughts, { at: m.text.length, n: m.tools.length + m.thoughts.length, text: e.data.text }] }
          })
          break
        }
        case 'tool_draft':
          patchMessage(cid, bot.id, () => ({ draft: e.data }))
          break
        case 'tasks':
          patchMessage(cid, bot.id, () => ({ tasks: e.data.items }))
          break
        case 'tool_start':
          flush()
          if (e.data.name === 'update_tasks') { // drawn as the task list, not as a tool row
            patchMessage(cid, bot.id, () => ({ draft: undefined }))
            break
          }
          patchMessage(cid, bot.id, m => ({
            draft: undefined,
            tools: [...m.tools, {
              id: e.data.id ?? uid(), name: e.data.name, args: e.data.args ?? {}, at: m.text.length,
              n: m.tools.length + m.thoughts.length,
            }],
          }))
          break
        case 'tool_end':
          patchTool(t => (e.data.id ? t.id === e.data.id : t.name === e.data.name && t.output === undefined),
            () => ({ output: e.data.output, ok: e.data.ok, ms: e.data.ms ?? undefined, live: undefined }))
          break
        case 'output':
          patchTool(t => t.name === 'run_command' && t.output === undefined,
            t => ({ live: ((t.live ?? '') + e.data.line + '\n').slice(-MAX_TOOL_OUTPUT) }))
          break
        case 'auto':
          if (e.data.diff) patchTool(t => t.name === e.data.tool && t.output === undefined, () => ({ diff: e.data.diff }))
          break
        case 'usage':
          patchMessage(cid, bot.id, () => ({ usage: e.data }))
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
      draft: undefined,
      text: m.text || (m.error || m.tools.length ? '' : '_Stopped._'),
      // Anything still waiting was refused by the engine when the stream closed.
      tools: m.tools.map(t => (t.approval && !t.approval.answer ? { ...t, approval: { ...t.approval, answer: 'no' as const } } : t)),
    }))
    abort = null
    set({ streamingId: null })
    persistChats()
    void loadHistory()
  }
}

// ---------------- saved sessions ----------------
/** Lists the conversations in the session database that are not already open as a chat here. */
export async function loadHistory(): Promise<void> {
  try {
    const open = new Set(state.chats.map(c => c.sessionId).filter(Boolean))
    set({ history: (await api.sessions()).filter(s => !open.has(s.id)) })
  } catch { /* the engine is not up yet; refresh() retries */ }
}

/** Opens a saved conversation as a chat. The next message continues it, in its folder. */
export async function openSession(id: string): Promise<void> {
  if (state.streamingId) return
  const already = state.chats.find(c => c.sessionId === id)
  if (already) { set({ currentId: already.id }); return }
  try {
    const saved = await api.session(id)
    const chat: Chat = {
      id: uid(), title: saved.title || 'Untitled', updated: saved.updated_at * 1000,
      messages: turnsToMessages(saved.turns), sessionId: saved.id,
    }
    set(s => ({
      chats: [chat, ...s.chats].sort((a, b) => b.updated - a.updated).slice(0, MAX_CHATS),
      currentId: chat.id,
      history: s.history.filter(h => h.id !== id),
    }))
    persistChats()
    // Continue where it was: Folder mode, in the conversation's folder (if it still exists).
    const settings = state.server?.settings
    if (settings && (settings.mode !== 'folder' || settings.workspace !== saved.cwd)) {
      await api.settings({ mode: 'folder', workspace: saved.cwd }).then(server => set({ server }), () => undefined)
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'error')
    void loadHistory()
  }
}

// ---------------- plan, undo, context ----------------
let beforePlan: AgentMode = 'ask'

/** Changes what the agent may do alone. Remembers the mode to return to after plan mode. */
export async function setAgentMode(mode: AgentMode): Promise<boolean> {
  const now = state.server?.settings.agent_mode
  if (mode === 'plan' && now && now !== 'plan') beforePlan = now
  return saveSettings({ agent_mode: mode })
}
export const togglePlan = (): Promise<boolean> =>
  setAgentMode(state.server?.settings.agent_mode === 'plan' ? beforePlan : 'plan')

/** The user read the plan and said yes: leave plan mode and let the agent do it. */
export async function runPlan(): Promise<void> {
  if (state.streamingId) return
  if (await setAgentMode(beforePlan)) await send('The plan is approved. Carry it out now.')
}

/** Puts back the files the agent's latest edits changed in the open chat. */
export async function undoChanges(): Promise<void> {
  const chat = currentChat(state)
  if (!chat || state.streamingId) return
  try {
    const paths = await api.undo(chat.id, chat.sessionId)
    if (!paths.length) { toast('Nothing to undo in this chat.'); return }
    const changed = (m: Msg): boolean => m.tools.some(t => (t.name === 'edit_file' || t.name === 'write_file') && t.ok)
    const target = [...chat.messages].reverse().find(m => m.role === 'bot' && !m.undone && changed(m))
    if (target) patchMessage(chat.id, target.id, () => ({ undone: true }))
    persistChats()
    toast(`${paths.length} file${paths.length === 1 ? '' : 's'} put back: ${paths.map(folderName).slice(0, 4).join(', ')}${paths.length > 4 ? ', …' : ''}`)
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'error')
  }
}

/** Replaces the older turns of the open chat with a summary, to free context. */
export async function compactChat(): Promise<void> {
  const chat = currentChat(state)
  if (!chat || state.streamingId) return
  toast('Summarizing the conversation…')
  try {
    const r = await api.compact(chat.id, chat.sessionId)
    toast(r.compacted ? `Older turns are now a summary (about ${r.before} → ${r.context} tokens).` : 'Nothing to summarize yet.')
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'error')
  }
}

/** Slash commands typed in the composer. Returns true when the text was one. */
export function runCommand(text: string): boolean {
  switch (text.trim().toLowerCase()) {
    case '/undo': void undoChanges(); return true
    case '/compact': void compactChat(); return true
    case '/plan': void togglePlan(); return true
    case '/new': case '/clear': newChat(); return true
    case '/transcript': case '/verbose': toggleTranscript(); return true
    case '/permissions': case '/think': openSettings('folder'); return true
    default: return false
  }
}

export async function deleteSession(id: string): Promise<void> {
  set(s => ({ history: s.history.filter(h => h.id !== id) }))
  await api.deleteSession(id).catch(e => toast(e instanceof Error ? e.message : String(e), 'error'))
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
