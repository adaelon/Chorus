import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// html:false → markdown 里的裸 HTML 被转义；再过 DOMPurify 双保险（LLM 输出不可信）。
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

export function renderMd(text) {
  return DOMPurify.sanitize(md.render(text || ''))
}
