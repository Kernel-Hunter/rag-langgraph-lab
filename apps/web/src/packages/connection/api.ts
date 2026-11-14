import { SSEParser } from './sse'

export interface Provider {
  id: string
  label: string
  local: boolean
  key_url: string
  needs_key: boolean
  has_key: boolean
  chat_model: string
  suggestions: string[]
}

export interface Settings {
  provider: string
  model: string
  workspace: string
  agent_mode: AgentMode
  thinking: Thinking
}

export type AgentMode = 'ask' | 'auto-edit' | 'yolo' | 'plan'
export type Thinking = 'off' | 'low' | 'medium' | 'high'

export interface ApprovalRequest {
  id: string
  kind: 'edit' | 'run' | 'web'
  tool: string
  path?: string
  diff?: string
  command?: string
  cwd?: string
  url?: string
  host?: string
  /** What "always allow" would save: a command prefix or a web host. Null: this exact command, this chat. */
  rule?: string | null
  /** Risky enough to need a yes every time: no "always allow". */
  strict?: boolean
}

export type ApprovalAnswer = 'yes' | 'always' | 'no'

export interface ServerState {
  providers: Provider[]
  custom_base_url: string
  settings: Settings
  version: string
  /** The user's home folder: where the agent starts when no folder is open. */
  home: string
}

/** An image pasted or dropped into the composer, sent with the next message. */
export interface ImageUpload {
  name: string
  mime: string
  data: string
}

/** A conversation saved in the session database (terminal and desktop write to the same one). */
export interface SavedSession {
  id: string
  title: string
  cwd: string
  provider: string
  model: string
  source: 'terminal' | 'print' | 'desktop'
  created_at: number
  updated_at: number
  messages?: number
}

export type TurnPart =
  | { type: 'reasoning'; text: string }
  | { type: 'text'; text: string }
  | { type: 'tool'; id: string; name: string; args: Record<string, unknown>; output: string | null; ok: boolean | null }

export type Turn = { role: 'user'; text: string; attachments?: string[] } | { role: 'assistant'; parts: TurnPart[] }

/** One line of the agent's task list. */
export interface Task {
  text: string
  status: 'pending' | 'active' | 'done'
}

/** Commands and web sites that run without asking. */
export interface Permissions {
  commands: string[]
  hosts: string[]
  safe: string[]
}

export interface Usage {
  input: number
  output: number
  cached?: number
  context: number
  seconds: number
  tools: number
  estimated?: boolean
}

export type StreamEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'error'; data: { message: string } }
  | { event: 'attached'; data: { names: string[]; problems: string[] } }
  | { event: 'session'; data: { id: string; cwd: string } }
  | { event: 'reasoning'; data: { text: string } }
  | { event: 'tool_draft'; data: { name: string; chars: number } }
  | { event: 'tool_start'; data: { id: string | null; name: string; args: Record<string, unknown> } }
  | { event: 'tool_end'; data: { id: string | null; name: string; output: string; ok: boolean; ms?: number | null } }
  | { event: 'output'; data: { line: string } }
  | { event: 'auto'; data: { kind: 'edit' | 'run' | 'web'; tool: string; path?: string; diff?: string } }
  | { event: 'tasks'; data: { items: Task[] } }
  | { event: 'usage'; data: Usage }
  | { event: 'cwd'; data: { path: string } }
  | { event: 'notice'; data: { text: string } }
  | { event: 'approval'; data: ApprovalRequest }
  | { event: 'approval_done'; data: { id: string; answer: ApprovalAnswer; timeout?: boolean } }
  | { event: 'done'; data: Record<string, never> }

/** Token from the URL hash (#token=...), kept for the session and removed from the address bar. */
const TOKEN = (() => {
  const match = location.hash.match(/token=([A-Za-z0-9_\-]+)/)
  try {
    if (match?.[1]) {
      sessionStorage.setItem('vesper.token', match[1])
      history.replaceState(null, '', location.pathname + location.search)
    }
    return sessionStorage.getItem('vesper.token') ?? match?.[1] ?? ''
  } catch {
    return match?.[1] ?? ''
  }
})()

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

