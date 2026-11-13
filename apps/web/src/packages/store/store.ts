import { useSyncExternalStore } from 'react'
import {
  api, ApiError, type AgentMode, type ApprovalAnswer, type ApprovalRequest, type ImageUpload, type SavedSession,
  type ServerState, type StreamEvent, type Task, type Usage,
} from '../connection/api'
import { applyAppearance, loadAppearance, saveAppearance, type Appearance } from '../ui-theme/appearance'
import { blankMsg, MAX_TOOL_OUTPUT, turnsToMessages, uid } from './history'

/** One tool call the agent made, drawn inline at text offset `at`. */
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
  /** Output of a command that is still running. */
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
  error: string | null
  tools: ToolStep[]
  thoughts: Thought[]
  /** Files, folders and images sent with a question. */
  attachments?: string[]
  usage?: Usage
  /** The agent's task list, as last updated in this answer. */
  tasks?: Task[]
  /** Written in plan mode: the answer is a plan waiting for a yes. */
  plan?: boolean
  /** The files this answer changed were put back. */
  undone?: boolean
  /** The tool call the model is writing right now ("Edit, 340 characters"). */
  draft?: { name: string; chars: number }
  streaming?: boolean
}

/** An open conversation. Saved ones live in the session database the terminal app shares. */
export interface Chat {
  id: string
  title: string
  updated: number
  messages: Msg[]
  /** Its row in the session database, once the engine has saved it. */
  sessionId?: string
}

/** Something to send with the next message: a file or folder by path, or a pasted image. */
export interface Attachment {
  key: string
  name: string
  kind: 'file' | 'folder' | 'image'
  path?: string
  image?: ImageUpload
  /** A small preview for images. */
  preview?: string
}

export type SettingsTab = 'agent' | 'models' | 'appearance'

export interface Toast {
  id: number
  text: string
  kind: 'info' | 'error'
}

export interface State {
  server: ServerState | null
  connected: boolean
  /** Conversations opened in this window, newest first. */
  chats: Chat[]
  currentId: string | null
  streamingId: string | null
  appearance: Appearance
  settings: { open: boolean; tab: SettingsTab; focusProvider: string | null }
  sidebar: boolean
  toasts: Toast[]
  /** Every saved conversation (terminal and desktop), newest first. */
  history: SavedSession[]
  historyLoaded: boolean
  /** Transcript view: thinking, commands, edits and output are shown in full. */
  transcript: boolean
  /** Files, folders and images waiting to go with the next message. */
  attachments: Attachment[]
}

let state: State = {
  server: null,
  connected: true,
  chats: [],
  currentId: null,
  streamingId: null,
  appearance: loadAppearance(),
  settings: { open: false, tab: 'models', focusProvider: null },
  sidebar: true,
  toasts: [],
  history: [],
  historyLoaded: false,
  transcript: (() => { try { return localStorage.getItem('vesper.transcript') === '1' } catch { return false } })(),
  attachments: [],
}
// Chats used to be kept in this window's storage; the session database holds them now.
try { for (const k of ['vesper.chats', 'pagewise.chats', 'raglab.chats']) localStorage.removeItem(k) } catch { /* private mode */ }

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
const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

// ---------------- toasts ----------------
let toastId = 0
export function toast(text: string, kind: Toast['kind'] = 'info'): void {
  const id = ++toastId
  set(s => ({ toasts: [...s.toasts.filter(t => t.text !== text), { id, text, kind }].slice(-4) }))
  setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })), kind === 'error' ? 7000 : 3200)
}

// ---------------- server state ----------------
let pollTimer: ReturnType<typeof setTimeout> | undefined

/** Reads settings, keys and models from the engine. It re-reads the files the terminal app writes,
 *  so a key or model chosen there shows up here within a few seconds. */
export async function refresh(): Promise<void> {
  clearTimeout(pollTimer)
  try {
    const server = await api.state()
    set({ server, connected: true })
    if (!document.hidden) void loadHistory() // conversations the terminal app added, renamed or deleted
  } catch (e) {
    set({ connected: false })
    if (e instanceof ApiError && e.status === 401) {
      toast('Session expired. Restart Vesper.', 'error')
      return
    }
  }
  const busy = !state.server || !state.connected
  if (!document.hidden || busy) pollTimer = setTimeout(() => void refresh(), busy ? 1000 : 5000)
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) { void refresh(); void loadHistory() } })
window.addEventListener('focus', () => { void refresh(); void loadHistory() })

