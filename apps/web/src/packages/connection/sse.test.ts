import { describe, expect, it } from 'vitest'
import { SSEParser } from './sse'

describe('SSEParser', () => {
  it('parses complete events and keeps partial ones', () => {
    const p = new SSEParser()
    expect(p.push('event: token\ndata: {"text":"Hel')).toEqual([])
    expect(p.push('lo"}\n\nevent: done\ndata: {}\n\n')).toEqual([
      { event: 'token', data: { text: 'Hello' } },
      { event: 'done', data: {} },
    ])
  })

  it('handles CRLF and unicode split across chunks', () => {
    const p = new SSEParser()
    const all = [...p.push('event: token\r\ndata: {"text":"é'), ...p.push('—✓"}\r\n\r\n')]
    expect(all).toEqual([{ event: 'token', data: { text: 'é—✓' } }])
  })

  it('ignores events without data and keeps bad json as text', () => {
    const p = new SSEParser()
    expect(p.push('event: ping\n\nevent: x\ndata: not json\n\n')).toEqual([{ event: 'x', data: 'not json' }])
  })
})
