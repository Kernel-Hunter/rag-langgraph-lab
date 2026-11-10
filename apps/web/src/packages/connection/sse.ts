export interface SSEEvent {
  event: string
  data: unknown
}

/**
 * Incremental server-sent-events parser. Feed it decoded text chunks; it returns
 * the complete events and keeps any partial event for the next call.
 */
export class SSEParser {
  private buffer = ''

  push(chunk: string): SSEEvent[] {
    this.buffer += chunk.replace(/\r\n/g, '\n')
    const events: SSEEvent[] = []
    let cut: number
    while ((cut = this.buffer.indexOf('\n\n')) >= 0) {
      const raw = this.buffer.slice(0, cut)
      this.buffer = this.buffer.slice(cut + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
      }
      if (!data.length) continue
      try {
        events.push({ event, data: JSON.parse(data.join('\n')) })
      } catch {
        events.push({ event, data: data.join('\n') })
      }
    }
    return events
  }
}
