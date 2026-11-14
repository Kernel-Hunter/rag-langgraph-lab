import { describe, expect, it } from 'vitest'
import { downloadSpeed, formatBytes } from './dictationProgress'

describe('dictation download progress', () => {
  it('estimates download speed from bytes over time', () => {
    expect(downloadSpeed([])).toBeNull()
    expect(downloadSpeed([{ time: 0, bytes: 50 }])).toBeNull()
    expect(downloadSpeed([{ time: 0, bytes: 50 }, { time: 2000, bytes: 2097202 }])).toBe(1048576)
    expect(downloadSpeed([{ time: 0, bytes: 50 }, { time: 2000, bytes: 50 }])).toBe(0)
    expect(downloadSpeed([{ time: 0, bytes: 50 }, { time: 2000, bytes: 10 }])).toBe(0)
  })

  it('formats downloaded data and speed in binary units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024)).toBe('1.0 KiB')
    expect(formatBytes(1048576)).toBe('1.0 MiB')
  })
})
