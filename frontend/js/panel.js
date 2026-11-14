'use strict';

/* Side panel: preview of what the model made (HTML pages, SVG images, Markdown documents, dashboards).
   HTML runs in a sandboxed iframe with no access to this app, its cookies or its API. */
const Panel = (() => {
  const A = window.App;
  const { $, $$, esc, icon } = A;
  const KINDS = { html: 'HTML page', svg: 'SVG image', markdown: 'Document', dashboard: 'Dashboard' };
  const LANG_KIND = { html: 'html', svg: 'svg', markdown: 'markdown', md: 'markdown' };
  const CSP =
    "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com " +
    "https://unpkg.com https://code.jquery.com https://cdn.tailwindcss.com; style-src 'unsafe-inline' https:; " +
    "img-src data: blob: https:; font-src data: https:; connect-src https:; media-src data: blob: https:";

  const panel = $('#panel');
  const body = $('#panel-body');
  let items = [];
  let cur = -1;
  let tab = 'preview';

  const isOpen = () => !panel.classList.contains('hidden');

  function titleOf(kind, content, n) {
    if (kind === 'html') {
      const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(content);
      if (m && m[1].trim()) return m[1].trim().slice(0, 50);
    }
    if (kind === 'markdown') {
      const m = /^\s*#{1,3}\s+(.+)$/m.exec(content);
      if (m) return m[1].trim().slice(0, 50);
    }
    return `${KINDS[kind]} ${n}`;
  }

  /** Everything previewable in the open chat, in reading order. */
  function collect() {
    const found = [];
    const counts = {};
    $$('#thread .code-block[data-lang], #thread .dash-block').forEach((el) => {
      if (el.classList.contains('dash-block')) {
        if (el._dash) {
          counts.dashboard = (counts.dashboard || 0) + 1;
          found.push({ kind: 'dashboard', title: el._dash.title || `Dashboard ${counts.dashboard}`, spec: el._dash, content: JSON.stringify(el._dash, null, 2) });
        }
        return;
      }
      const kind = LANG_KIND[el.dataset.lang];
      if (!kind) return;
      const content = $('code', el).textContent;
      counts[kind] = (counts[kind] || 0) + 1;
      found.push({ kind, title: titleOf(kind, content, counts[kind]), content });
    });
    return found;
  }

  function highlight(code, lang) {
    if (window.hljs && lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value; } catch (_) { /* fall through */ }
    }
    return esc(code);
  }

  function render() {
    const it = items[cur];
    $('#panel-pick').innerHTML = items.map((x, i) => `<option value="${i}"${i === cur ? ' selected' : ''}>${esc(x.title)} — ${KINDS[x.kind]}</option>`).join('') || '<option>Nothing yet</option>';
    $$('.panel-tabs button', panel).forEach((b) => b.classList.toggle('active', b.dataset.ptab === tab));
    if (!it) {
      body.innerHTML = '<div class="panel-empty">Nothing to preview yet.<br><span class="muted small">HTML pages, SVG images, Markdown documents and dashboards from the chat open here.</span></div>';
      return;
    }
    if (tab === 'code') {
      body.innerHTML = `<pre class="panel-code"><code class="hljs">${highlight(it.content, it.kind === 'dashboard' ? 'json' : it.kind === 'html' ? 'xml' : it.kind === 'svg' ? 'xml' : 'markdown')}</code></pre>`;
      return;
    }
    if (it.kind === 'html') {
      body.innerHTML = '<iframe id="panel-frame" sandbox="allow-scripts allow-forms allow-popups allow-modals" referrerpolicy="no-referrer" title="Preview"></iframe>';
      $('#panel-frame').srcdoc = `<meta http-equiv="Content-Security-Policy" content="${CSP}"><meta charset="utf-8">` + it.content;
    } else if (it.kind === 'svg') {
      const clean = window.DOMPurify ? DOMPurify.sanitize(it.content, { USE_PROFILES: { svg: true, svgFilters: true } }) : '';
      body.innerHTML = `<div class="panel-svg">${clean || '<span class="muted">This SVG could not be shown.</span>'}</div>`;
    } else if (it.kind === 'markdown') {
      body.innerHTML = `<div class="md panel-doc">${Render.markdown(it.content)}</div>`;
      Render.renderBlocks(body);
    } else if (it.kind === 'dashboard') {
      body.innerHTML = '<div class="dash-block panel-dash"></div>';
      Viz.renderDashboard($('.panel-dash', body), it.spec);
    }
  }

  function show() {
    panel.classList.remove('hidden');
    document.body.classList.add('panel-open');
    render();
  }

  function close() {
    panel.classList.add('hidden');
    document.body.classList.remove('panel-open');
    body.innerHTML = ''; // stops anything running in the preview
  }

  function openItem(match) {
    items = collect();
    let i = items.findIndex(match);
    if (i < 0) i = items.length - 1;
    cur = i;
    tab = 'preview';
    show();
  }

  function openCode(text, lang) {
    const kind = LANG_KIND[(lang || '').toLowerCase()];
    if (!kind) return;
    openItem((x) => x.kind === kind && x.content === text);
  }

  function toggle() {
    if (isOpen()) { close(); return; }
    items = collect();
    cur = items.length - 1;
    tab = 'preview';
    show();
  }

  /** After a reply finishes: if it contains a real HTML page, show it (desktop only). */
  function autoOpen(msgEl) {
    if (innerWidth < 1000) return;
    const blocks = $$('.code-block[data-lang="html"]', msgEl).filter((b) => $('code', b).textContent.length >= 400);
    if (!blocks.length) return;
    const text = $('code', blocks[blocks.length - 1]).textContent;
    openItem((x) => x.kind === 'html' && x.content === text);
  }

  $('#panel-close').innerHTML = icon('x');
  $('#panel-copy').innerHTML = icon('copy', 16);
  $('#panel-download').innerHTML = icon('download', 16);
  $('#panel-close').onclick = close;
  $('#panel-pick').onchange = (e) => { cur = +e.target.value; render(); };
  $('.panel-tabs', panel).addEventListener('click', (e) => {
    const b = e.target.closest('[data-ptab]');
    if (b) { tab = b.dataset.ptab; render(); }
  });
  $('#panel-copy').onclick = () => { if (items[cur]) A.copyText(items[cur].content).then(() => A.toast('Copied')); };
  $('#panel-download').onclick = () => {
    const it = items[cur];
    if (!it) return;
    const ext = { html: 'html', svg: 'svg', markdown: 'md', dashboard: 'json' }[it.kind];
    const mime = { html: 'text/html', svg: 'image/svg+xml', markdown: 'text/markdown', dashboard: 'application/json' }[it.kind];
    A.download(`${it.title.replace(/[^\w-]+/g, '_') || 'artifact'}.${ext}`, it.content, mime);
  };
  document.addEventListener('open-dash-panel', (e) => openItem((x) => x.kind === 'dashboard' && x.spec === e.detail));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isOpen() && !document.querySelector('dialog[open]') && !document.body.classList.contains('viz-open')) close(); });
  // a different chat has different artifacts
  window.addEventListener('hashchange', () => { if (isOpen()) close(); });

  return { openCode, toggle, close, autoOpen };
})();
window.Panel = Panel; // a top-level `const` is not a window property
