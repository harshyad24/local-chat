'use strict';

/* Markdown / math / code / mermaid rendering. Everything is sanitized with DOMPurify. */
const Render = (() => {
  const esc = (s) =>
    String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const EXT = {
    python: 'py', py: 'py', javascript: 'js', js: 'js', typescript: 'ts', ts: 'ts', tsx: 'tsx', jsx: 'jsx',
    html: 'html', css: 'css', json: 'json', bash: 'sh', shell: 'sh', sh: 'sh', zsh: 'sh', powershell: 'ps1',
    java: 'java', c: 'c', cpp: 'cpp', csharp: 'cs', cs: 'cs', go: 'go', rust: 'rs', sql: 'sql', yaml: 'yml',
    yml: 'yml', xml: 'xml', markdown: 'md', md: 'md', ruby: 'rb', php: 'php', swift: 'swift', kotlin: 'kt',
    mermaid: 'mmd', svg: 'svg', toml: 'toml', ini: 'ini', diff: 'diff', lua: 'lua', r: 'r',
  };

  const PANEL_LANGS = new Set(['html', 'svg', 'markdown', 'md']); // blocks that can be previewed in the side panel

  function highlight(code, lang) {
    if (window.hljs && lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value; } catch (_) { /* fall through */ }
    }
    return esc(code);
  }

  if (window.marked) {
    marked.use({
      gfm: true,
      breaks: false,
      renderer: {
        code(code, info) {
          const lang = ((info || '').match(/^\S*/)[0] || '').toLowerCase();
          if (lang === 'mermaid') {
            return `<div class="mermaid-block"><pre class="mermaid-src"><code>${esc(code)}</code></pre></div>`;
          }
          if (['chart', 'viz', 'visualization', 'vizspec'].includes(lang)) {
            return `<div class="viz-block"><div class="viz-pending">Building visualization…</div><pre class="viz-src"><code>${esc(code)}</code></pre></div>`;
          }
          const label = lang || 'text';
          const ext = EXT[lang] || 'txt';
          return (
            `<div class="code-block" data-ext="${ext}" data-lang="${esc(lang)}">` +
            `<div class="code-head"><span class="code-lang">${esc(label)}</span><span class="spacer"></span>` +
            (PANEL_LANGS.has(lang) ? `<button class="code-btn" data-act="open-panel" title="Open in the side panel">Open</button>` : '') +
            `<button class="code-btn" data-act="download-code" title="Download as file">Download</button>` +
            `<button class="code-btn" data-act="copy-code" title="Copy code">Copy</button></div>` +
            `<pre><code class="hljs">${highlight(code, lang)}</code></pre></div>`
          );
        },
        table(header, body) {
          return `<div class="table-wrap"><table><thead>${header}</thead><tbody>${body}</tbody></table></div>`;
        },
      },
    });
  }

  if (window.DOMPurify) {
    DOMPurify.addHook('afterSanitizeAttributes', (node) => {
      if (node.tagName === 'A') {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    });
  }

  // --- math ---------------------------------------------------------------
  const FENCE = /(^|\n)(```|~~~)[^\n]*\n[\s\S]*?(?:\n\2[^\n]*(?=\n|$)|$)/g;
  const INLINE_CODE = /`[^`\n]+`/g;
  const INLINE_MATH = /(?<![\\$])\$(?!\s)([^\n$]+?)(?<!\s)\$(?!\d)/g;

  function renderMath(tex, display) {
    if (!window.katex) return `<code>${esc(tex)}</code>`;
    try {
      return katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false, trust: false });
    } catch (_) {
      return `<code>${esc(tex)}</code>`;
    }
  }

  /** Turn [1] / [2][3] / [1, 2] into links to the numbered sources (outside code and links). */
  function linkCitations(html, sources) {
    if (!sources || !sources.length) return html;
    const byN = new Map(sources.map((s) => [s.n, s]));
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const re = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;
    for (const node of nodes) {
      if (node.parentElement && node.parentElement.closest('pre, code, a')) continue;
      const text = node.nodeValue;
      if (!re.test(text)) continue;
      re.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0;
      for (const m of text.matchAll(re)) {
        const nums = m[1].split(',').map((x) => parseInt(x, 10)).filter((n) => byN.has(n));
        if (!nums.length) continue;
        frag.append(text.slice(last, m.index));
        for (const n of nums) {
          const s = byN.get(n);
          const a = document.createElement('a');
          a.className = 'cite';
          a.textContent = String(n);
          a.href = /^https?:\/\//i.test(s.url) ? s.url : '#';
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.title = `${s.title || s.domain} — ${s.domain || ''}`;
          frag.append(a);
        }
        last = m.index + m[0].length;
      }
      frag.append(text.slice(last));
      node.replaceWith(frag);
    }
    return tpl.innerHTML;
  }

  /** Markdown string -> sanitized HTML (math and code blocks included). opts.sources numbers citations. */
  function markdown(src, opts) {
    if (!window.marked || !window.DOMPurify) {
      return `<p>${esc(src).replace(/\n/g, '<br>')}</p>`;
    }
    const code = [];
    const math = [];
    const stashCode = (m) => { code.push(m); return `ZZCODE${code.length - 1}ZZ`; };
    const stashMath = (tex, display) => { math.push(renderMath(tex, display)); return `ZZMATH${math.length - 1}ZZ`; };

    let t = src.replace(FENCE, stashCode).replace(INLINE_CODE, stashCode);
    t = t
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, x) => stashMath(x, true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, x) => stashMath(x, true))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, x) => stashMath(x, false))
      .replace(INLINE_MATH, (_, x) => stashMath(x, false));
    t = t.replace(/ZZCODE(\d+)ZZ/g, (_, i) => code[+i]);

    let html = DOMPurify.sanitize(marked.parse(t), { ADD_ATTR: ['target'] });
    html = linkCitations(html, opts && opts.sources);
    // KaTeX output is generated by us from a trusted library, so it is inserted after sanitizing.
    return html.replace(/ZZMATH(\d+)ZZ/g, (_, i) => math[+i]);
  }

  // --- reasoning ----------------------------------------------------------
  /** Split "<think>…</think>answer" into parts. `open` is true while reasoning is still streaming. */
  function splitThinking(text) {
    if (/^\s*<(t(h(i(n(k)?)?)?)?)?$/.test(text)) return { thinking: null, answer: '', open: false };
    const m = text.match(/^\s*<think>([\s\S]*?)(<\/think>|$)/);
    if (!m) return { thinking: null, answer: text, open: false };
    const closed = m[2] === '</think>';
    return {
      thinking: m[1].trim(),
      answer: closed ? text.slice(m[0].length).replace(/^\s+/, '') : '',
      open: !closed,
    };
  }

  // --- mermaid ------------------------------------------------------------
  let mermaidN = 0;
  function setMermaidTheme(dark) {
    if (!window.mermaid) return;
    try {
      // htmlLabels:false keeps node text as plain SVG <text>, so PNG/SVG export works (foreignObject taints the canvas)
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'default', flowchart: { htmlLabels: false, curve: 'step' } });
    } catch (_) { /* ignore */ }
  }

  async function renderMermaid(root) {
    if (!window.mermaid) return;
    for (const el of root.querySelectorAll('.mermaid-block:not([data-done])')) {
      el.dataset.done = '1';
      const id = 'mmd' + (++mermaidN);
      try {
        const src = el.querySelector('code').textContent;
        const { svg } = await mermaid.render(id, src);
        el.innerHTML = svg;
        el.classList.add('rendered');
        if (typeof Exporter !== 'undefined') Exporter.decorateFlowchart(el, svg, src);
      } catch (err) {
        console.warn('Mermaid render failed:', err);
        document.getElementById('d' + id)?.remove();
        el.classList.add('failed');
        el.insertAdjacentHTML('afterbegin', '<div class="mermaid-err">Couldn’t render this diagram — showing source.' +
          (err && err.message ? ` <small>(${esc(String(err.message).split('\n')[0].slice(0, 140))})</small>` : '') + '</div>');
      }
    }
  }

  /** Render everything that needs a post-pass: mermaid flowcharts and chart/graph specs. */
  function renderBlocks(root, forceViz) {
    if (typeof Viz !== 'undefined') Viz.renderAll(root, !!forceViz); // `const Viz` is not a window property
    return renderMermaid(root);
  }

  return { esc, markdown, splitThinking, renderMermaid, renderBlocks, setMermaidTheme };
})();
