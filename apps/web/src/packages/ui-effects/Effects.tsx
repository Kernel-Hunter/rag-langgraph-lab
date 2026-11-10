import { useEffect, useRef } from 'react'
import { useStore } from '../store/store'
import { activeEffect, resolvedTheme } from '../ui-theme/appearance'

type Painter = (ctx: CanvasRenderingContext2D, w: number, h: number, t: number) => void
type Factory = (w: number, h: number, colors: Colors) => Painter

interface Colors { a: string; b: string; text: string; bg: string; terminal: boolean }

const FPS = 30
const MAX_DPR = 1.5

function readColors(): Colors {
  const css = getComputedStyle(document.documentElement)
  const v = (n: string): string => css.getPropertyValue(n).trim() || '#888'
  return { a: v('--accent'), b: v('--accent-2'), text: v('--text'), bg: v('--bg'), terminal: document.documentElement.dataset.theme === 'terminal' }
}

// ---- code rain ----
const GLYPHS = 'アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789ABCDEF<>/{}[]=+*#$%&'
const matrix: Factory = (w, h, c) => {
  const size = 15
  const cols = Math.ceil(w / size)
  const drops = Array.from({ length: cols }, () => Math.random() * -h / size)
  const speed = Array.from({ length: cols }, () => 0.35 + Math.random() * 0.55)
  const head = c.terminal ? '#eaffef' : c.text
  let last = 0
  return (ctx, W, H, t) => {
    const dt = last ? Math.min(3, (t - last) / (1000 / FPS)) : 1
    last = t
    // Fade the previous frame instead of clearing it, which leaves the trails.
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = `rgba(0,0,0,${Math.min(1, 0.13 * dt)})`
    ctx.fillRect(0, 0, W, H)
    ctx.globalCompositeOperation = 'source-over'
    ctx.font = `${size - 1}px ui-monospace, Menlo, Consolas, monospace`
    for (let i = 0; i < cols; i++) {
      const y = drops[i]! * size
      const ch = GLYPHS[(Math.random() * GLYPHS.length) | 0]!
      ctx.fillStyle = c.a
      ctx.fillText(ch, i * size, y - size)
      ctx.fillStyle = head
      ctx.fillText(GLYPHS[(Math.random() * GLYPHS.length) | 0]!, i * size, y)
      drops[i]! += speed[i]! * dt
      if (y > H && Math.random() > 0.975) drops[i] = Math.random() * -20
    }
  }
}

// ---- starfield ----
const stars: Factory = (w, h, c) => {
  const n = Math.min(260, Math.round((w * h) / 6500))
  const pts = Array.from({ length: n }, () => ({ x: Math.random() - 0.5, y: Math.random() - 0.5, z: Math.random() }))
  let last = 0
  return (ctx, W, H, t) => {
    const dt = last ? Math.min(3, (t - last) / 33) : 1
    last = t
    ctx.clearRect(0, 0, W, H)
    const cx = W / 2; const cy = H / 2; const s = Math.max(W, H)
    for (const p of pts) {
      p.z -= 0.0022 * dt
      if (p.z <= 0.02) { p.x = Math.random() - 0.5; p.y = Math.random() - 0.5; p.z = 1 }
      const k = 0.5 / p.z
      const x = cx + p.x * s * k; const y = cy + p.y * s * k
      if (x < 0 || x > W || y < 0 || y > H) { p.z = 0; continue }
      const r = Math.max(0.4, (1 - p.z) * 2.1)
      ctx.globalAlpha = Math.min(1, (1 - p.z) * 1.4)
      ctx.fillStyle = p.z < 0.25 ? c.b : c.text
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
    }
    ctx.globalAlpha = 1
  }
}

// ---- horizon grid ----
const grid: Factory = (_w, _h, c) => (ctx, W, H, t) => {
  ctx.clearRect(0, 0, W, H)
  const horizon = H * 0.58
  const g = ctx.createLinearGradient(0, horizon - 120, 0, horizon + 4)
  g.addColorStop(0, 'transparent'); g.addColorStop(1, c.b)
  ctx.globalAlpha = 0.25; ctx.fillStyle = g; ctx.fillRect(0, horizon - 120, W, 124); ctx.globalAlpha = 1
  ctx.strokeStyle = c.a
  ctx.lineWidth = 1
  // Horizontal lines scroll toward the viewer.
  const phase = (t / 1600) % 1
  for (let i = 0; i < 22; i++) {
    const z = (i + 1 - phase) / 22
    const y = horizon + (H - horizon) * Math.pow(1 - z, 2.2)
    ctx.globalAlpha = Math.max(0, 1 - z) * 0.9
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
  }
  // Vertical lines fan out from the vanishing point.
  ctx.globalAlpha = 0.55
  for (let i = -24; i <= 24; i++) {
    ctx.beginPath(); ctx.moveTo(W / 2 + i * 8, horizon); ctx.lineTo(W / 2 + i * W * 0.09, H); ctx.stroke()
  }
  ctx.globalAlpha = 1
}

const FACTORIES: Partial<Record<string, Factory>> = { matrix, stars, grid }

function CanvasEffect({ kind, themeKey }: { kind: string; themeKey: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    const factory = FACTORIES[kind]
    if (!canvas || !factory) return
    const ctx = canvas.getContext('2d', { alpha: true })
    if (!ctx) return
    let raf = 0; let paint: Painter; let lastFrame = 0; let W = 0; let H = 0

    const resize = (): void => {
      const dpr = Math.min(MAX_DPR, window.devicePixelRatio || 1)
      W = Math.max(1, canvas.clientWidth); H = Math.max(1, canvas.clientHeight)
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      paint = factory(W, H, readColors())
    }
    const loop = (t: number): void => {
      raf = requestAnimationFrame(loop)
      if (t - lastFrame < 1000 / FPS - 1) return
      lastFrame = t
      paint(ctx, W, H, t)
    }
    const start = (): void => { if (!raf && !document.hidden) raf = requestAnimationFrame(loop) }
    const halt = (): void => { cancelAnimationFrame(raf); raf = 0 }
    const onVis = (): void => (document.hidden ? halt() : start())
    let resizeTimer: ReturnType<typeof setTimeout> | undefined
    const onResize = (): void => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, 120) }
    const observer = new ResizeObserver(onResize) // window resize and sidebar toggle

    resize(); start()
    document.addEventListener('visibilitychange', onVis)
    observer.observe(canvas)
    return () => {
      halt(); clearTimeout(resizeTimer); observer.disconnect()
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [kind, themeKey])
  return <canvas ref={ref} className={`fx-canvas fx-canvas-${kind}`} aria-hidden="true" />
}

/** Optional animated background. Sits behind everything and never takes pointer events. */
export function Effects() {
  const appearance = useStore(s => s.appearance)
  const effect = activeEffect(appearance)
  if (effect === 'none') return null
  const themeKey = `${resolvedTheme(appearance.theme)}-${appearance.phosphor}`
  return (
    <div className={`fx fx-layer-${effect}`} aria-hidden="true">
      {effect === 'aurora' && <><span className="blob b1" /><span className="blob b2" /><span className="blob b3" /></>}
      {effect === 'scanlines' && <><span className="crt-lines" /><span className="crt-vignette" /><span className="crt-sweep" /></>}
      {FACTORIES[effect] && <CanvasEffect kind={effect} themeKey={themeKey} />}
    </div>
  )
}