async function request(path: string, init: RequestInit & { json?: unknown } = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Token', TOKEN)
  let body = init.body
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(init.json)
  }
  const res = await fetch(path, { ...init, headers, body, cache: 'no-store' })
  if (!res.ok) {
    let message = res.statusText || `Request failed (${res.status})`
    try {
      const detail = (await res.json() as { detail?: unknown }).detail
      if (typeof detail === 'string') message = detail
      else if (Array.isArray(detail)) message = detail.map(d => (d as { msg?: string }).msg ?? String(d)).join('; ')
    } catch { /* body was not JSON */ }
    throw new ApiError(message, res.status)
  }
  return res
}

export const api = {
  transcribe: async (audio: Blob): Promise<string> =>
    ((await (await request('/api/dictation', { method: 'POST', body: audio, headers: { 'Content-Type': 'application/octet-stream' } })).json()) as { text: string }).text,
  state: async (): Promise<ServerState> => (await request('/api/state')).json(),
  settings: async (values: Record<string, unknown>): Promise<ServerState> =>
    (await request('/api/settings', { method: 'POST', json: values })).json(),
  models: async (provider: string): Promise<string[]> =>
    ((await (await request(`/api/models?provider=${encodeURIComponent(provider)}`)).json()) as { models: string[] }).models,
  approve: async (id: string, answer: ApprovalAnswer): Promise<void> => {
    await request('/api/approve', { method: 'POST', json: { id, answer } })
  },
  forgetChat: async (chatId: string): Promise<void> => {
    await request(`/api/chats/${encodeURIComponent(chatId)}`, { method: 'DELETE' })
  },
  sessions: async (): Promise<SavedSession[]> =>
    ((await (await request('/api/sessions?limit=200')).json()) as { sessions: SavedSession[] }).sessions,
  session: async (id: string): Promise<SavedSession & { turns: Turn[] }> =>
    (await request(`/api/sessions/${encodeURIComponent(id)}`)).json(),
  deleteSession: async (id: string): Promise<void> => {
    await request(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  renameSession: async (id: string, title: string): Promise<void> => {
    await request(`/api/sessions/${encodeURIComponent(id)}/rename`, { method: 'POST', json: { title } })
  },
  /** Stops a running answer in the engine (ends its command, refuses further tools). */
  stopChat: async (chatId: string): Promise<void> => {
    await request(`/api/chats/${encodeURIComponent(chatId)}/stop`, { method: 'POST' })
  },
  /** Puts back the files the agent's latest edits changed. */
  undo: async (chatId: string, sessionId?: string): Promise<string[]> =>
    ((await (await request(`/api/chats/${encodeURIComponent(chatId)}/undo`, { method: 'POST', json: { session_id: sessionId } })).json()) as { paths: string[] }).paths,
  /** Replaces the older turns of a chat with a summary. */
  compact: async (chatId: string, sessionId?: string): Promise<{ compacted: boolean; before: number; context: number }> =>
    (await request(`/api/chats/${encodeURIComponent(chatId)}/compact`, { method: 'POST', json: { session_id: sessionId } })).json(),
  permissions: async (): Promise<Permissions> => (await request('/api/permissions')).json(),
  removePermission: async (kind: 'commands' | 'hosts', rule: string): Promise<Permissions> =>
    (await request('/api/permissions/remove', { method: 'POST', json: { kind, rule } })).json(),

  /** Streams one answer. Calls onEvent for each event; resolves when the stream ends. */
  async chat(
    body: {
      question: string; chat_id: string; session_id?: string
      attachments?: string[]; images?: ImageUpload[]; retry?: boolean
    },
    onEvent: (e: StreamEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const res = await request('/api/chat', { method: 'POST', json: body, signal })
    if (!res.body) throw new ApiError('No response stream', 0)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    const parser = new SSEParser()
    let finished = false
    const emit = (e: unknown): void => {
      const ev = e as StreamEvent
      if (ev.event === 'done') finished = true
      onEvent(ev)
    }
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      for (const e of parser.push(decoder.decode(value, { stream: true }))) emit(e)
    }
    for (const e of parser.push(decoder.decode() + '\n\n')) emit(e)
    if (!finished) throw new ApiError('The answer was cut off because the engine stopped. Try again.', 0)
  },
}
