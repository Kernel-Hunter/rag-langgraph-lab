import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: false })

let hooked = false
function ensureHooks(): void {
  if (hooked) return
  hooked = true
  DOMPurify.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') {
      const href = node.getAttribute('href') ?? ''
      if (!/^https?:\/\//i.test(href)) {
        node.removeAttribute('href')
      } else {
        node.setAttribute('target', '_blank')
        node.setAttribute('rel', 'noopener noreferrer')
      }
    }
  })
}

const cache = new Map<string, string>()

export function renderMarkdown(text: string): string {
  const hit = cache.get(text)
  if (hit !== undefined) return hit
  ensureHooks()
  const raw = marked.parse(text, { async: false }) as string
  const clean = DOMPurify.sanitize(raw, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'img', 'iframe', 'form', 'input', 'button', 'svg', 'math', 'video', 'audio', 'object', 'embed'],
    FORBID_ATTR: ['style', 'class', 'id'],
  })
  const html = clean
  if (cache.size > 300) cache.clear()
  cache.set(text, html)
  return html
}
