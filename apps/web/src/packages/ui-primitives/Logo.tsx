/** Vesper mark: a "V" followed by a terminal cursor, with the evening star above it.
 *  Same geometry as packaging/make_icon.py. */
export function LogoMark({ size = 30, className, blink = false }: { size?: number; className?: string; blink?: boolean }) {
  return (
    <svg className={`logo-mark${className ? ` ${className}` : ''}`} width={size} height={size} viewBox="8 6 48 48" aria-hidden="true">
      <path d="M15 19 L29 46 L43 19" fill="none" style={{ stroke: 'var(--brand)' }} strokeWidth="7.5" strokeLinecap="round" strokeLinejoin="round" />
      <rect className={blink ? 'logo-cursor blink' : 'logo-cursor'} x="40" y="41" width="11" height="5.5" rx="1.5" style={{ fill: 'var(--brand-2)' }} />
      <circle cx="50" cy="14" r="3.4" style={{ fill: 'var(--brand-2)' }} />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div className="brand">
      <LogoMark size={28} />
      <div className="brand-text">
        <strong>Vesper</strong>
        <span>Coding agent</span>
      </div>
    </div>
  )
}
