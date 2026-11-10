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

// [page 3], [p. 3], [pages 3-4], [pp. 3, 5], [page 3, handbook.pdf]
const CITE = /\[(?:pages?|pp?\.)\s*(\d+)(?:\s*(?:-|–|,|and)\s*(\d+))?[^\]<]{0,24}\]/gi

/**
 * Replaces citation markers in already-sanitized HTML with chip buttons.
 * Only touches text between tags (never attribute values) and skips code.
 */
export function linkCitations(html: string): string {
  let inCode = 0
  return html.split(/(<[^>]*>)/).map(part => {
    if (part.startsWith('<')) {
      if (/^<(pre|code)[\s>]/i.test(part)) inCode++
      else if (/^<\/(pre|code)>/i.test(part)) inCode = Math.max(0, inCode - 1)
      return part
    }
    if (inCode) return part
    return part.replace(CITE, (_m, a: string, b?: string) => {
      const label = b ? `p.${a}–${b}` : `p.${a}`
      return `<button type="button" class="cite" data-page="${a}">${label}</button>`
    })
  }).join('')
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
  const html = linkCitations(clean)
  if (cache.size > 300) cache.clear()
  cache.set(text, html)
  return html
}
