'use strict';
/* Export helpers: flowchart (Mermaid SVG) → PNG/SVG/clipboard, dashboard → PNG/CSV/JSON, chart → PNG/clipboard.
   No extra libraries: the dashboard image is composed on a canvas from the spec the app already holds, so it always
   contains the whole dashboard (not just the visible part of the page) and never depends on DOM screenshots. */
const Exporter = (() => {
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const slug = (s) => String(s || 'export').replace(/[^\w-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'export';
  const toast = (m) => document.dispatchEvent(new CustomEvent('viz-toast', { detail: m }));

  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
  const canvasBlob = (cv) => new Promise((res, rej) => cv.toBlob((b) => (b ? res(b) : rej(new Error('Could not create the image'))), 'image/png'));

  /** Copy a PNG blob as an image; if the browser can't, download it instead and say so. */
  async function copyOrSave(blob, name) {
    try {
      if (!navigator.clipboard || !window.ClipboardItem) throw new Error('unsupported');
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      toast('Image copied — paste it into a document.');
      return 'copied';
    } catch (_) {
      saveBlob(blob, name);
      toast('This browser can’t copy images — downloaded it instead.');
      return 'downloaded';
    }
  }

  /* ---------------- flowcharts (Mermaid SVG) ---------------- */
  function svgSize(svg) {
    const vb = svg.viewBox && svg.viewBox.baseVal;
    if (vb && vb.width && vb.height) return { w: vb.width, h: vb.height };
    const bb = svg.getBBox();
    return { w: Math.ceil(bb.x + bb.width) + 8, h: Math.ceil(bb.y + bb.height) + 8 };
  }

  /** A standalone, document-compatible SVG string (explicit size, namespace, optional background). */
  function svgString(svg, bg) {
    const { w, h } = svgSize(svg);
    const c = svg.cloneNode(true);
    c.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    c.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
    c.setAttribute('width', Math.ceil(w));
    c.setAttribute('height', Math.ceil(h));
    if (!c.getAttribute('viewBox')) c.setAttribute('viewBox', `0 0 ${w} ${h}`);
    c.style.maxWidth = 'none';
    c.removeAttribute('style');
    if (bg) {
      const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      r.setAttribute('width', '100%'); r.setAttribute('height', '100%'); r.setAttribute('fill', bg);
      c.insertBefore(r, c.firstChild);
    }
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(c);
  }

  async function svgToCanvas(svg, scale, bg) {
    const { w, h } = svgSize(svg);
    // keep the bitmap inside what browsers can allocate
    const k = Math.max(0.25, Math.min(scale, 8192 / Math.max(w, h)));
    const url = URL.createObjectURL(new Blob([svgString(svg, bg)], { type: 'image/svg+xml;charset=utf-8' }));
    try {
      const img = new Image();
      img.decoding = 'sync';
      await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new Error('The browser could not rasterize this diagram')); img.src = url; });
      const cv = document.createElement('canvas');
      cv.width = Math.ceil(w * k); cv.height = Math.ceil(h * k);
      const ctx = cv.getContext('2d');
      ctx.fillStyle = bg || '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.drawImage(img, 0, 0, cv.width, cv.height);
      return cv;
    } finally { URL.revokeObjectURL(url); }
  }

  function diagramName(el) {
    const t = el.querySelector('svg title, .titleText');
    return slug((t && t.textContent) || 'flowchart');
  }

  const bgOf = () => css('--panel', '#ffffff');

  async function flowchartAction(el, act) {
    const svg = el.querySelector('.mm-stage svg');
    if (!svg) return;
    const name = diagramName(el);
    try {
      if (act === 'svg') saveBlob(new Blob([svgString(svg, bgOf())], { type: 'image/svg+xml' }), `${name}.svg`);
      else if (act === 'png') saveBlob(await canvasBlob(await svgToCanvas(svg, 3, bgOf())), `${name}.png`);
      else if (act === 'copy-image') await copyOrSave(await canvasBlob(await svgToCanvas(svg, 3, bgOf())), `${name}.png`);
      else if (act === 'copy-src') {
        await navigator.clipboard.writeText(el._src || '');
        toast('Mermaid source copied.');
      }
    } catch (err) { toast(`Export failed: ${err.message || err}`); }
  }

  function applyView(el) {
    const st = el.querySelector('.mm-stage');
    const v = el._view;
    if (st) st.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.k})`;
  }

  function fitView(el) {
    const vp = el.querySelector('.mm-viewport'), svg = el.querySelector('.mm-stage svg');
    if (!vp || !svg) return;
    const { w, h } = svgSize(svg);
    const k = Math.min(1, (vp.clientWidth - 16) / w, (vp.clientHeight - 16) / h) || 1;
    el._view = { k, x: Math.max(8, (vp.clientWidth - w * k) / 2), y: Math.max(8, (vp.clientHeight - h * k) / 2) };
    applyView(el);
  }

  function zoomBy(el, f, cx, cy) {
    const vp = el.querySelector('.mm-viewport');
    const v = el._view;
    const px = cx === undefined ? vp.clientWidth / 2 : cx, py = cy === undefined ? vp.clientHeight / 2 : cy;
    const k = Math.max(0.1, Math.min(8, v.k * f));
    v.x = px - ((px - v.x) / v.k) * k; v.y = py - ((py - v.y) / v.k) * k; v.k = k;
    applyView(el);
  }

  /** Wrap a rendered Mermaid SVG in a viewport with a toolbar (export, zoom, fit, fullscreen) and drag-to-pan. */
  function decorateFlowchart(el, svgMarkup, src) {
    el._src = src;
    el.innerHTML =
      `<div class="mm-bar"><span class="mm-label">Flowchart</span><div class="viz-tools">` +
      `<button data-mm="zoom-in" title="Zoom in">+</button><button data-mm="zoom-out" title="Zoom out">−</button><button data-mm="fit" title="Fit to screen">Fit</button>` +
      `<button data-mm="png" title="Download PNG">PNG</button><button data-mm="svg" title="Download SVG">SVG</button>` +
      `<button data-mm="copy-image" title="Copy as image">Copy image</button><button data-mm="copy-src" title="Copy Mermaid source">Source</button>` +
      `<button data-mm="full" title="Full screen">⤢</button></div></div>` +
      `<div class="mm-viewport"><div class="mm-stage">${svgMarkup}</div></div>`;
    const svg = el.querySelector('.mm-stage svg');
    if (svg) { const { w, h } = svgSize(svg); svg.setAttribute('width', w); svg.setAttribute('height', h); svg.style.maxWidth = 'none'; }
    el._view = { k: 1, x: 8, y: 8 };
    const vp = el.querySelector('.mm-viewport');
    // fit the diagram into the box it starts in (never enlarge)
    const { w, h } = svg ? svgSize(svg) : { w: 1, h: 1 };
    vp.style.height = Math.min(560, Math.max(160, h + 24)) + 'px';
    requestAnimationFrame(() => fitView(el));
    let drag = null;
    vp.addEventListener('pointerdown', (e) => { drag = { x: e.clientX - el._view.x, y: e.clientY - el._view.y }; vp.setPointerCapture(e.pointerId); vp.classList.add('panning'); });
    vp.addEventListener('pointermove', (e) => { if (!drag) return; el._view.x = e.clientX - drag.x; el._view.y = e.clientY - drag.y; applyView(el); });
    const end = () => { drag = null; vp.classList.remove('panning'); };
    vp.addEventListener('pointerup', end); vp.addEventListener('pointercancel', end);
    vp.addEventListener('wheel', (e) => {
      if (!el.classList.contains('mm-full') && !e.ctrlKey) return; // normal scrolling keeps working in the chat
      e.preventDefault();
      const r = vp.getBoundingClientRect();
      zoomBy(el, e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
  }

  function flowchartClick(e) {
    const b = e.target.closest('[data-mm]');
    if (!b) return false;
    const el = b.closest('.mermaid-block');
    if (!el) return false;
    const act = b.dataset.mm;
    if (act === 'zoom-in') zoomBy(el, 1.25);
    else if (act === 'zoom-out') zoomBy(el, 1 / 1.25);
    else if (act === 'fit') fitView(el);
    else if (act === 'full') {
      const on = el.classList.toggle('mm-full');
      document.body.classList.toggle('viz-open', on);
      b.textContent = on ? '✕' : '⤢';
      const vp = el.querySelector('.mm-viewport');
      vp.style.height = on ? '' : vp.dataset.h || vp.style.height;
      setTimeout(() => fitView(el), 60);
    } else flowchartAction(el, act);
    return true;
  }

  /* ---------------- single charts ---------------- */
  async function chartBlob(block, scale) {
    const url = block._png();
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
    const cv = document.createElement('canvas');
    cv.width = img.naturalWidth; cv.height = img.naturalHeight;
    const ctx = cv.getContext('2d');
    ctx.fillStyle = css('--panel', '#fff'); ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0);
    return canvasBlob(cv);
  }
  async function chartAction(block, act) {
    try {
      const blob = await chartBlob(block);
      const name = slug((block._viz && block._viz.title) || 'chart') + '.png';
      if (act === 'copy-image') await copyOrSave(blob, name); else saveBlob(blob, name);
    } catch (err) { toast(`Export failed: ${err.message || err}`); }
  }

  /* ---------------- dashboards ---------------- */
  function wrapLines(ctx, text, maxW, maxLines) {
    const words = String(text).split(/\s+/); const lines = []; let cur = '';
    for (const w of words) {
      const t = cur ? cur + ' ' + w : w;
      if (ctx.measureText(t).width <= maxW || !cur) cur = t; else { lines.push(cur); cur = w; }
    }
    if (cur) lines.push(cur);
    if (maxLines && lines.length > maxLines) { lines.length = maxLines; lines[maxLines - 1] = lines[maxLines - 1].replace(/.{0,2}$/, '…'); }
    return lines;
  }
  const fit1 = (ctx, t, maxW) => { let s = String(t); while (s.length > 1 && ctx.measureText(s).width > maxW) s = s.slice(0, -2) + '…'; return s; };
  function rrect(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  async function dashboardCanvas(root, scale) {
    const spec = root._dash;
    if (!spec) throw new Error('No dashboard data');
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const blocks = [...root.querySelectorAll('.viz-block')];
    const imgs = await Promise.all(blocks.map((b) => new Promise((res) => {
      if (!b._png) return res(null);
      try { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = b._png(); } catch (_) { res(null); }
    })));
    const C = { bg: css('--bg', '#faf9f5'), panel: css('--panel', '#fff'), text: css('--text', '#1f1e1d'), muted: css('--muted', '#6f6d66'), border: css('--border', '#e3e0d5'), accent: css('--accent', '#c15f3c') };
    const font = getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif';
    const W = 1200, PAD = 32, GAP = 16, inner = W - PAD * 2;
    const measure = document.createElement('canvas').getContext('2d');

    // ---- layout pass (heights), then draw pass
    let y = PAD;
    const plan = [];
    plan.push({ t: 'head', y }); y += 70;
    const n = spec.kpis.length || 1;
    const perRow = n <= 4 ? n : 3, kw = (inner - GAP * (perRow - 1)) / perRow, kh = 92;
    const krows = Math.ceil(spec.kpis.length / perRow);
    plan.push({ t: 'kpis', y, kw, kh, perRow }); y += krows * kh + (krows - 1) * GAP + GAP + 8;
    // chart grid: wide = full row, half = two per row
    const cells = spec.charts.map((c, i) => ({ c, i, img: imgs[i] }));
    let row = [];
    const flush = () => {
      if (!row.length) return;
      const widths = row.length === 1 ? [inner] : row.map(() => (inner - GAP) / 2);
      const hs = row.map((r, j) => {
        const im = r.img; const base = r.c.wide ? 340 : 300;
        return im && im.naturalWidth ? Math.max(200, Math.min(460, (widths[j] - 24) * (im.naturalHeight / im.naturalWidth)) + 44) : base;
      });
      const h = Math.max(...hs);
      let x = PAD;
      row.forEach((r, j) => { plan.push({ t: 'chart', x, y, w: widths[j], h, cell: r }); x += widths[j] + GAP; });
      y += h + GAP; row = [];
    };
    for (const cell of cells) {
      if (cell.c.wide) { flush(); row = [cell]; flush(); } else { row.push(cell); if (row.length === 2) flush(); }
    }
    flush();
    if (spec.table) {
      const rows = spec.table.rows.slice(0, 200);
      const h = 54 + 34 + rows.length * 30 + 12;
      plan.push({ t: 'table', y, h, rows }); y += h + GAP;
    }
    const H = Math.ceil(y + PAD - GAP);
    const k = Math.max(0.5, Math.min(scale, 16000 / H, 8000 / W));
    const cv = document.createElement('canvas'); cv.width = Math.ceil(W * k); cv.height = Math.ceil(H * k);
    const ctx = cv.getContext('2d'); ctx.scale(k, k);
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    ctx.textBaseline = 'alphabetic';

    for (const p of plan) {
      if (p.t === 'head') {
        ctx.fillStyle = C.text; ctx.font = `700 26px ${font}`; ctx.fillText(fit1(ctx, spec.title, inner), PAD, p.y + 28);
        ctx.fillStyle = C.muted; ctx.font = `400 14px ${font}`;
        ctx.fillText((spec.template_name || 'Dashboard') + (spec.period ? ' · ' + spec.period : ''), PAD, p.y + 52);
      } else if (p.t === 'kpis') {
        spec.kpis.forEach((kp, i) => {
          const x = PAD + (i % p.perRow) * (p.kw + GAP), yy = p.y + Math.floor(i / p.perRow) * (p.kh + GAP);
          ctx.fillStyle = C.panel; rrect(ctx, x, yy, p.kw, p.kh, 12); ctx.fill(); ctx.strokeStyle = C.border; ctx.lineWidth = 1; ctx.stroke();
          ctx.fillStyle = C.muted; ctx.font = `600 12px ${font}`; ctx.fillText(fit1(ctx, String(kp.label).toUpperCase(), p.kw - 28), x + 14, yy + 26);
          ctx.fillStyle = C.text; ctx.font = `700 28px ${font}`; ctx.fillText(fit1(ctx, kp.value, p.kw - 28), x + 14, yy + 58);
          if (typeof kp.change === 'number' && isFinite(kp.change)) {
            const el = root.querySelectorAll('.kpi-delta')[i];
            ctx.fillStyle = el ? getComputedStyle(el).color : C.muted; ctx.font = `600 13px ${font}`;
            ctx.fillText(`${kp.change > 0 ? '▲' : kp.change < 0 ? '▼' : '■'} ${Math.abs(kp.change).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`, x + 14, yy + 79);
          }
        });
      } else if (p.t === 'chart') {
        ctx.fillStyle = C.panel; rrect(ctx, p.x, p.y, p.w, p.h, 12); ctx.fill(); ctx.strokeStyle = C.border; ctx.stroke();
        ctx.fillStyle = C.text; ctx.font = `600 14px ${font}`; ctx.fillText(fit1(ctx, p.cell.c.spec.title || '', p.w - 28), p.x + 14, p.y + 26);
        const im = p.cell.img;
        if (im && im.naturalWidth) {
          const aw = p.w - 24, ah = p.h - 44; const r = Math.min(aw / im.naturalWidth, ah / im.naturalHeight);
          const dw = im.naturalWidth * r, dh = im.naturalHeight * r;
          ctx.drawImage(im, p.x + 12 + (aw - dw) / 2, p.y + 36 + (ah - dh) / 2, dw, dh);
        } else { ctx.fillStyle = C.muted; ctx.font = `400 13px ${font}`; ctx.fillText('Chart unavailable', p.x + 14, p.y + 60); }
      } else if (p.t === 'table') {
        ctx.fillStyle = C.panel; rrect(ctx, PAD, p.y, inner, p.h, 12); ctx.fill(); ctx.strokeStyle = C.border; ctx.stroke();
        ctx.fillStyle = C.text; ctx.font = `600 14px ${font}`; ctx.fillText(fit1(ctx, spec.table.title, inner - 28), PAD + 14, p.y + 28);
        const cols = spec.table.columns, cw = (inner - 28) / cols.length;
        ctx.fillStyle = C.muted; ctx.font = `600 12px ${font}`;
        cols.forEach((c, i) => ctx.fillText(fit1(ctx, c, cw - 10), PAD + 14 + i * cw, p.y + 62));
        ctx.font = `400 13px ${font}`;
        p.rows.forEach((r, ri) => {
          const ry = p.y + 78 + ri * 30;
          ctx.strokeStyle = C.border; ctx.beginPath(); ctx.moveTo(PAD + 14, ry); ctx.lineTo(PAD + inner - 14, ry); ctx.stroke();
          ctx.fillStyle = C.text;
          r.forEach((c, i) => ctx.fillText(fit1(ctx, c, cw - 10), PAD + 14 + i * cw, ry + 20));
        });
      }
    }
    return cv;
  }

  const csvCell = (v) => { const s = v === null || v === undefined ? '' : String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csvRows = (rows) => rows.map((r) => r.map(csvCell).join(',')).join('\r\n');

  /** Everything numeric/tabular in the dashboard as one CSV (sections separated by a blank line). */
  function dashboardCSV(spec, root) {
    const out = [];
    out.push(csvRows([['Section', 'KPIs'], ['Label', 'Value', 'Change %'], ...spec.kpis.map((k) => [k.label, k.value, k.change === undefined ? '' : k.change])]));
    spec.charts.forEach((c, i) => {
      const b = root && root.querySelector(`.viz-block[data-ci="${i}"]`);
      const title = (b && b._viz && b._viz.title) || (c.spec && c.spec.title) || `Chart ${i + 1}`;
      if (b && b._viz) {
        for (const [head, rows] of Viz.dataTables(b._viz)) out.push(csvRows([['Chart', title], head, ...rows]));
      } else out.push(csvRows([['Chart', title]]));
    });
    if (spec.table) out.push(csvRows([['Table', spec.table.title], spec.table.columns, ...spec.table.rows]));
    return '﻿' + out.join('\r\n\r\n') + '\r\n';
  }

  async function dashboardAction(root, act) {
    const spec = root._dash;
    if (!spec) return;
    const name = slug(spec.title || 'dashboard');
    try {
      if (act === 'csv') saveBlob(new Blob([dashboardCSV(spec, root)], { type: 'text/csv;charset=utf-8' }), `${name}.csv`);
      else if (act === 'json') saveBlob(new Blob([JSON.stringify(spec, null, 2)], { type: 'application/json' }), `${name}.json`);
      else {
        toast('Preparing image…');
        const blob = await canvasBlob(await dashboardCanvas(root, 2));
        if (act === 'copy-image') await copyOrSave(blob, `${name}.png`); else saveBlob(blob, `${name}.png`);
      }
    } catch (err) { toast(`Export failed: ${err.message || err}`); }
  }

  return { svgToCanvas, flowchartClick, decorateFlowchart, chartAction, dashboardAction, dashboardCanvas, dashboardCSV, svgString, csvRows, slug };
})();
if (typeof module !== 'undefined') module.exports = Exporter;
