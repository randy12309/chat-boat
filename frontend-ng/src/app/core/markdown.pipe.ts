import { Pipe, PipeTransform } from '@angular/core';

/* Minimal Markdown renderer for assistant messages. All input is HTML-escaped first, so only the tags produced here
   can reach the page; binding the result through [innerHTML] also runs Angular's own sanitizer over it. */
function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inline(escaped: string): string {
  const codes: string[] = [];
  let s = escaped.replace(/`([^`\n]+)`/g, (_, c: string) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\*\*([^*\n]+?)\*\*|__([^_\n]+?)__/g, (_, a?: string, b?: string) => `<strong>${a ?? b}</strong>`);
  s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\*)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?![_\w])/g, '$1<em>$2</em>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i: string) => `<code>${codes[+i]}</code>`);
}

export function renderMarkdown(src: string | null | undefined): string {
  const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out: string[] = [];
  let para: string[] = [];
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) {
      out.push('<p>' + para.map((l) => inline(esc(l))).join('<br>') + '</p>');
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(`<${list.type}>` + list.items.map((t) => `<li>${inline(esc(t))}</li>`).join('') + `</${list.type}>`);
      list = null;
    }
  };
  const flush = () => {
    flushPara();
    flushList();
  };
  const addItem = (type: 'ul' | 'ol', text: string) => {
    flushPara();
    if (list && list.type !== type) flushList();
    if (!list) list = { type, items: [] };
    list.items.push(text);
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m: RegExpExecArray | null;
    if (/^\s*```/.test(line)) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
    } else if (/^\s*$/.test(line)) {
      flush();
    } else if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
      flush();
      const level = Math.min(m[1].length + 2, 6);
      out.push(`<h${level}>${inline(esc(m[2]))}</h${level}>`);
    } else if (/^\s*([-*_])\s*(?:\1\s*){2,}$/.test(line)) {
      flush();
      out.push('<hr>');
    } else if ((m = /^\s*[-*+]\s+(.*)$/.exec(line))) {
      addItem('ul', m[1]);
    } else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
      addItem('ol', m[1]);
    } else if ((m = /^\s*>\s?(.*)$/.exec(line))) {
      flush();
      out.push(`<blockquote>${inline(esc(m[1]))}</blockquote>`);
    } else {
      flushList();
      para.push(line);
    }
  }
  flush();
  return out.join('');
}

@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    return renderMarkdown(value);
  }
}
