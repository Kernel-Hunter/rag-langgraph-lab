import { useStore } from '../store/store'
import { Icon } from './Icon'

export function Toasts() {
  const toasts = useStore(s => s.toasts)
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <Icon name={t.kind === 'error' ? 'warn' : 'check'} size={15} />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  )
}
