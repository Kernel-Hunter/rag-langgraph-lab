import { SSEParser } from './sse'

export interface Provider {
  id: string
  label: string
  local: boolean
  key_url: string
  needs_key: boolean
  has_key: boolean
  chat_model: string
  embeddings: boolean
  suggestions: string[]
}

export interface Settings {
  provider: string
  model: string
  embeddings: string
  embed_model: string
  chunk_size: number
  chunk_overlap: number
  k: number
  mode: 'folder' | 'graph' | 'agent'
  check: boolean
  workspace: string
  agent_mode: AgentMode
}

export type AgentMode = 'ask' | 'auto-edit' | 'yolo'

export interface ApprovalRequest {
  id: string
  kind: 'edit' | 'run'
  tool: string
  path?: string
  diff?: string
  command?: string
  cwd?: string
}

export type ApprovalAnswer = 'yes' | 'always' | 'no'

export interface ChatTurn {
  role: 'user' | 'bot'
  text: string
}

export interface IndexState {
  status: 'idle' | 'loading' | 'embedding' | 'ready' | 'empty' | 'error'
  chunks: number
  pages: number
  error: string | null
  warnings?: { source: string; message: string }[]
}

export interface ServerState {
  providers: Provider[]
  custom_base_url: string
  settings: Settings
  index: IndexState
  docs: { name: string; size: number }[]
}

export interface Source {
  page: number | string | null
  source: string | null
  text: string
}

export type StreamEvent =
  | { event: 'sources'; data: { items: Source[] } }
  | { event: 'search'; data: { query: string } }
  | { event: 'token'; data: { text: string } }
  | { event: 'check'; data: { supported: boolean } }
  | { event: 'error'; data: { message: string } }
  | { event: 'tool_start'; data: { id: string | null; name: string; args: Record<string, unknown> } }
  | { event: 'tool_end'; data: { id: string | null; name: string; output: string; ok: boolean } }
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
  state: async (): Promise<ServerState> => (await request('/api/state')).json(),
  settings: async (values: Record<string, unknown>): Promise<ServerState> =>
    (await request('/api/settings', { method: 'POST', json: values })).json(),
  models: async (provider: string): Promise<string[]> =>
    ((await (await request(`/api/models?provider=${encodeURIComponent(provider)}`)).json()) as { models: string[] }).models,
  upload: async (file: File): Promise<ServerState> =>
    (await request(`/api/docs?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: await file.arrayBuffer() })).json(),
  removeDoc: async (name: string): Promise<ServerState> =>
    (await request(`/api/docs/${encodeURIComponent(name)}`, { method: 'DELETE' })).json(),
  reindex: async (): Promise<ServerState> => (await request('/api/reindex', { method: 'POST' })).json(),
  approve: async (id: string, answer: ApprovalAnswer): Promise<void> => {
    await request('/api/approve', { method: 'POST', json: { id, answer } })
  },
  forgetChat: async (chatId: string): Promise<void> => {
    await request(`/api/chats/${encodeURIComponent(chatId)}`, { method: 'DELETE' })
  },

  /** Streams one answer. Calls onEvent for each event; resolves when the stream ends. */
  async chat(
    body: { question: string; chat_id: string; history: ChatTurn[] },
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
