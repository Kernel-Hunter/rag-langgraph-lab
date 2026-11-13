import type { Task, Turn } from '../connection/api'
import type { Msg } from './store'

/** Kept per tool call in saved chats. */
export const MAX_TOOL_OUTPUT = 4000

// Also the engine's chat id, so only letters, digits and dashes.
export const uid = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`

export function blankMsg(role: Msg['role']): Msg {
  return { id: uid(), role, text: '', error: null, tools: [], thoughts: [] }
}

/** The text a model sends to update_tasks ('[x] done', '[>] doing', '[ ] to do'), as a list. */
export function parseTasks(text: unknown): Task[] {
  if (typeof text !== 'string') return []
  return text.split('\n').flatMap((line): Task[] => {
    const m = /^\s*(?:[-*]\s*|\d+[.)]\s*)?\[(.?)\]\s*(.+)$/.exec(line)
    const body = (m ? m[2]! : line.replace(/^[\s\-*]+/, '')).trim()
    if (!body) return []
    const mark = m ? m[1]!.toLowerCase() : ' '
    return [{ text: body.slice(0, 200), status: mark === 'x' ? 'done' : mark === '>' || mark === '~' ? 'active' : 'pending' }]
  }).slice(0, 20)
}

/** Turns from the session database, as the messages this app draws. */
export function turnsToMessages(turns: Turn[]): Msg[] {
  return turns.map((turn): Msg => {
    if (turn.role === 'user') return { ...blankMsg('user'), text: turn.text, attachments: turn.attachments }
    const msg = blankMsg('bot')
    for (const part of turn.parts) {
      const n = msg.tools.length + msg.thoughts.length
      if (part.type === 'text') msg.text += (msg.text ? '\n\n' : '') + part.text
      else if (part.type === 'reasoning') msg.thoughts.push({ at: msg.text.length, n, text: part.text })
      else if (part.name === 'update_tasks' && part.ok !== false) msg.tasks = parseTasks(part.args?.tasks)
      else {
        msg.tools.push({
          id: part.id || uid(), name: part.name, args: part.args ?? {}, at: msg.text.length, n,
          output: (part.output ?? 'Stopped before it finished.').slice(0, MAX_TOOL_OUTPUT), ok: part.ok ?? false,
        })
      }
    }
    return msg
  })
}