export async function saveSettings(values: Record<string, unknown>, okText?: string): Promise<boolean> {
  try {
    const server = await api.settings(values)
    set({ server })
    if (okText) toast(okText)
    return true
  } catch (e) {
    toast(errorText(e), 'error')
    return false
  }
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

export function selectChat(id: string): void {
  if (!state.streamingId) set({ currentId: id })
}

export function newChat(): void {
  if (state.streamingId) return
  set({ currentId: null })
}

/** Opens a conversation from the session database (or the copy already open). */
export async function openSession(id: string): Promise<void> {
  if (state.streamingId) return
  const open = state.chats.find(c => c.sessionId === id)
  if (open) { set({ currentId: open.id }); return }
  try {
    const saved = await api.session(id)
    const chat: Chat = {
      id: saved.id, title: saved.title || 'Untitled', updated: saved.updated_at * 1000,
      messages: turnsToMessages(saved.turns), sessionId: saved.id,
    }
    set(s => ({ chats: [chat, ...s.chats.filter(c => c.id !== chat.id)], currentId: chat.id }))
    // Continue where it was: in the conversation's folder, if it still exists.
    if (state.server && state.server.settings.workspace !== saved.cwd) {
      await api.settings({ workspace: saved.cwd }).then(server => set({ server }), () => undefined)
    }
  } catch (e) {
    toast(errorText(e), 'error')
    void loadHistory()
  }
}

/** Deletes a conversation everywhere: this window, the engine and the session database. */
export async function deleteSession(id: string): Promise<void> {
  const chat = state.chats.find(c => c.sessionId === id || c.id === id)
  if (chat && state.streamingId === chat.id) stop()
  try {
    if (chat?.sessionId || !chat) await api.deleteSession(chat?.sessionId ?? id)
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) { // already gone is fine
      toast(`Could not delete: ${errorText(e)}`, 'error')
      return
    }
  }
  if (chat) await api.forgetChat(chat.id).catch(() => undefined)
  set(s => {
    const chats = s.chats.filter(c => c !== chat)
    return {
      chats,
      history: s.history.filter(h => h.id !== id && h.id !== chat?.sessionId),
      currentId: s.currentId === chat?.id ? null : s.currentId,
    }
  })
  toast('Conversation deleted')
}

export async function renameSession(id: string, title: string): Promise<void> {
  const clean = title.trim().replace(/\s+/g, ' ').slice(0, 80)
  if (!clean) return
  const chat = state.chats.find(c => c.sessionId === id || c.id === id)
  const sessionId = chat?.sessionId ?? (chat ? undefined : id)
  set(s => ({
    chats: s.chats.map(c => (c === chat ? { ...c, title: clean } : c)),
    history: s.history.map(h => (h.id === sessionId ? { ...h, title: clean } : h)),
  }))
  if (!sessionId) return // not saved yet: the title goes with the first answer
  try {
    await api.renameSession(sessionId, clean)
  } catch (e) {
    toast(`Could not rename: ${errorText(e)}`, 'error')
    void loadHistory()
  }
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
  const id = state.streamingId
  if (id) api.stopChat(id).catch(() => undefined) // the engine ends the running command too
  abort?.abort()
}

/** True when a message can be sent now; otherwise explains why with a toast. */
function readyToSend(): boolean {
  const s = state
  if (s.streamingId) return false
  if (!s.server) { toast('Still connecting to the engine…', 'error'); return false }
  const p = s.server.providers.find(x => x.id === s.server?.settings.provider)
  if (p && p.needs_key && !p.has_key) {
    openSettings('models', p.id)
    toast(`Add your ${p.label} key first.`, 'error')
    return false
  }
  return true
}

