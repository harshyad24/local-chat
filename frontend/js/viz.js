'use strict';

/* Renders validated visualization specs (see viz-schema.js) inside chat messages.
 *   statistical charts -> Chart.js        graphs / trees / ER -> Cytoscape.js
 * Flowcharts are NOT handled here; they keep using mermaid.
 */
const Viz = (() => {
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const css = (name, fallback) => (getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback);
  const palette = () => Array.from({ length: 8 }, (_, i) => css(`--c${i + 1}`, ['#c15f3c', '#4f7cac', '#5a9a6b', '#b98a2e', '#8b6bb1', '#c2587a', '#3f9aa8', '#7c7f86'][i]));
  const alpha = (hex, a) => {
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
  };
  const fmt = (v) => (typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v);

  /* ---------- card shell ---------- */
  function shell(block, viz, warnings) {
    const typeLabel = viz.type.replace(/_/g, ' ');
    block.innerHTML =
      `<div class="viz-head"><div class="viz-title">${esc(viz.title || typeLabel)}</div><span class="viz-type">${esc(typeLabel)}</span>` +
      `<div class="viz-tools">` +
      (viz.kind === 'graph' ? `<button data-viz="zoom-in" title="Zoom in">+</button><button data-viz="zoom-out" title="Zoom out">−</button><button data-viz="fit" title="Fit to view">Fit</button>` : '') +
      `<button data-viz="data" title="Show the data behind this">Data</button>` +
      `<button data-viz="png" title="Download as PNG">PNG</button><button data-viz="copy-image" title="Copy as image">Copy</button>` +
      `<button data-viz="expand" title="Expand">⤢</button></div></div>` +
      `<div class="viz-body">${viz.kind === 'chart' ? '<canvas></canvas>' : '<div class="viz-graph"></div>'}</div>` +
      `<div class="viz-info hidden"></div>` +
      (warnings.length ? `<div class="viz-warn">${warnings.map(esc).join(' · ')}</div>` : '') +
      `<div class="viz-data hidden"></div>`;
  }

  function dataTableHTML(viz) {
    const table = (head, rows) =>
      `<div class="viz-table-wrap"><table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows
        .map((r) => `<tr>${r.map((c) => `<td>${esc(c === null || c === undefined ? '—' : fmt(c))}</td>`).join('')}</tr>`)
        .join('')}</tbody></table></div>`;
    if (viz.kind === 'graph') {
      return table(['Node', 'Label', 'Group'], viz.nodes.map((n) => [n.id, n.label, n.group])) + table(['From', 'To', 'Relationship'], viz.edges.map((e) => [e.source, e.target, e.label]));
    }
    if (viz.table) return table(viz.table.head, viz.table.rows);
    if (viz.shape === 'scatter' || viz.shape === 'bubble') {
      const rows = [];
      viz.series.forEach((s) => s.points.forEach((p) => rows.push(viz.shape === 'bubble' ? [s.name, p.x, p.y, p.r] : [s.name, p.x, p.y])));
      return table(['Series', viz.xLabel || 'x', viz.yLabel || 'y', ...(viz.shape === 'bubble' ? ['size'] : [])], rows);
    }
    return table([viz.xLabel || 'Category', ...viz.series.map((s) => s.name)], viz.labels.map((l, i) => [l, ...viz.series.map((s) => s.values[i])]));
  }

  /* ---------- statistical charts ---------- */
  const CANVAS_SHAPES = new Set(['heatmap', 'treemap', 'sankey']); // drawn by hand on a <canvas>, not by Chart.js

  function theme() {
    return { colors: palette(), text: css('--muted', '#888'), fg: css('--text', '#222'), grid: css('--border', '#ddd'), panel: css('--panel', '#fff'), bad: css('--danger', '#c0392b') };
  }

  /** The numeric value of a hovered point (horizontal bars store it on x, everything else on y). */
  function pointValue(c) {
    const p = c.parsed;
    if (p && typeof p === 'object') return c.chart.options.indexAxis === 'y' ? p.x : p.y;
    return p;
  }

  /** Axis min/max with 6% headroom, for charts whose bars don't span the whole data range. */
  function padRange(values) {
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.06;
    return { min: lo - pad, max: hi + pad };
  }

  function buildChart(block, viz) {
    if (CANVAS_SHAPES.has(viz.shape)) return buildCanvas(block, viz);
    if (!window.Chart) throw new Error('Chart.js failed to load (offline?).');
    const { colors, text, grid, panel, bad } = theme();
    const shape = viz.shape;
    const multi = (viz.series || []).length > 1;
    const circular = shape === 'pie' || shape === 'donut';
    const plugins = [];
    let config;

    plugins.push({
      id: 'panelBg',
      beforeDraw(chart) {
        const { ctx } = chart;
        ctx.save();
        ctx.globalCompositeOperation = 'destination-over';
        ctx.fillStyle = panel;
        ctx.fillRect(0, 0, chart.width, chart.height);
        ctx.restore();
      },
    });

    const scales = {
      x: { title: { display: !!viz.xLabel, text: viz.xLabel, color: text }, ticks: { color: text, maxRotation: 45 }, grid: { color: grid } },
      y: { title: { display: !!viz.yLabel, text: viz.yLabel, color: text }, ticks: { color: text, callback: (v) => fmt(v) }, grid: { color: grid }, beginAtZero: !['line', 'step', 'scatter', 'bubble', 'dot', 'box', 'candle'].includes(shape) },
    };
    const legend = (show, pos) => ({ display: show, position: pos || 'top', labels: { color: text } });
    const radialScale = { r: { angleLines: { color: grid }, grid: { color: grid }, pointLabels: { color: text }, ticks: { color: text, backdropColor: 'transparent' } } };

    if (circular) {
      const s = viz.series[0];
      config = {
        type: shape === 'donut' ? 'doughnut' : 'pie',
        data: { labels: viz.labels, datasets: [{ data: s.values, backgroundColor: viz.labels.map((_, i) => colors[i % colors.length]), borderColor: panel, borderWidth: 2 }] },
        options: { plugins: { legend: legend(true, 'right') } },
      };
    } else if (shape === 'polar') {
      config = {
        type: 'polarArea',
        data: { labels: viz.labels, datasets: [{ data: viz.series[0].values, backgroundColor: viz.labels.map((_, i) => alpha(colors[i % colors.length], 0.7)), borderColor: panel, borderWidth: 1 }] },
        options: { scales: radialScale, plugins: { legend: legend(true, 'right') } },
      };
    } else if (shape === 'radar') {
      config = {
        type: 'radar',
        data: { labels: viz.labels, datasets: viz.series.map((s, i) => ({ label: s.name, data: s.values, borderColor: colors[i % colors.length], backgroundColor: alpha(colors[i % colors.length], 0.2), pointBackgroundColor: colors[i % colors.length], borderWidth: 2 })) },
        options: { scales: radialScale, plugins: { legend: legend(multi) } },
      };
    } else if (shape === 'scatter') {
      config = {
        type: 'scatter',
        data: { datasets: viz.series.map((s, i) => ({ label: s.name, data: s.points, backgroundColor: alpha(colors[i % colors.length], 0.75), borderColor: colors[i % colors.length], pointRadius: 4 })) },
        options: { scales, plugins: { legend: legend(multi) } },
      };
    } else if (shape === 'bubble') {
      const maxR = Math.max(...viz.series.flatMap((s) => s.points.map((p) => p.r)));
      config = {
        type: 'bubble',
        data: { datasets: viz.series.map((s, i) => ({ label: s.name, data: s.points.map((p) => ({ x: p.x, y: p.y, r: 5 + 22 * Math.sqrt(p.r / maxR), size: p.r })), backgroundColor: alpha(colors[i % colors.length], 0.6), borderColor: colors[i % colors.length] })) },
        options: { scales, plugins: { legend: legend(multi), tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: (${fmt(c.parsed.x)}, ${fmt(c.parsed.y)}) size ${fmt(c.raw.size)}` } } } },
      };
    } else if (shape === 'gauge') {
      const v = Math.min(viz.max, Math.max(viz.min, viz.value));
      config = {
        type: 'doughnut',
        data: { datasets: [{ data: [v - viz.min, viz.max - v], backgroundColor: [colors[0], grid], borderWidth: 0, circumference: 180, rotation: 270, cutout: '72%' }] },
        options: { plugins: { legend: { display: false }, tooltip: { enabled: false } } },
      };
      plugins.push({
        id: 'gaugeText',
        afterDraw(chart) {
          const arc = chart.getDatasetMeta(0).data[0];
          if (!arc) return;
          const { ctx } = chart;
          const r = (arc.outerRadius + arc.innerRadius) / 2;
          ctx.save();
          ctx.textAlign = 'center';
          ctx.fillStyle = css('--text', '#222');
          ctx.font = `600 ${Math.min(34, Math.max(16, arc.innerRadius * 0.28))}px sans-serif`;
          ctx.fillText(fmt(viz.value), arc.x, arc.y - 10);
          ctx.fillStyle = text;
          ctx.font = '12px sans-serif';
          ctx.fillText(fmt(viz.min), arc.x - r, arc.y + 16);
          ctx.fillText(fmt(viz.max), arc.x + r, arc.y + 16);
          ctx.restore();
        },
      });
    } else if (shape === 'box') {
      config = {
        type: 'bar',
        data: { labels: viz.labels, datasets: [{ label: 'Q1–Q3', data: viz.boxes.map((b) => [b.q1, b.q3]), backgroundColor: viz.boxes.map((_, i) => alpha(colors[i % colors.length], 0.45)), borderColor: viz.boxes.map((_, i) => colors[i % colors.length]), borderWidth: 2, borderSkipped: false }] },
        options: {
          scales: { ...scales, y: { ...scales.y, ...padRange(viz.boxes.flatMap((b) => [b.min, b.max, ...b.outliers])) } },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (c) => { const b = viz.boxes[c.dataIndex]; return [` median ${fmt(b.median)}`, ` Q1 ${fmt(b.q1)} · Q3 ${fmt(b.q3)}`, ` whiskers ${fmt(b.min)} – ${fmt(b.max)}`, ...(b.outliers.length ? [` outliers: ${b.outliers.map(fmt).join(', ')}`] : [])]; } } },
          },
        },
      };
      plugins.push({
        id: 'boxExtras',
        afterDatasetsDraw(chart) {
          const { ctx, scales: sc } = chart;
          const meta = chart.getDatasetMeta(0);
          ctx.save();
          viz.boxes.forEach((b, i) => {
            const bar = meta.data[i];
            if (!bar) return;
            const c = colors[i % colors.length];
            const x = bar.x;
            const half = bar.width / 2;
            const y = (v) => sc.y.getPixelForValue(v);
            ctx.strokeStyle = c;
            ctx.fillStyle = c;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(x - half, y(b.median)); ctx.lineTo(x + half, y(b.median));       // median
            ctx.moveTo(x, y(b.q3)); ctx.lineTo(x, y(b.max));                            // upper whisker
            ctx.moveTo(x, y(b.q1)); ctx.lineTo(x, y(b.min));                            // lower whisker
            ctx.moveTo(x - half / 2, y(b.max)); ctx.lineTo(x + half / 2, y(b.max));
            ctx.moveTo(x - half / 2, y(b.min)); ctx.lineTo(x + half / 2, y(b.min));
            ctx.stroke();
            b.outliers.forEach((o) => { ctx.beginPath(); ctx.arc(x, y(o), 3, 0, Math.PI * 2); ctx.fill(); });
          });
          ctx.restore();
        },
      });
    } else if (shape === 'candle') {
      const up = colors[2];
      config = {
        type: 'bar',
        data: { labels: viz.candles.map((c) => c.label), datasets: [{ label: 'Open–Close', data: viz.candles.map((c) => [c.open, c.close]), backgroundColor: viz.candles.map((c) => (c.close >= c.open ? up : bad)), borderColor: viz.candles.map((c) => (c.close >= c.open ? up : bad)), borderWidth: 1, borderSkipped: false }] },
        options: {
          scales: { ...scales, y: { ...scales.y, beginAtZero: false, ...padRange(viz.candles.flatMap((k) => [k.high, k.low])) } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => { const k = viz.candles[c.dataIndex]; return [` open ${fmt(k.open)}  close ${fmt(k.close)}`, ` high ${fmt(k.high)}  low ${fmt(k.low)}`]; } } } },
        },
      };
      plugins.push({
        id: 'wicks',
        afterDatasetsDraw(chart) {
          const { ctx, scales: sc } = chart;
          const meta = chart.getDatasetMeta(0);
          ctx.save();
          ctx.lineWidth = 1.5;
          viz.candles.forEach((k, i) => {
            const bar = meta.data[i];
            if (!bar) return;
            ctx.strokeStyle = k.close >= k.open ? up : bad;
            ctx.beginPath();
            ctx.moveTo(bar.x, sc.y.getPixelForValue(k.high));
            ctx.lineTo(bar.x, sc.y.getPixelForValue(k.low));
            ctx.stroke();
          });
          ctx.restore();
        },
      });
    } else if (shape === 'waterfall') {
      const isTotal = (l) => /^(sub)?(total|net|sum|end|ending|final|result|balance)\b/i.test(l);
      let run = 0;
      const notes = [];
      const bars = viz.labels.map((l, i) => {
        const v = viz.series[0].values[i];
        if (v === null) { notes.push('missing'); return null; }
        if (isTotal(l)) { notes.push(`total ${fmt(run)}`); return [0, run]; }
        const start = run;
        run += v;
        notes.push(`${v >= 0 ? '+' : ''}${fmt(v)} → ${fmt(run)}`);
        return [start, run];
      });
      const colorAt = (i) => (isTotal(viz.labels[i]) ? colors[1] : viz.series[0].values[i] >= 0 ? colors[2] : bad);
      config = {
        type: 'bar',
        data: { labels: viz.labels, datasets: [{ label: viz.series[0].name, data: bars, backgroundColor: viz.labels.map((_, i) => alpha(colorAt(i), 0.9)), borderRadius: 3, borderSkipped: false }] },
        options: { scales, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${notes[c.dataIndex]}` } } } },
      };
    } else if (shape === 'funnel') {
      const vals = viz.series[0].values;
      const max = Math.max(...vals.map((v) => v || 0)) || 1;
      const first = vals.find((v) => v) || 1;
      config = {
        type: 'bar',
        data: { labels: viz.labels, datasets: [{ label: viz.series[0].name, data: vals.map((v) => (v === null ? null : [-v / 2, v / 2])), backgroundColor: vals.map((_, i) => alpha(colors[i % colors.length], 0.9)), categoryPercentage: 1, barPercentage: 0.92, borderSkipped: false }] },
        options: {
          indexAxis: 'y',
          scales: { x: { display: false, min: -max * 0.55, max: max * 0.55 }, y: { ticks: { color: text }, grid: { display: false } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${fmt(vals[c.dataIndex])} (${Math.round(((vals[c.dataIndex] || 0) / first) * 100)}% of the first stage)` } } },
        },
      };
      plugins.push({
        id: 'funnelText',
        afterDatasetsDraw(chart) {
          const meta = chart.getDatasetMeta(0);
          const { ctx } = chart;
          ctx.save();
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.font = '600 12px sans-serif';
          ctx.fillStyle = '#fff';
          vals.forEach((v, i) => {
            const bar = meta.data[i];
            if (bar && v !== null) ctx.fillText(fmt(v), (bar.x + bar.base) / 2, bar.y);
          });
          ctx.restore();
        },
      });
    } else {
      const isLine = shape === 'line' || shape === 'area' || shape === 'step' || shape === 'dot';
      const stacked = shape === 'stacked' || shape === 'stacked100';
      const pct = shape === 'stacked100';
      const totals = viz.labels.map((_, i) => viz.series.reduce((a, s) => a + (s.values[i] || 0), 0));
      if (stacked) { scales.x.stacked = true; scales.y.stacked = true; }
      if (pct) { scales.y.max = 100; scales.y.ticks.callback = (v) => v + '%'; }
      const slope = viz.type === 'slope_chart';
      config = {
        type: isLine ? 'line' : 'bar',
        data: {
          labels: viz.labels,
          datasets: viz.series.map((s, i) => {
            const c = colors[i % colors.length];
            return {
              label: s.name, borderColor: c, spanGaps: false,
              data: pct ? s.values.map((v, k) => (v === null ? null : totals[k] ? (v / totals[k]) * 100 : 0)) : s.values,
              backgroundColor: isLine ? alpha(c, shape === 'area' ? 0.25 : 0.9) : alpha(c, 0.9),
              fill: shape === 'area', tension: shape === 'line' || shape === 'area' ? 0.25 : 0,
              stepped: shape === 'step',
              showLine: shape !== 'dot',
              pointRadius: shape === 'dot' ? 7 : slope ? 6 : isLine ? 3 : 0,
              ...(shape === 'histogram' ? { categoryPercentage: 1, barPercentage: 1, borderWidth: 1 } : isLine ? { borderWidth: 2 } : { borderRadius: 4 }),
            };
          }),
        },
        options: {
          indexAxis: shape === 'hbar' ? 'y' : 'x',
          scales: shape === 'hbar' ? { x: { ...scales.y, title: scales.y.title }, y: { ...scales.x, ticks: { color: text } } } : scales,
          plugins: {
            legend: legend(multi),
            ...(pct ? { tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${fmt(viz.series[c.datasetIndex].values[c.dataIndex])} (${Math.round(c.parsed.y)}%)` } } } : {}),
          },
        },
      };
    }
    config.options = {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      layout: { padding: shape === 'bubble' ? 28 : shape === 'scatter' || shape === 'dot' ? 10 : 0 },
      ...config.options,
      plugins: { tooltip: { callbacks: { label: (c) => ` ${c.dataset.label ? c.dataset.label + ': ' : ''}${fmt(pointValue(c))}` } }, ...config.options.plugins },
    };
    config.plugins = plugins;
    const canvas = block.querySelector('canvas');
    block._chart = new Chart(canvas, config);
    block._resize = () => block._chart && block._chart.resize();
    block._png = () => block._chart.toBase64Image('image/png', 1);
  }

  /* ---------- hand-drawn canvas charts: heatmap, treemap, sankey ---------- */
  const rgbOf = (c) => {
    const x = document.createElement('canvas').getContext('2d');
    x.fillStyle = '#000';
    x.fillStyle = c;
    const m = /^#([0-9a-f]{6})$/i.exec(x.fillStyle);
    const n = m ? parseInt(m[1], 16) : 0xffffff;
    return [n >> 16, (n >> 8) & 255, n & 255];
  };
  const mix = (a, b, t) => `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(',')})`;
  const luminance = ([r, g, b]) => (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const fitText = (ctx, s, maxW) => {
    if (ctx.measureText(s).width <= maxW) return s;
    let t = s;
    while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
    return t + '…';
  };

  function paintHeatmap(ctx, w, h, viz, T, hits) {
    const rows = viz.series;
    const cols = viz.labels;
    const all = rows.flatMap((r) => r.values).filter((v) => v !== null);
    const lo = viz.corr ? -1 : Math.min(...all);
    const hi = viz.corr ? 1 : Math.max(...all);
    ctx.font = '12px sans-serif';
    const left = Math.min(150, Math.max(...rows.map((r) => ctx.measureText(r.name).width)) + 14);
    const top = 30;
    const bottom = 34;
    const cw = (w - left - 10) / cols.length;
    const ch = (h - top - bottom) / rows.length;
    const neutral = rgbOf(T.panel);
    const pos = rgbOf(T.colors[0]);
    const neg = rgbOf(T.colors[1]);
    const colorFor = (v) => {
      if (viz.corr) return v >= 0 ? mix(neutral, pos, Math.min(1, v)) : mix(neutral, neg, Math.min(1, -v));
      const t = hi === lo ? 1 : (v - lo) / (hi - lo);
      return mix(neutral, pos, 0.12 + 0.88 * t);
    };
    ctx.textBaseline = 'middle';
    ctx.fillStyle = T.text;
    ctx.textAlign = 'right';
    rows.forEach((r, i) => ctx.fillText(fitText(ctx, r.name, left - 10), left - 6, top + ch * (i + 0.5)));
    ctx.textAlign = 'center';
    cols.forEach((c, j) => ctx.fillText(fitText(ctx, c, cw - 4), left + cw * (j + 0.5), top - 14));
    rows.forEach((r, i) => r.values.forEach((v, j) => {
      const x = left + cw * j;
      const y = top + ch * i;
      if (v === null) { ctx.strokeStyle = T.grid; ctx.strokeRect(x + 1, y + 1, cw - 2, ch - 2); return; }
      const fill = colorFor(v);
      ctx.fillStyle = fill;
      ctx.fillRect(x + 1, y + 1, cw - 2, ch - 2);
      if (cw > 38 && ch > 18) {
        const m = /rgb\((\d+),(\d+),(\d+)\)/.exec(fill);
        ctx.fillStyle = luminance([+m[1], +m[2], +m[3]]) > 0.58 ? '#222' : '#fff';
        ctx.fillText(fmt(Math.round(v * 100) / 100), x + cw / 2, y + ch / 2);
      }
      hits.push({ x: x + 1, y: y + 1, w: cw - 2, h: ch - 2, text: `${r.name} × ${cols[j]}: ${fmt(v)}` });
    }));
    // colour legend
    const lx = left;
    const lw = Math.min(220, w - left - 10);
    const ly = h - 20;
    for (let k = 0; k < lw; k++) {
      ctx.fillStyle = colorFor(lo + ((hi - lo) * k) / lw);
      ctx.fillRect(lx + k, ly, 1.5, 8);
    }
    ctx.fillStyle = T.text;
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(fmt(Math.round(lo * 100) / 100), lx - 6, ly + 4);
    ctx.textAlign = 'left';
    ctx.fillText(fmt(Math.round(hi * 100) / 100), lx + lw + 8, ly + 4);
  }

  function squarify(items, W, H) {
    const total = items.reduce((a, b) => a + b.v, 0);
    const scale = (W * H) / total;
    const list = items.map((o) => ({ ...o, a: o.v * scale }));
    const rects = [];
    let x = 0, y = 0, w = W, h = H;
    let row = [];
    const sum = (r) => r.reduce((a, b) => a + b.a, 0);
    const worst = (r, side) => {
      const s = sum(r);
      return Math.max((side * side * Math.max(...r.map((q) => q.a))) / (s * s), (s * s) / (side * side * Math.min(...r.map((q) => q.a))));
    };
    const place = (r) => {
      const s = sum(r);
      if (w >= h) {
        const cw = s / h;
        let yy = y;
        r.forEach((q) => { const rh = q.a / cw; rects.push({ ...q, x, y: yy, w: cw, h: rh }); yy += rh; });
        x += cw; w -= cw;
      } else {
        const rh = s / w;
        let xx = x;
        r.forEach((q) => { const rw = q.a / rh; rects.push({ ...q, x: xx, y, w: rw, h: rh }); xx += rw; });
        y += rh; h -= rh;
      }
    };
    let i = 0;
    while (i < list.length) {
      const side = Math.min(w, h);
      const cand = [...row, list[i]];
      if (!row.length || worst(cand, side) <= worst(row, side)) { row = cand; i++; } else { place(row); row = []; }
    }
    if (row.length) place(row);
    return rects;
  }

  function paintTreemap(ctx, w, h, viz, T, hits) {
    const total = viz.series[0].values.reduce((a, v) => a + (v > 0 ? v : 0), 0);
    const items = viz.labels.map((l, i) => ({ l, v: viz.series[0].values[i], i })).filter((o) => o.v > 0).sort((a, b) => b.v - a.v);
    const rects = squarify(items, w - 4, h - 4);
    rects.forEach((r) => {
      const x = r.x + 2, y = r.y + 2;
      const c = T.colors[r.i % T.colors.length];
      ctx.fillStyle = c;
      ctx.fillRect(x + 1, y + 1, r.w - 2, r.h - 2);
      hits.push({ x: x + 1, y: y + 1, w: r.w - 2, h: r.h - 2, text: `${r.l}: ${fmt(r.v)} (${Math.round((r.v / total) * 1000) / 10}%)` });
      if (r.w > 46 && r.h > 30) {
        ctx.fillStyle = luminance(rgbOf(c)) > 0.6 ? '#222' : '#fff';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.font = '600 13px sans-serif';
        ctx.fillText(fitText(ctx, r.l, r.w - 14), x + 7, y + 7);
        ctx.font = '12px sans-serif';
        ctx.fillText(fitText(ctx, `${fmt(r.v)} · ${Math.round((r.v / total) * 100)}%`, r.w - 14), x + 7, y + 25);
      }
    });
  }

  function paintSankey(ctx, w, h, viz, T, hits, dpr) {
    const nodes = viz.nodes.map((n) => ({ ...n, in: 0, out: 0 }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    viz.links.forEach((l) => { byId.get(l.source).out += l.value; byId.get(l.target).in += l.value; });
    nodes.forEach((n) => { n.flow = Math.max(n.in, n.out); });
    const cols = Math.max(...nodes.map((n) => n.depth)) + 1;
    const nodeW = 14;
    const padL = 8;
    const padR = 8;
    const labelRoom = Math.min(140, (w - padL - padR) / (cols + 1));
    const x0 = padL + (cols > 1 ? 0 : 0);
    const usableW = w - padL - padR - labelRoom;
    const colX = (d) => x0 + (cols === 1 ? 0 : (usableW - nodeW) * (d / (cols - 1)));
    const gap = 12;
    const top = 8;
    const availH = h - top - 8;
    let scale = Infinity;
    for (let d = 0; d < cols; d++) {
      const col = nodes.filter((n) => n.depth === d);
      const tot = col.reduce((a, n) => a + n.flow, 0);
      scale = Math.min(scale, (availH - gap * (col.length - 1)) / tot);
    }
    for (let d = 0; d < cols; d++) {
      const col = nodes.filter((n) => n.depth === d).sort((a, b) => b.flow - a.flow);
      const used = col.reduce((a, n) => a + n.flow * scale, 0) + gap * (col.length - 1);
      let y = top + (availH - used) / 2;
      col.forEach((n) => { n.x = colX(d); n.y = y; n.h = Math.max(2, n.flow * scale); y += n.h + gap; n.so = 0; n.to = 0; });
    }
    const links = viz.links.map((l) => ({ ...l, s: byId.get(l.source), t: byId.get(l.target) }));
    // order link ends by the other end's position to reduce crossings
    const outs = new Map(nodes.map((n) => [n.id, links.filter((l) => l.s === n).sort((a, b) => a.t.y - b.t.y)]));
    const ins = new Map(nodes.map((n) => [n.id, links.filter((l) => l.t === n).sort((a, b) => a.s.y - b.s.y)]));
    outs.forEach((arr) => arr.forEach((l) => { l.sy = l.s.y + l.s.so; l.s.so += l.value * scale; l.lh = l.value * scale; }));
    ins.forEach((arr) => arr.forEach((l) => { l.ty = l.t.y + l.t.to; l.t.to += l.value * scale; }));
    const colorOf = (n) => T.colors[nodes.indexOf(n) % T.colors.length];
    const paths = [];
    links.forEach((l) => {
      const xs = l.s.x + nodeW;
      const xt = l.t.x;
      const mid = (xs + xt) / 2;
      const p = new Path2D();
      p.moveTo(xs, l.sy);
      p.bezierCurveTo(mid, l.sy, mid, l.ty, xt, l.ty);
      p.lineTo(xt, l.ty + l.lh);
      p.bezierCurveTo(mid, l.ty + l.lh, mid, l.sy + l.lh, xs, l.sy + l.lh);
      p.closePath();
      ctx.fillStyle = mix(rgbOf(T.panel), rgbOf(colorOf(l.s)), 0.38);
      ctx.fill(p);
      paths.push({ path: p, text: `${l.source} → ${l.target}: ${fmt(l.value)}` });
    });
    ctx.font = '12px sans-serif';
    ctx.textBaseline = 'middle';
    nodes.forEach((n) => {
      ctx.fillStyle = colorOf(n);
      ctx.fillRect(n.x, n.y, nodeW, n.h);
      hits.push({ x: n.x, y: n.y, w: nodeW, h: n.h, text: `${n.id}: ${fmt(n.flow)}` });
      ctx.fillStyle = T.fg;
      const lastCol = n.depth === cols - 1 && cols > 1;
      ctx.textAlign = lastCol ? 'left' : 'left';
      const label = fitText(ctx, `${n.id} (${fmt(n.flow)})`, labelRoom + (lastCol ? 0 : 60));
      ctx.fillText(label, n.x + nodeW + 6, n.y + n.h / 2);
    });
    hits.paths = paths;
    hits.dpr = dpr;
  }

  const PAINTERS = { heatmap: paintHeatmap, treemap: paintTreemap, sankey: paintSankey };

  function buildCanvas(block, viz) {
    const canvas = block.querySelector('canvas');
    const body = block.querySelector('.viz-body');
    canvas.style.cssText = 'width:100%;height:100%;display:block';
    let hits = [];
    let hover = null;
    let ctx = canvas.getContext('2d');
    const draw = () => {
      const w = body.clientWidth - 20;
      const h = body.clientHeight - 20;
      if (w < 40 || h < 40) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const T = theme();
      ctx.fillStyle = T.panel;
      ctx.fillRect(0, 0, w, h);
      hits = [];
      ctx.font = '12px sans-serif';
      PAINTERS[viz.shape](ctx, w, h, viz, T, hits, dpr);
    };
    const tip = (text, x, y) => {
      if (!hover) { hover = document.createElement('div'); hover.className = 'viz-hover'; body.appendChild(hover); }
      hover.textContent = text;
      hover.style.display = text ? 'block' : 'none';
      hover.style.left = Math.min(x + 14, body.clientWidth - 150) + 'px';
      hover.style.top = y + 14 + 'px';
    };
    canvas.addEventListener('mousemove', (e) => {
      const r = canvas.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      let hit = hits.find((q) => mx >= q.x && mx <= q.x + q.w && my >= q.y && my <= q.y + q.h);
      if (!hit && hits.paths) {
        const dpr = hits.dpr || 1;
        hit = hits.paths.find((q) => ctx.isPointInPath(q.path, mx * dpr, my * dpr));
      }
      tip(hit ? hit.text : '', e.clientX - r.left + 10, e.clientY - r.top);
    });
    canvas.addEventListener('mouseleave', () => tip('', 0, 0));
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => draw()) : null;
    if (ro) ro.observe(body);
    block._ro = ro;
    draw();
    block._resize = draw;
    block._png = () => canvas.toDataURL('image/png');
  }

  /* ---------- graphs ---------- */
  function buildGraph(block, viz) {
    if (!window.cytoscape) throw new Error('Cytoscape failed to load (offline?).');
    const colors = palette();
    const text = css('--text', '#222');
    const muted = css('--muted', '#888');
    const panel = css('--panel', '#fff');
    const groups = [...new Set(viz.nodes.map((n) => n.group).filter(Boolean))];
    const colorOf = (n) => (n.group ? colors[(groups.indexOf(n.group) + 1) % colors.length] : colors[0]);
    const label = (n) => (viz.type === 'entity_relationship' && n.attributes.length ? `${n.label}\n─────\n${n.attributes.slice(0, 8).join('\n')}` : n.label);
    const elements = [
      ...viz.nodes.map((n) => ({ data: { id: n.id, label: label(n), color: colorOf(n), name: n.label, group: n.group, meta: n.meta, attributes: n.attributes } })),
      ...viz.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, label: e.label, meta: e.meta } })),
    ];
    const er = viz.type === 'entity_relationship';
    const cy = cytoscape({
      container: block.querySelector('.viz-graph'),
      elements,
      userZoomingEnabled: false, // wheel-zoom only when expanded, so scrolling the chat still works
      userPanningEnabled: true,
      boxSelectionEnabled: false,
      minZoom: 0.2, maxZoom: 4,
      layout: viz.tree
        ? { name: 'breadthfirst', directed: true, spacingFactor: 1.15, padding: 20 }
        : { name: 'cose', animate: false, padding: 24, nodeRepulsion: 9000, idealEdgeLength: 90 },
      style: [
        { selector: 'node', style: { label: 'data(label)', 'background-color': 'data(color)', color: text, 'font-size': 11, 'text-valign': er ? 'center' : 'bottom', 'text-margin-y': er ? 0 : 5, 'text-wrap': 'wrap', 'text-max-width': er ? 140 : 110, shape: er ? 'round-rectangle' : 'ellipse', width: er ? 'label' : 26, height: er ? 'label' : 26, padding: er ? 10 : 0, 'text-background-color': panel, 'text-background-opacity': er ? 0 : 0.7, 'text-background-padding': 2, ...(er ? { color: '#fff' } : {}) } },
        { selector: 'edge', style: { width: 1.6, 'line-color': muted, 'curve-style': 'bezier', label: 'data(label)', 'font-size': 9.5, color: muted, 'text-rotation': 'autorotate', 'text-background-color': panel, 'text-background-opacity': 0.85, 'text-background-padding': 2, ...(viz.directed ? { 'target-arrow-shape': 'triangle', 'target-arrow-color': muted } : {}) } },
        { selector: ':selected', style: { 'border-width': 3, 'border-color': css('--accent', '#c15f3c'), 'line-color': css('--accent', '#c15f3c'), 'target-arrow-color': css('--accent', '#c15f3c') } },
      ],
    });
    const info = block.querySelector('.viz-info');
    const showInfo = (html) => { info.innerHTML = html; info.classList.toggle('hidden', !html); };
    cy.on('tap', 'node', (e) => {
      const n = e.target;
      const d = n.data();
      const out = n.outgoers('edge').map((x) => `${esc(x.data('label') || '→')} → <b>${esc(cy.getElementById(x.data('target')).data('name'))}</b>`);
      const inc = n.incomers('edge').map((x) => `<b>${esc(cy.getElementById(x.data('source')).data('name'))}</b> → ${esc(x.data('label') || '')}`);
      showInfo(
        `<b>${esc(d.name)}</b>${d.group ? ` <span class="muted">· ${esc(d.group)}</span>` : ''}` +
          (d.attributes && d.attributes.length ? `<div class="muted small">${d.attributes.map(esc).join(' · ')}</div>` : '') +
          (d.meta ? `<div class="muted small">${esc(JSON.stringify(d.meta))}</div>` : '') +
          (out.length ? `<div class="small">Out: ${out.join('; ')}</div>` : '') + (inc.length ? `<div class="small">In: ${inc.join('; ')}</div>` : '')
      );
    });
    cy.on('tap', 'edge', (e) => {
      const d = e.target.data();
      showInfo(`<b>${esc(cy.getElementById(d.source).data('name'))}</b> ${viz.directed ? '→' : '—'} <b>${esc(cy.getElementById(d.target).data('name'))}</b>${d.label ? ` <span class="muted">· ${esc(d.label)}</span>` : ''}`);
    });
    cy.on('tap', (e) => { if (e.target === cy) showInfo(''); });
    block._cy = cy;
    block._resize = () => { cy.resize(); cy.fit(undefined, 24); };
    block._png = () => cy.png({ full: true, scale: 2, bg: panel });
  }

  /* ---------- lifecycle ---------- */
  function destroy(block) {
    if (block._chart) { block._chart.destroy(); block._chart = null; }
    if (block._ro) { block._ro.disconnect(); block._ro = null; }
    if (block._cy) { block._cy.destroy(); block._cy = null; }
  }

  function fail(block, message, src) {
    destroy(block);
    block.classList.add('failed');
    block.innerHTML =
      `<div class="viz-error"><b>Couldn’t render this visualization.</b> ${esc(message)}</div>` +
      `<div class="viz-error-actions"><button class="btn" data-viz="fix">Ask the model to fix it</button></div>` +
      `<details><summary>Show the raw spec</summary><pre class="viz-raw">${esc(src)}</pre></details>`;
    block._failMessage = message;
  }

  function renderBlock(block, force) {
    if (block.dataset.done && !force) return;
    block.dataset.done = '1';
    if (block._src === undefined) {
      const code = block.querySelector('code');
      block._src = code ? code.textContent : '';
    }
    const src = block._src;
    destroy(block);
    block.classList.remove('failed');
    const parsed = VizSchema.parse(src);
    if (!parsed.ok) return fail(block, parsed.error, src);
    const result = VizSchema.validate(parsed.spec);
    if (!result.ok) return fail(block, result.errors.join(' '), src);
    block._viz = result.viz;
    shell(block, result.viz, result.warnings);
    try {
      if (result.viz.kind === 'chart') buildChart(block, result.viz);
      else buildGraph(block, result.viz);
    } catch (err) {
      fail(block, err.message || String(err), src);
    }
  }

  /** Draw a spec the app already holds (from the create_chart tool) into an empty .viz-block. */
  function renderSpec(block, spec) {
    block._src = JSON.stringify(spec);
    renderBlock(block, true);
  }

  /* ---------- dashboards: one fixed pattern, whatever model wrote the data ---------- */
  const LOWER_IS_BETTER = /churn|expens|cost|defect|downtime|attrition|absentee|abandon|spend|debt|loss|refund|return rate|bounce/i;

  function kpiCardHTML(k) {
    let delta = '';
    if (typeof k.change === 'number' && isFinite(k.change)) {
      const up = k.change > 0;
      const good = k.change === 0 ? null : LOWER_IS_BETTER.test(k.label) ? !up : up;
      const cls = good === null ? 'flat' : good ? 'good' : 'bad';
      delta = `<span class="kpi-delta ${cls}">${k.change === 0 ? '■' : up ? '▲' : '▼'} ${Math.abs(k.change).toLocaleString(undefined, { maximumFractionDigits: 1 })}%</span>`;
    }
    return `<div class="kpi"><div class="kpi-label">${esc(k.label)}</div><div class="kpi-value">${esc(k.value)}</div>${delta}</div>`;
  }

  function renderDashboard(root, spec) {
    root.classList.add('dash');
    root._dash = spec;
    const tableHTML = spec.table
      ? `<section class="dash-table"><div class="dash-sec-title">${esc(spec.table.title)}</div><div class="viz-table-wrap"><table><thead><tr>${spec.table.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${spec.table.rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`
      : '';
    root.innerHTML =
      `<header class="dash-head"><div class="dash-titles"><div class="dash-title">${esc(spec.title)}</div>` +
      `<div class="dash-sub">${esc(spec.template_name || 'Dashboard')}${spec.period ? ' · ' + esc(spec.period) : ''}</div></div>` +
      `<div class="viz-tools"><button data-dash="png" title="Download the whole dashboard as PNG">PNG</button><button data-dash="copy-image" title="Copy the whole dashboard as an image">Copy</button><button data-dash="csv" title="Download data as CSV">CSV</button><button data-dash="json" title="Download as JSON">JSON</button><button data-dash="panel" title="Open in side panel">▤</button><button data-dash="expand" title="Full screen">⤢</button></div></header>` +
      `<section class="dash-kpis kpis-${Math.min(spec.kpis.length, 6)}">${spec.kpis.map(kpiCardHTML).join('')}</section>` +
      `<section class="dash-grid">${spec.charts.map((c, i) => `<div class="dash-cell ${c.wide ? 'wide' : 'half'}"><div class="viz-block" data-ci="${i}"></div></div>`).join('')}</section>` +
      tableHTML;
    root.querySelectorAll('.viz-block').forEach((b) => renderSpec(b, spec.charts[Number(b.dataset.ci)].spec));
  }

  function renderAll(root, force) {
    const blocks = root.querySelectorAll('.viz-block');
    blocks.forEach((b) => renderBlock(b, !!force && !!b._src));
  }

  function download(url, name) {
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /** Toolbar clicks (delegated from the chat thread). Returns true if the click was handled. */
  function handleClick(e) {
    if (Exporter.flowchartClick(e)) return true;
    const dbtn = e.target.closest('[data-dash]');
    if (dbtn) {
      const dash = dbtn.closest('.dash');
      if (['png', 'copy-image', 'csv', 'json'].includes(dbtn.dataset.dash)) {
        Exporter.dashboardAction(dash, dbtn.dataset.dash);
        return true;
      }
      if (dbtn.dataset.dash === 'panel') {
        document.dispatchEvent(new CustomEvent('open-dash-panel', { detail: dash._dash }));
        return true;
      }
      const on = dash.classList.toggle('dash-full');
      document.body.classList.toggle('viz-open', on);
      dbtn.textContent = on ? '✕' : '⤢';
      setTimeout(() => window.dispatchEvent(new Event('resize')), 60);
      return true;
    }
    const btn = e.target.closest('[data-viz]');
    if (!btn) return false;
    const block = btn.closest('.viz-block');
    if (!block) return false;
    const act = btn.dataset.viz;
    if (act === 'data') {
      const box = block.querySelector('.viz-data');
      if (box.classList.contains('hidden') && !box.innerHTML) box.innerHTML = dataTableHTML(block._viz);
      box.classList.toggle('hidden');
    } else if (act === 'png' || act === 'copy-image') {
      Exporter.chartAction(block, act);
    } else if (act === 'expand') {
      const on = block.classList.toggle('viz-full');
      document.body.classList.toggle('viz-open', on);
      btn.textContent = on ? '✕' : '⤢';
      if (block._cy) block._cy.userZoomingEnabled(on);
      setTimeout(() => block._resize && block._resize(), 60);
    } else if (act === 'zoom-in' && block._cy) block._cy.zoom({ level: block._cy.zoom() * 1.25, renderedPosition: { x: block._cy.width() / 2, y: block._cy.height() / 2 } });
    else if (act === 'zoom-out' && block._cy) block._cy.zoom({ level: block._cy.zoom() / 1.25, renderedPosition: { x: block._cy.width() / 2, y: block._cy.height() / 2 } });
    else if (act === 'fit' && block._cy) block._cy.fit(undefined, 24);
    else if (act === 'fix') {
      // At most two repair attempts per message, so a stubborn model can't loop forever.
      const msg = block.closest('.msg');
      const tries = Number((msg && msg.dataset.vizFixes) || 0);
      if (tries >= 2) { document.dispatchEvent(new CustomEvent('viz-toast', { detail: 'Already tried twice — try rephrasing the request.' })); return true; }
      if (msg) msg.dataset.vizFixes = String(tries + 1);
      document.dispatchEvent(new CustomEvent('viz-fix', { detail: { text: `The visualization block you wrote could not be rendered: ${block._failMessage}. Reply with ONLY a corrected \`\`\`chart block that follows the schema.` } }));
    }
    return true;
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const open = document.querySelector('.viz-block.viz-full');
      if (open) { open.querySelector('[data-viz="expand"]').click(); }
    }
  });

  /** The tables behind a chart as [head, rows] (used by CSV export). */
  function dataTables(viz) {
    if (viz.kind === 'graph') return [[['Node', 'Label', 'Group'], viz.nodes.map((n) => [n.id, n.label, n.group])], [['From', 'To', 'Relationship'], viz.edges.map((e) => [e.source, e.target, e.label])]];
    if (viz.table) return [[viz.table.head, viz.table.rows]];
    if (viz.shape === 'scatter' || viz.shape === 'bubble') {
      const rows = [];
      viz.series.forEach((s) => s.points.forEach((p) => rows.push(viz.shape === 'bubble' ? [s.name, p.x, p.y, p.r] : [s.name, p.x, p.y])));
      return [[['Series', viz.xLabel || 'x', viz.yLabel || 'y', ...(viz.shape === 'bubble' ? ['size'] : [])], rows]];
    }
    return [[[viz.xLabel || 'Category', ...viz.series.map((s) => s.name)], viz.labels.map((l, i) => [l, ...viz.series.map((s) => s.values[i])])]];
  }

  return { renderAll, renderSpec, renderDashboard, handleClick, dataTables };
})();
