import { describe, expect, it } from 'vitest'
import { linkCitations } from './markdown'

describe('linkCitations', () => {
  it('turns page markers into chips', () => {
    const out = linkCitations('<p>Rule [page 3].</p>')
    expect(out).toContain('data-page="3"')
    expect(out).toContain('>p.3<')
  })

  it('handles ranges and pp.', () => {
    expect(linkCitations('[pages 1-4]')).toContain('p.1–4')
    expect(linkCitations('[pp. 2, 5]')).toContain('p.2–5')
    expect(linkCitations('[p. 7, handbook.pdf]')).toContain('data-page="7"')
  })

  it('leaves code untouched', () => {
    const html = '<pre><code>[page 3]</code></pre><p>[page 4]</p>'
    const out = linkCitations(html)
    expect(out).toContain('<code>[page 3]</code>')
    expect(out).toContain('data-page="4"')
  })

  it('never rewrites inside attributes', () => {
    const html = '<p><a href="https://x/[page 1]">link</a> [page 2]</p>'
    const out = linkCitations(html)
    expect(out).toContain('href="https://x/[page 1]"')
    expect(out).toContain('data-page="2"')
    expect(out.match(/class="cite"/g)).toHaveLength(1)
  })

  it('ignores ordinary brackets', () => {
    expect(linkCitations('<p>[note] and [1]</p>')).toBe('<p>[note] and [1]</p>')
  })
})