export async function send(text: string, opts: { retry?: boolean } = {}): Promise<void> {
  const question = text.trim()
  const attached = opts.retry ? [] : state.attachments
  if (!question && !attached.length) return
  if (!readyToSend()) return

  let chatId = state.currentId
  if (!chatId || !state.chats.some(c => c.id === chatId)) {
    chatId = uid()
    const chat: Chat = { id: chatId, title: (question || attached[0]!.name).slice(0, 60), updated: Date.now(), messages: [] }
    set(st => ({ chats: [chat, ...st.chats], currentId: chatId }))
  }
  const user: Msg = { ...blankMsg('user'), text: question, attachments: attached.length ? attached.map(a => a.name) : undefined }
  const settings = state.server?.settings
  const bot: Msg = { ...blankMsg('bot'), streaming: true, plan: settings?.agent_mode === 'plan' || undefined }
  const sessionId = state.chats.find(c => c.id === chatId)?.sessionId
  const cid = chatId
  set(st => ({
    streamingId: cid,
    attachments: opts.retry ? st.attachments : [],
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
    await api.chat({
      question: question || 'Look at what I attached.',
      chat_id: cid,
      session_id: sessionId,
      retry: opts.retry || undefined,
      attachments: attached.flatMap(a => (a.path ? [a.path] : [])),
      images: attached.flatMap(a => (a.image ? [a.image] : [])),
    }, (e: StreamEvent) => {
      if (e.event !== 'reasoning' && e.event !== 'output') thinking = false
      switch (e.event) {
        case 'session':
          set(st => ({ chats: st.chats.map(c => (c.id === cid ? { ...c, sessionId: e.data.id } : c)) }))
          break
        case 'attached':
          if (e.data.problems.length) toast(`Not attached: ${e.data.problems.join('; ')}`, 'error')
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
      patchMessage(cid, bot.id, () => ({ error: errorText(e) }))
    }
  } finally {
    if (frame) cancelAnimationFrame(frame)
    flush()
    patchMessage(cid, bot.id, m => ({
      streaming: false,
      draft: undefined,
      text: m.text || (m.error || m.tools.length ? '' : '_Stopped._'),
      tools: m.tools.map(t => ({
        ...t,
        live: undefined,
        output: t.output ?? (t.live ? `${t.live}\n(stopped)` : t.output),
        // Anything still waiting was refused by the engine when the stream closed.
        approval: t.approval && !t.approval.answer ? { ...t.approval, answer: 'no' as const } : t.approval,
      })),
    }))
    abort = null
    set({ streamingId: null })
    void loadHistory()
  }
}

/** Asks the last question again. The engine forgets the old answer first. */
export async function retry(chatId: string, botId: string): Promise<void> {
  const chat = state.chats.find(c => c.id === chatId)
  if (!chat || state.streamingId) return
  const i = chat.messages.findIndex(m => m.id === botId)
  const question = i > 0 ? chat.messages[i - 1] : undefined
  if (!question || question.role !== 'user' || i !== chat.messages.length - 1) return
  if (!readyToSend()) return // keep the old answer if we cannot ask again
  set(s => ({
    currentId: chatId,
    chats: s.chats.map(c => (c.id === chatId ? { ...c, messages: c.messages.slice(0, i - 1) } : c)),
  }))
  await send(question.text, { retry: true })
}

// ---------------- saved sessions ----------------
/** Every conversation in the session database: the sidebar's list. */
const HISTORY_LIMIT = 200 // matches api.sessions
export async function loadHistory(): Promise<void> {
  try {
    const history = await api.sessions()
    set(s => {
      // A chat open here whose conversation was deleted elsewhere (the terminal) goes too. Only when the
      // list is complete: a full page may leave out an old conversation that still exists.
      const ids = new Set(history.map(h => h.id))
      const complete = history.length < HISTORY_LIMIT
      const chats = s.chats.filter(c => !complete || !c.sessionId || ids.has(c.sessionId) || c.id === s.streamingId)
      return {
        history,
        historyLoaded: true,
        chats,
        currentId: chats.some(c => c.id === s.currentId) ? s.currentId : null,
      }
    })
  } catch { /* the engine is not up yet; refresh() retries */ }
}

// ---------------- attachments ----------------
interface DesktopBridge {
  pickFolder?: () => Promise<string | null>
  pickFiles?: () => Promise<string[]>
  pathForFile?: (file: File) => string
}
const desktop = (): DesktopBridge | undefined => (window as unknown as { vesperDesktop?: DesktopBridge }).vesperDesktop
export const isDesktop = (): boolean => typeof desktop()?.pickFolder === 'function'

const IMAGE_MIME = /^image\/(png|jpeg|gif|webp)$/
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const baseName = (p: string): string => p.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || p

function addAttachment(a: Omit<Attachment, 'key'>): void {
  set(s => {
    if (s.attachments.some(x => (a.path && x.path === a.path))) return {}
    if (s.attachments.length >= 20) { toast('At most 20 attachments per message.', 'error'); return {} }
    return { attachments: [...s.attachments, { ...a, key: uid() }] }
  })
}
export const removeAttachment = (key: string): void => set(s => ({ attachments: s.attachments.filter(a => a.key !== key) }))

export function attachPath(path: string, kind: Attachment['kind'] = 'file'): void {
  addAttachment({ name: baseName(path), kind, path })
}

/** Native file picker (several files at once). */
export async function pickAttachments(): Promise<void> {
  const paths = await desktop()?.pickFiles?.()
  for (const p of paths ?? []) attachPath(p, /\.(png|jpe?g|gif|webp)$/i.test(p) ? 'image' : 'file')
}

/** Native folder picker; the folder goes with the message as a listing the agent can explore. */
export async function pickAttachFolder(): Promise<void> {
  const path = await desktop()?.pickFolder?.()
  if (path) attachPath(path, 'folder')
}

const readAsDataUrl = (file: Blob): Promise<string> => new Promise((resolve, reject) => {
  const r = new FileReader()
  r.onload = () => resolve(String(r.result))
  r.onerror = () => reject(r.error)
  r.readAsDataURL(file)
})

/** Files dropped or pasted into the window. On the desktop they go by path; pasted images by content. */
export async function attachFiles(files: File[]): Promise<void> {
  for (const file of files) {
    const path = (() => { try { return desktop()?.pathForFile?.(file) || '' } catch { return '' } })()
    if (path) {
      attachPath(path, IMAGE_MIME.test(file.type) ? 'image' : file.type === '' && !file.size ? 'folder' : 'file')
      continue
    }
    if (IMAGE_MIME.test(file.type)) {
      if (file.size > MAX_IMAGE_BYTES) { toast(`${file.name || 'Image'}: images must be under 5 MB.`, 'error'); continue }
      const url = await readAsDataUrl(file)
      const name = file.name && file.name !== 'image.png' ? file.name : `pasted-${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.png`
      addAttachment({ name, kind: 'image', preview: url, image: { name, mime: file.type, data: url.slice(url.indexOf(',') + 1) } })
      continue
    }
    toast(`${file.name}: drop files in the desktop app, or use @path in the message.`, 'error')
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
    toast(`${paths.length} file${paths.length === 1 ? '' : 's'} put back: ${paths.map(folderName).slice(0, 4).join(', ')}${paths.length > 4 ? ', …' : ''}`)
  } catch (e) {
    toast(errorText(e), 'error')
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
    toast(errorText(e), 'error')
  }
}

export const COMMANDS: { name: string; hint: string }[] = [
  { name: '/new', hint: 'start a new conversation' },
  { name: '/plan', hint: 'plan mode on or off' },
  { name: '/undo', hint: 'put back the files of the last change' },
  { name: '/compact', hint: 'summarize older turns to free context' },
  { name: '/transcript', hint: 'show thinking and output in full' },
  { name: '/rename', hint: 'rename this conversation: /rename New title' },
  { name: '/delete', hint: 'delete this conversation' },
  { name: '/model', hint: 'pick a model' },
  { name: '/permissions', hint: 'what runs without asking' },
]

/** Slash commands typed in the composer. Returns true when the text was one. */
export function runCommand(text: string): boolean {
  const [word = '', ...rest] = text.trim().split(/\s+/)
  const chat = currentChat(state)
  switch (word.toLowerCase()) {
    case '/undo': void undoChanges(); return true
    case '/compact': void compactChat(); return true
    case '/plan': void togglePlan(); return true
    case '/new': case '/clear': newChat(); return true
    case '/transcript': case '/verbose': toggleTranscript(); return true
    case '/permissions': case '/think': openSettings('agent'); return true
    case '/model': case '/key': openSettings('models'); return true
    case '/rename':
      if (chat && rest.length) void renameSession(chat.sessionId ?? chat.id, rest.join(' '))
      else toast('Use /rename New title in an open conversation.', 'error')
      return true
    case '/delete':
      if (chat) void deleteSession(chat.sessionId ?? chat.id)
      return true
    default: return false
  }
}

// ---------------- approvals ----------------
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
    toast(errorText(e), 'error')
  }
}

// ---------------- the working folder ----------------
export const canPickFolder = isDesktop

/** Native folder dialog in the desktop app; the Agent settings tab everywhere else. */
export async function pickFolder(): Promise<void> {
  const bridge = desktop()
  if (!bridge?.pickFolder) {
    openSettings('agent')
    return
  }
  const path = await bridge.pickFolder()
  if (path) await openFolder(path)
}

export async function openFolder(path: string): Promise<boolean> {
  const ok = await saveSettings({ workspace: path })
  if (ok) {
    toast(`Working in ${folderName(path)}`)
    if (state.currentId && currentChat(state)?.messages.length) newChat()
  }
  return ok
}

export const folderName = (path: string): string => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path
