export type ThemeChoice = 'system' | 'dark' | 'light' | 'terminal'
export type Phosphor = 'green' | 'amber' | 'cyan' | 'white' | 'red'
export type EffectChoice = 'none' | 'aurora' | 'matrix' | 'scanlines' | 'grid' | 'stars'

export interface Appearance {
  theme: ThemeChoice
  phosphor: Phosphor
  effect: EffectChoice
  intensity: number // 0..100
}

const KEY = 'vesper.appearance'
const THEMES: ThemeChoice[] = ['system', 'dark', 'light', 'terminal']
const PHOSPHORS: Phosphor[] = ['green', 'amber', 'cyan', 'white', 'red']
export const EFFECTS: { id: EffectChoice; label: string; hint: string }[] = [
  { id: 'none', label: 'None', hint: 'Plain background' },
  { id: 'aurora', label: 'Aurora', hint: 'Slow, soft color glow' },
  { id: 'matrix', label: 'Code rain', hint: 'Falling glyphs' },
  { id: 'scanlines', label: 'CRT', hint: 'Scanlines, vignette, flicker' },
  { id: 'grid', label: 'Grid', hint: 'Moving horizon grid' },
  { id: 'stars', label: 'Starfield', hint: 'Drifting stars' },
]

const reducedMotion = (): boolean =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

export const DEFAULT_APPEARANCE: Appearance = { theme: 'system', phosphor: 'green', effect: 'none', intensity: 55 }

export function loadAppearance(): Appearance {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Appearance>
    return {
      theme: THEMES.includes(raw.theme as ThemeChoice) ? (raw.theme as ThemeChoice) : DEFAULT_APPEARANCE.theme,
      phosphor: PHOSPHORS.includes(raw.phosphor as Phosphor) ? (raw.phosphor as Phosphor) : 'green',
      effect: EFFECTS.some(e => e.id === raw.effect) ? (raw.effect as EffectChoice) : 'none',
      intensity: typeof raw.intensity === 'number' ? Math.min(100, Math.max(0, raw.intensity)) : 55,
    }
  } catch {
    return { ...DEFAULT_APPEARANCE }
  }
}

export function saveAppearance(a: Appearance): void {
  try { localStorage.setItem(KEY, JSON.stringify(a)) } catch { /* private mode: keep in memory */ }
}

export function resolvedTheme(theme: ThemeChoice): 'dark' | 'light' | 'terminal' {
  if (theme !== 'system') return theme
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

/** Effects stay off when the OS asks for reduced motion, whatever the saved choice. */
export function activeEffect(a: Appearance): EffectChoice {
  return reducedMotion() ? 'none' : a.effect
}

export function applyAppearance(a: Appearance): void {
  const html = document.documentElement
  html.dataset.theme = resolvedTheme(a.theme)
  html.dataset.phosphor = a.phosphor
  html.dataset.effect = activeEffect(a)
  html.style.setProperty('--fx', String(a.intensity / 100))
  const ua = navigator.userAgent
  html.classList.toggle('is-desktop', /Electron\//.test(ua))
  html.classList.toggle('is-mac', /Macintosh|Mac OS X/.test(ua))
  html.classList.toggle('is-win', /Windows/.test(ua))
}
