import { describe, expect, it } from 'vitest'
import { turnsToMessages } from './history'

describe('turnsToMessages', () => {
  it('rebuilds a saved conversation in order: thinking, tool calls, text', () => {
    const msgs = turnsToMessages([
      { role: 'user', text: 'fix it' },
      {
        role: 'assistant',
        parts: [
          { type: 'reasoning', text: 'look first' },
          { type: 'tool', id: 'c1', name: 'read_file', args: { path: 'a.py' }, output: '1\tx', ok: true },
          { type: 'text', text: 'Found it.' },
          { type: 'tool', id: 'c2', name: 'run_command', args: { command: 'pytest' }, output: null, ok: null },
          { type: 'text', text: 'Done.' },
        ],
      },
    ])
    expect(msgs.map(m => m.role)).toEqual(['user', 'bot'])
    const bot = msgs[1]!
    expect(bot.text).toBe('Found it.\n\nDone.')
    expect(bot.thoughts).toEqual([{ at: 0, n: 0, text: 'look first' }])
    expect(bot.tools.map(t => [t.name, t.at, t.n, t.ok])).toEqual([['read_file', 0, 1, true], ['run_command', 9, 2, false]])
    expect(bot.tools[1]!.output).toMatch(/Stopped/)
  })
})
