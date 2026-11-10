import { useId } from 'react'

/** Pagewise mark: a page with a folded corner and a spark lifting off the fold. */
export function LogoMark({ size = 30, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, '')
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" style={{ stroke: 'none' }}>
      <defs>
        <linearGradient id={`pg-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: 'var(--accent)' }} />
          <stop offset="1" style={{ stopColor: 'var(--accent-2)' }} />
        </linearGradient>
      </defs>
      <path d="M17 10h19l13 13v28a6 6 0 0 1-6 6H17a6 6 0 0 1-6-6V16a6 6 0 0 1 6-6z" fill={`url(#pg-${id})`} />
      <path d="M36 10v9a4 4 0 0 0 4 4h9z" fill="var(--accent-ink)" opacity=".38" />
      <rect x="19" y="30" width="22" height="4" rx="2" fill="var(--accent-ink)" opacity=".72" />
      <rect x="19" y="38" width="22" height="4" rx="2" fill="var(--accent-ink)" opacity=".72" />
      <rect x="19" y="46" width="13" height="4" rx="2" fill="var(--accent-ink)" opacity=".72" />
      <path d="M54 2.5l1.9 5 5 1.9-5 1.9-1.9 5-1.9-5-5-1.9 5-1.9z" fill={`url(#pg-${id})`} />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div className="brand">
      <LogoMark size={30} />
      <div className="brand-text">
        <strong>Pagewise</strong>
        <span>Answers by the page</span>
      </div>
    </div>
  )
}
