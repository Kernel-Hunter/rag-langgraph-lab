export interface DownloadSample { time: number; bytes: number }

/** Approximate bytes written per second over the last few samples, not a network speed test. */
export function downloadSpeed(samples: DownloadSample[]): number | null {
  if (samples.length < 2) return null
  const first = samples[0]!
  const last = samples[samples.length - 1]!
  const seconds = (last.time - first.time) / 1000
  return seconds > 0 ? Math.max(0, (last.bytes - first.bytes) / seconds) : null
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${Math.max(0, Math.round(bytes))} B`
}
