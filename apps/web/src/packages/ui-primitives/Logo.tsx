import { useId } from 'react'

/** Vesper mark: the evening star rising over the horizon, with a smaller star beside it. */
export function LogoMark({ size = 30, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, '')
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" style={{ stroke: 'none' }}>
      <defs>
        <linearGradient id={`vg-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: 'var(--accent)' }} />
          <stop offset="1" style={{ stopColor: 'var(--accent-2)' }} />
        </linearGradient>
      </defs>
      <path d={STAR} fill={`url(#vg-${id})`} />
      <path d={SMALL_STAR} fill={`url(#vg-${id})`} opacity=".85" />
      <path d="M6 55 Q32 44 58 55" fill="none" stroke={`url(#vg-${id})`} strokeWidth="4.5" strokeLinecap="round" />
    </svg>
  )
}

// Four-point stars with curved (concave) sides. Shared with packaging/make_icon.py.
const star = (cx: number, cy: number, r: number, k: number): string =>
  `M${cx} ${cy - r}Q${cx + k} ${cy - k} ${cx + r} ${cy}Q${cx + k} ${cy + k} ${cx} ${cy + r}` +
  `Q${cx - k} ${cy + k} ${cx - r} ${cy}Q${cx - k} ${cy - k} ${cx} ${cy - r}z`
const STAR = star(31, 25, 20, 3.6)
const SMALL_STAR = star(51, 11, 6.5, 1.3)

export function Wordmark() {
  return (
    <div className="brand">
      <LogoMark size={30} />
      <div className="brand-text">
        <strong>Vesper</strong>
        <span>Your agent for every folder</span>
      </div>
    </div>
  )
}
