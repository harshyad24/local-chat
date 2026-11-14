/* Visualization spec parsing + validation. Pure functions (no DOM) so they can be unit-tested in Node.
 *
 * The model writes a small JSON spec inside a ```chart fence; this file turns it into a normalized,
 * validated structure that viz.js renders. Nothing here ever executes model-written code.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.VizSchema = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const MAX_POINTS = 2000;
  const MAX_NODES = 500;
  const MAX_EDGES = 2000;

  const CHART_TYPES = {
    bar_chart: 'bar', horizontal_bar_chart: 'hbar', stacked_bar_chart: 'stacked', stacked_100_bar_chart: 'stacked100',
    line_chart: 'line', step_chart: 'step', area_chart: 'area', slope_chart: 'line', pie_chart: 'pie', donut_chart: 'donut',
    scatter_plot: 'scatter', bubble_chart: 'bubble', dot_plot: 'dot', histogram: 'histogram', radar_chart: 'radar',
    polar_area_chart: 'polar', waterfall_chart: 'waterfall', funnel_chart: 'funnel', gauge_chart: 'gauge', box_plot: 'box',
    candlestick_chart: 'candle', heatmap: 'heatmap', correlation_heatmap: 'heatmap', treemap: 'treemap', sankey_diagram: 'sankey',
  };
  const SINGLE_SERIES = new Set(['pie', 'donut', 'polar', 'funnel', 'treemap', 'waterfall']);
  const NON_NEGATIVE = new Set(['pie', 'donut', 'polar', 'funnel', 'treemap']);
  // graph type -> directed by default
  const GRAPH_TYPES = {
    network_graph: false, undirected_graph: false, directed_graph: true, knowledge_graph: true,
    dependency_graph: true, tree_diagram: true, entity_relationship: false,
  };
  const ALIASES = {
    bar: 'bar_chart', column: 'bar_chart', column_chart: 'bar_chart', hbar: 'horizontal_bar_chart',
    horizontal_bar: 'horizontal_bar_chart', barh: 'horizontal_bar_chart', stacked: 'stacked_bar_chart',
    stacked_bar: 'stacked_bar_chart', line: 'line_chart', area: 'area_chart', pie: 'pie_chart',
    donut: 'donut_chart', doughnut: 'donut_chart', doughnut_chart: 'donut_chart', scatter: 'scatter_plot',
    scatter_chart: 'scatter_plot', hist: 'histogram', network: 'network_graph', graph: 'network_graph',
    directed: 'directed_graph', undirected: 'undirected_graph', knowledge: 'knowledge_graph',
    dependency: 'dependency_graph', tree: 'tree_diagram', hierarchy: 'tree_diagram', tree_chart: 'tree_diagram',
    er: 'entity_relationship', erd: 'entity_relationship', er_diagram: 'entity_relationship',
    entity_relationship_diagram: 'entity_relationship', stacked_100: 'stacked_100_bar_chart', percent_stacked: 'stacked_100_bar_chart',
    step: 'step_chart', slope: 'slope_chart', bubble: 'bubble_chart', dot: 'dot_plot', radar: 'radar_chart', spider: 'radar_chart',
    spider_chart: 'radar_chart', polar_area: 'polar_area_chart', waterfall: 'waterfall_chart', funnel: 'funnel_chart',
    gauge: 'gauge_chart', boxplot: 'box_plot', box: 'box_plot', box_and_whisker: 'box_plot', candlestick: 'candlestick_chart',
    heat_map: 'heatmap', sankey: 'sankey_diagram', correlation: 'correlation_heatmap', correlation_matrix: 'correlation_heatmap',
  };

  function normalizeType(raw) {
    const k = String(raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
    return ALIASES[k] || k;
  }

  /** Parse the fence body. Tolerates the usual model slips (code fences, smart quotes, trailing commas). */
  function parse(text) {
    let s = String(text == null ? '' : text).replace(/^﻿/, '').trim();
    s = s.replace(/^```(?:json|chart)?\s*/i, '').replace(/```\s*$/, '').trim();
    if (!s) return { ok: false, error: 'The visualization block is empty.' };
    const attempts = [
      s,
      s.replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/,\s*([}\]])/g, '$1'),
    ];
    let lastErr = '';
    for (const a of attempts) {
      try {
        const spec = JSON.parse(a);
        if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return { ok: false, error: 'The spec must be a JSON object.' };
        return { ok: true, spec };
      } catch (e) {
        lastErr = e.message;
      }
    }
    return { ok: false, error: 'Invalid JSON: ' + lastErr };
  }

  /** "1,200" / "45%" / 12 -> number; anything else -> NaN. null/undefined/"" -> null (a missing value). */
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v === 'number') return Number.isFinite(v) ? v : NaN;
    if (typeof v === 'string') {
      const t = v.trim().replace(/,/g, '').replace(/%$/, '');
      if (t !== '' && /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(t)) return Number(t);
    }
    return NaN;
  }

  function pointOf(item, i, labels) {
    if (item === null || item === undefined) return { x: labels && labels[i] != null ? labels[i] : i + 1, y: null };
    if (typeof item === 'number' || typeof item === 'string') return { x: labels && labels[i] != null ? labels[i] : i + 1, y: item };
    if (Array.isArray(item)) return { x: item[0], y: item[1] };
    if (typeof item === 'object') {
      const x = item.x ?? item.label ?? item.name ?? item.category ?? item.id;
      const y = item.y ?? item.value ?? item.count ?? item.val;
      return { x: x === undefined ? (labels && labels[i] != null ? labels[i] : i + 1) : x, y };
    }
    return { x: i + 1, y: null };
  }

  function seriesOf(spec) {
    if (Array.isArray(spec.series) && spec.series.length) {
      return spec.series.map((s, i) => ({
        name: String((s && (s.name ?? s.label)) ?? `Series ${i + 1}`),
        raw: Array.isArray(s) ? s : (s && (s.data ?? s.values)) || [],
      }));
    }
    if (Array.isArray(spec.datasets) && spec.datasets.length) {
      return spec.datasets.map((s, i) => ({ name: String(s.label ?? s.name ?? `Series ${i + 1}`), raw: s.data || s.values || [] }));
    }
    if (Array.isArray(spec.values)) return [{ name: String(spec.y_axis || spec.name || 'Value'), raw: spec.values }];
    if (Array.isArray(spec.data)) return [{ name: String(spec.y_axis || spec.name || 'Value'), raw: spec.data }];
    return [];
  }

  function histogramBins(values, binCount) {
    const n = values.length;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const k = Math.max(1, Math.min(50, binCount || Math.ceil(Math.log2(n)) + 1));
    if (min === max) return { labels: [String(min)], counts: [n] };
    const width = (max - min) / k;
    const counts = new Array(k).fill(0);
    for (const v of values) counts[Math.min(k - 1, Math.floor((v - min) / width))]++;
    const fmt = (x) => (Math.abs(x) >= 100 ? String(Math.round(x)) : String(Math.round(x * 100) / 100));
    const labels = counts.map((_, i) => `${fmt(min + i * width)}–${fmt(min + (i + 1) * width)}`);
    return { labels, counts };
  }

  function validateChart(spec, type) {
    const errors = [];
    const warnings = [];
    const kind = CHART_TYPES[type];
    if (kind === 'gauge') return validateGauge(spec, type);
    if (kind === 'box') return validateBox(spec, type);
    if (kind === 'candle') return validateCandle(spec, type);
    if (kind === 'sankey') return validateSankey(spec, type);
    let series = seriesOf(spec);
    if (!series.length) return { ok: false, errors: ['No data found. Provide "series" (or "labels" + "values").'], warnings };

    // shorthand: {"labels": [...], "values": [...]}
    const topLabels = Array.isArray(spec.labels) ? spec.labels : null;
    let dropped = 0;
    let total = 0;

    if (kind === 'histogram') {
      const values = [];
      for (const item of series[0].raw) {
        const v = num(typeof item === 'object' && item !== null && !Array.isArray(item) ? (item.value ?? item.y) : item);
        if (v === null || Number.isNaN(v)) { dropped++; continue; }
        values.push(v);
      }
      if (!values.length) return { ok: false, errors: ['Histogram needs a list of numeric values.'], warnings };
      if (values.length > MAX_POINTS) return { ok: false, errors: [`Too many values (max ${MAX_POINTS}).`], warnings };
      const { labels, counts } = histogramBins(values, Number(spec.bins) || 0);
      if (dropped) warnings.push(`${dropped} non-numeric value(s) were skipped.`);
      return {
        ok: true, errors, warnings,
        viz: { kind: 'chart', type, shape: 'histogram', title: spec.title || '', xLabel: spec.x_axis || '', yLabel: spec.y_axis || 'Count', labels, series: [{ name: 'Count', values: counts }], rawCount: values.length },
      };
    }

    if (kind === 'scatter' || kind === 'bubble') {
      const out = [];
      for (const s of series) {
        const pts = [];
        for (let i = 0; i < s.raw.length; i++) {
          const item = s.raw[i];
          const p = pointOf(item, i, null);
          const x = num(p.x);
          const y = num(p.y);
          total++;
          if (x === null || y === null || Number.isNaN(x) || Number.isNaN(y)) { dropped++; continue; }
          if (kind === 'bubble') {
            const r = num(Array.isArray(item) ? item[2] : item && (item.r ?? item.size ?? item.z));
            if (r === null || Number.isNaN(r) || r <= 0) { dropped++; continue; }
            pts.push({ x, y, r });
          } else pts.push({ x, y });
        }
        out.push({ name: s.name, points: pts });
      }
      if (total > MAX_POINTS) return { ok: false, errors: [`Too many points (max ${MAX_POINTS}).`], warnings };
      if (!out.some((s) => s.points.length)) return { ok: false, errors: [kind === 'bubble' ? 'A bubble chart needs numeric x, y and a positive size r.' : 'A scatter plot needs numeric x and y values.'], warnings };
      if (dropped) warnings.push(`${dropped} point(s) without numeric x/y${kind === 'bubble' ? '/size' : ''} were skipped.`);
      return { ok: true, errors, warnings, viz: { kind: 'chart', type, shape: kind, title: spec.title || '', xLabel: spec.x_axis || '', yLabel: spec.y_axis || '', series: out } };
    }

    // categorical charts: bar / hbar / stacked / line / area / pie / donut
    const labels = [];
    const seen = new Map();
    const parsed = series.map((s) => {
      const m = new Map();
      for (let i = 0; i < s.raw.length; i++) {
        const p = pointOf(s.raw[i], i, topLabels);
        total++;
        const label = String(p.x);
        const y = num(p.y);
        if (Number.isNaN(y)) { dropped++; continue; }
        if (!seen.has(label)) { seen.set(label, labels.length); labels.push(label); }
        m.set(label, y); // y may be null = a gap
      }
      return { name: s.name, m };
    });
    if (total > MAX_POINTS) return { ok: false, errors: [`Too many data points (max ${MAX_POINTS}).`], warnings };
    if (!labels.length) return { ok: false, errors: ['None of the data points had a usable numeric value.'], warnings };
    const values = parsed.map((s) => ({ name: s.name, values: labels.map((l) => (s.m.has(l) ? s.m.get(l) : null)) }));
    if (NON_NEGATIVE.has(kind) && values[0].values.some((v) => v !== null && v < 0)) {
      return { ok: false, errors: [`${type.replace(/_/g, ' ')} values must not be negative.`], warnings };
    }
    if (SINGLE_SERIES.has(kind) && values.length > 1) warnings.push('This chart shows one series; only the first is drawn.');
    if (kind === 'radar' && labels.length < 3) return { ok: false, errors: ['A radar chart needs at least 3 categories.'], warnings };
    if (kind === 'treemap' && !values[0].values.some((v) => v !== null && v > 0)) return { ok: false, errors: ['A treemap needs positive values.'], warnings };
    const missing = values.reduce((a, s) => a + s.values.filter((v) => v === null).length, 0);
    if (dropped) warnings.push(`${dropped} non-numeric value(s) were skipped.`);
    if (missing) warnings.push(`${missing} missing value(s) are shown as gaps.`);
    return { ok: true, errors, warnings, viz: { kind: 'chart', type, shape: kind, title: spec.title || '', xLabel: spec.x_axis || '', yLabel: spec.y_axis || '', labels, series: values, corr: type === 'correlation_heatmap' } };
  }

  /* ---------- special shapes ---------- */
  function validateGauge(spec, type) {
    const s = seriesOf(spec);
    const first = spec.value !== undefined ? spec.value : Array.isArray(spec.values) ? spec.values[0] : s.length && s[0].raw.length ? pointOf(s[0].raw[0], 0, null).y : undefined;
    const value = num(first);
    if (value === null || Number.isNaN(value)) return { ok: false, errors: ['A gauge needs one numeric value.'], warnings: [] };
    const min = spec.min === undefined ? 0 : num(spec.min);
    const max = spec.max === undefined ? 100 : num(spec.max);
    if (min === null || max === null || Number.isNaN(min) || Number.isNaN(max) || max <= min) return { ok: false, errors: ['A gauge needs numeric min < max.'], warnings: [] };
    const warnings = value < min || value > max ? ['The value is outside the min–max range and was clamped.'] : [];
    return { ok: true, errors: [], warnings, viz: { kind: 'chart', type, shape: 'gauge', title: spec.title || '', value, min, max, table: { head: ['Value', 'Min', 'Max'], rows: [[value, min, max]] } } };
  }

  /** Linear-interpolated quantile of a sorted array (same as numpy's default). */
  function quantile(sorted, q) {
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  function boxStats(values) {
    const v = values.slice().sort((a, b) => a - b);
    const q1 = quantile(v, 0.25);
    const median = quantile(v, 0.5);
    const q3 = quantile(v, 0.75);
    const iqr = q3 - q1;
    const loFence = q1 - 1.5 * iqr;
    const hiFence = q3 + 1.5 * iqr;
    const inside = v.filter((x) => x >= loFence && x <= hiFence);
    return { n: v.length, min: inside[0], q1, median, q3, max: inside[inside.length - 1], outliers: v.filter((x) => x < loFence || x > hiFence) };
  }

  function validateBox(spec, type) {
    const warnings = [];
    const groups = Array.isArray(spec.groups) ? spec.groups : seriesOf(spec).map((s) => ({ name: s.name, values: s.raw }));
    if (!groups.length) return { ok: false, errors: ['A box plot needs "groups": [{name, values:[numbers]}].'], warnings };
    const labels = [];
    const boxes = [];
    let total = 0;
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i] || {};
      const raw = Array.isArray(g) ? g : g.values || g.data || [];
      const vals = raw.map((x) => num(x)).filter((x) => x !== null && !Number.isNaN(x));
      total += vals.length;
      if (vals.length < 3) { warnings.push(`Group "${g.name || i + 1}" has fewer than 3 numbers and was skipped.`); continue; }
      labels.push(String(g.name ?? g.label ?? `Group ${i + 1}`));
      boxes.push(boxStats(vals));
    }
    if (total > MAX_POINTS) return { ok: false, errors: [`Too many values (max ${MAX_POINTS}).`], warnings };
    if (!boxes.length) return { ok: false, errors: ['Each box needs at least 3 numbers.'], warnings };
    const r = (x) => Math.round(x * 1000) / 1000;
    return {
      ok: true, errors: [], warnings,
      viz: {
        kind: 'chart', type, shape: 'box', title: spec.title || '', xLabel: spec.x_axis || '', yLabel: spec.y_axis || '', labels, boxes,
        table: { head: ['Group', 'n', 'Min*', 'Q1', 'Median', 'Q3', 'Max*', 'Outliers'], rows: boxes.map((b, i) => [labels[i], b.n, r(b.min), r(b.q1), r(b.median), r(b.q3), r(b.max), b.outliers.map(r).join(', ') || '—']) },
      },
    };
  }

  function validateCandle(spec, type) {
    const warnings = [];
    const rows = Array.isArray(spec.ohlc) ? spec.ohlc : Array.isArray(spec.data) ? spec.data : [];
    if (!rows.length) return { ok: false, errors: ['A candlestick chart needs "ohlc": [{label, open, high, low, close}].'], warnings };
    if (rows.length > MAX_POINTS) return { ok: false, errors: [`Too many candles (max ${MAX_POINTS}).`], warnings };
    const out = [];
    let dropped = 0;
    rows.forEach((r, i) => {
      const o = num(r && r.open), h = num(r && r.high), l = num(r && r.low), c = num(r && r.close);
      if ([o, h, l, c].some((x) => x === null || Number.isNaN(x)) || l > Math.min(o, c) || h < Math.max(o, c)) { dropped++; return; }
      out.push({ label: String((r.label ?? r.date ?? r.x) ?? i + 1), open: o, high: h, low: l, close: c });
    });
    if (!out.length) return { ok: false, errors: ['No valid candles: each needs numeric open/high/low/close with low ≤ open,close ≤ high.'], warnings };
    if (dropped) warnings.push(`${dropped} invalid candle(s) were skipped.`);
    return {
      ok: true, errors: [], warnings,
      viz: { kind: 'chart', type, shape: 'candle', title: spec.title || '', xLabel: spec.x_axis || '', yLabel: spec.y_axis || '', candles: out, table: { head: ['Label', 'Open', 'High', 'Low', 'Close'], rows: out.map((c) => [c.label, c.open, c.high, c.low, c.close]) } },
    };
  }

  function validateSankey(spec, type) {
    const warnings = [];
    const raw = spec.edges || spec.links || [];
    if (!Array.isArray(raw) || !raw.length) return { ok: false, errors: ['A sankey diagram needs "edges": [{source, target, value}].'], warnings };
    if (raw.length > MAX_EDGES) return { ok: false, errors: [`Too many flows (max ${MAX_EDGES}).`], warnings };
    const links = [];
    for (const e of raw) {
      const s = e && (e.source ?? e.from);
      const t = e && (e.target ?? e.to);
      const v = num(e && e.value);
      if (s === undefined || t === undefined || v === null || Number.isNaN(v) || v <= 0) { warnings.push('A flow without source, target and positive value was skipped.'); continue; }
      if (String(s) === String(t)) { warnings.push('A flow from a node to itself was skipped.'); continue; }
      links.push({ source: String(s), target: String(t), value: v });
    }
    if (!links.length) return { ok: false, errors: ['No valid flows (each needs source, target and a positive value).'], warnings };
    const ids = [];
    for (const l of links) for (const id of [l.source, l.target]) if (!ids.includes(id)) ids.push(id);
    // depth = longest path from a source; a cycle means the layout can't be drawn
    const depth = new Map(ids.map((id) => [id, 0]));
    for (let pass = 0; pass <= ids.length; pass++) {
      let changed = false;
      for (const l of links) {
        if (depth.get(l.target) < depth.get(l.source) + 1) { depth.set(l.target, depth.get(l.source) + 1); changed = true; }
      }
      if (!changed) break;
      if (pass === ids.length) return { ok: false, errors: ['The flows contain a loop; a sankey diagram must flow one way.'], warnings };
    }
    const nodes = ids.map((id) => ({ id, depth: depth.get(id) }));
    return {
      ok: true, errors: [], warnings,
      viz: { kind: 'chart', type, shape: 'sankey', title: spec.title || '', nodes, links, table: { head: ['From', 'To', 'Value'], rows: links.map((l) => [l.source, l.target, l.value]) } },
    };
  }

  function flattenTree(node, nodes, edges, parent, counter) {
    if (!node || typeof node !== 'object') return;
    const id = String(node.id ?? `n${counter.n++}`);
    nodes.push({ id, label: String(node.label ?? node.name ?? id), meta: node.metadata || null });
    if (parent !== null) edges.push({ source: parent, target: id, label: '' });
    for (const c of node.children || []) flattenTree(c, nodes, edges, id, counter);
  }

  function validateGraph(spec, type) {
    const errors = [];
    const warnings = [];
    let rawNodes = spec.nodes || spec.entities || [];
    let rawEdges = spec.edges || spec.links || spec.relationships || [];
    if (!rawNodes.length && spec.root) {
      const nodes = [];
      const edges = [];
      flattenTree(spec.root, nodes, edges, null, { n: 0 });
      rawNodes = nodes;
      rawEdges = edges;
    }
    if (!Array.isArray(rawNodes) || !rawNodes.length) return { ok: false, errors: ['A graph needs a non-empty "nodes" list.'], warnings };
    if (rawNodes.length > MAX_NODES) return { ok: false, errors: [`Too many nodes (max ${MAX_NODES}).`], warnings };
    if (!Array.isArray(rawEdges)) rawEdges = [];
    if (rawEdges.length > MAX_EDGES) return { ok: false, errors: [`Too many edges (max ${MAX_EDGES}).`], warnings };

    const nodes = [];
    const ids = new Set();
    for (const n of rawNodes) {
      const o = typeof n === 'string' ? { id: n } : n;
      if (!o || o.id === undefined || o.id === null || o.id === '') { errors.push('Every node needs an "id".'); continue; }
      const id = String(o.id);
      if (ids.has(id)) { errors.push(`Duplicate node id "${id}".`); continue; }
      ids.add(id);
      const attrs = Array.isArray(o.attributes) ? o.attributes.map(String) : [];
      nodes.push({ id, label: String(o.label ?? o.name ?? id), group: o.group != null ? String(o.group) : '', meta: o.metadata || o.meta || null, attributes: attrs });
    }
    if (errors.length) return { ok: false, errors, warnings };

    const edges = [];
    let created = 0;
    for (const e of rawEdges) {
      if (!e) continue;
      const s = e.source ?? e.from;
      const t = e.target ?? e.to;
      if (s === undefined || t === undefined) { warnings.push('An edge without source/target was skipped.'); continue; }
      for (const id of [String(s), String(t)]) {
        if (!ids.has(id)) { ids.add(id); nodes.push({ id, label: id, group: '', meta: null, attributes: [] }); created++; }
      }
      edges.push({ source: String(s), target: String(t), label: e.label != null ? String(e.label) : '', meta: e.metadata || null });
    }
    if (created) warnings.push(`${created} node(s) referenced by edges were created automatically.`);
    const directed = spec.directed !== undefined ? !!spec.directed : GRAPH_TYPES[type];
    return { ok: true, errors, warnings, viz: { kind: 'graph', type, directed, tree: type === 'tree_diagram', title: spec.title || '', nodes, edges } };
  }

  /** spec -> { ok, errors[], warnings[], viz } */
  function validate(spec) {
    if (!spec || typeof spec !== 'object') return { ok: false, errors: ['Not an object.'], warnings: [] };
    const type = normalizeType(spec.visualization_type || spec.type || spec.chart_type || spec.kind);
    if (!type) return { ok: false, errors: ['Missing "visualization_type".'], warnings: [] };
    if (type === 'flowchart' || type === 'flow_chart' || type === 'mermaid') {
      return { ok: false, errors: ['Flowcharts are written as a ```mermaid code block, not a chart spec.'], warnings: [] };
    }
    if (CHART_TYPES[type]) return validateChart(spec, type);
    if (type in GRAPH_TYPES) return validateGraph(spec, type);
    return { ok: false, errors: [`Unsupported visualization_type "${type}".`], warnings: [] };
  }

  return { parse, validate, normalizeType, num, boxStats, CHART_TYPES, GRAPH_TYPES, MAX_POINTS, MAX_NODES };
});
