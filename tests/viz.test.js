// Run:  node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const V = require('../frontend/js/viz-schema.js');

const ok = (spec) => {
  const r = V.validate(spec);
  assert.equal(r.ok, true, 'expected ok but got: ' + JSON.stringify(r.errors));
  return r;
};
const bad = (spec, re) => {
  const r = V.validate(spec);
  assert.equal(r.ok, false, 'expected failure');
  if (re) assert.match(r.errors.join(' '), re);
  return r;
};

test('bar chart from {x,y} points', () => {
  const r = ok({
    visualization_type: 'bar_chart', title: 'Monthly Revenue', x_axis: 'Month', y_axis: 'Revenue',
    series: [{ name: 'Revenue', data: [{ x: 'January', y: 12000 }, { x: 'February', y: 15000 }, { x: 'March', y: 18000 }] }],
  });
  assert.deepEqual(r.viz.labels, ['January', 'February', 'March']);
  assert.deepEqual(r.viz.series[0].values, [12000, 15000, 18000]);
  assert.equal(r.viz.xLabel, 'Month');
  assert.equal(r.viz.shape, 'bar');
});

test('shorthand {type, labels, values} is accepted', () => {
  const r = ok({ type: 'bar', labels: ['A', 'B', 'C'], values: [20, 40, 30] });
  assert.deepEqual(r.viz.labels, ['A', 'B', 'C']);
  assert.deepEqual(r.viz.series[0].values, [20, 40, 30]);
});

test('line chart, multi-series with different x sets aligns and leaves gaps', () => {
  const r = ok({
    visualization_type: 'line_chart',
    series: [
      { name: 'A', data: [{ x: '2024', y: 1 }, { x: '2025', y: 2 }] },
      { name: 'B', data: [{ x: '2025', y: 5 }, { x: '2026', y: 7 }] },
    ],
  });
  assert.deepEqual(r.viz.labels, ['2024', '2025', '2026']);
  assert.deepEqual(r.viz.series[0].values, [1, 2, null]);
  assert.deepEqual(r.viz.series[1].values, [null, 5, 7]);
  assert.match(r.warnings.join(' '), /missing value/);
});

test('missing values (null) become gaps, numeric strings are coerced, junk is dropped with a warning', () => {
  const r = ok({ visualization_type: 'bar_chart', series: [{ name: 's', data: [{ x: 'a', y: '1,200' }, { x: 'b', y: null }, { x: 'c', y: 'n/a' }, { x: 'd', y: '45%' }] }] });
  assert.deepEqual(r.viz.labels, ['a', 'b', 'd']);
  assert.deepEqual(r.viz.series[0].values, [1200, null, 45]);
  assert.match(r.warnings.join(' '), /skipped/);
});

test('scatter plot needs numeric x and y', () => {
  const r = ok({ visualization_type: 'scatter_plot', series: [{ name: 'p', data: [{ x: 1, y: 2 }, { x: 2, y: 4.5 }, { x: 'oops', y: 3 }] }] });
  assert.equal(r.viz.series[0].points.length, 2);
  assert.match(r.warnings.join(' '), /skipped/);
  bad({ visualization_type: 'scatter_plot', series: [{ name: 'p', data: [{ x: 'a', y: 'b' }] }] }, /numeric/);
});

test('histogram bins raw values', () => {
  const r = ok({ visualization_type: 'histogram', bins: 3, series: [{ name: 'v', data: [1, 2, 2, 3, 3, 3, 9] }] });
  assert.equal(r.viz.labels.length, 3);
  assert.equal(r.viz.series[0].values.reduce((a, b) => a + b, 0), 7);
  bad({ visualization_type: 'histogram', series: [{ name: 'v', data: ['x', 'y'] }] }, /numeric/);
});

test('pie rejects negative values, donut accepted', () => {
  bad({ visualization_type: 'pie_chart', series: [{ name: 's', data: [{ x: 'a', y: -1 }, { x: 'b', y: 3 }] }] }, /negative/);
  ok({ visualization_type: 'donut_chart', series: [{ name: 's', data: [{ x: 'a', y: 1 }, { x: 'b', y: 3 }] }] });
});

test('stacked bar and horizontal bar types', () => {
  assert.equal(ok({ visualization_type: 'stacked_bar_chart', series: [{ name: 'a', data: [{ x: 'q', y: 1 }] }, { name: 'b', data: [{ x: 'q', y: 2 }] }] }).viz.shape, 'stacked');
  assert.equal(ok({ visualization_type: 'horizontal_bar_chart', labels: ['x'], values: [1] }).viz.shape, 'hbar');
});

test('network graph', () => {
  const r = ok({
    visualization_type: 'network_graph',
    nodes: [{ id: 'A', label: 'Customer' }, { id: 'B', label: 'Order' }, { id: 'C', label: 'Product' }],
    edges: [{ source: 'A', target: 'B', label: 'places' }, { source: 'B', target: 'C', label: 'contains' }],
  });
  assert.equal(r.viz.nodes.length, 3);
  assert.equal(r.viz.edges.length, 2);
  assert.equal(r.viz.directed, false);
});

test('directed graph / knowledge graph are directed; explicit directed flag wins', () => {
  const g = { nodes: [{ id: 'a' }, { id: 'b' }], edges: [{ from: 'a', to: 'b', label: 'x' }] };
  assert.equal(ok({ visualization_type: 'directed_graph', ...g }).viz.directed, true);
  assert.equal(ok({ visualization_type: 'knowledge_graph', ...g }).viz.directed, true);
  assert.equal(ok({ visualization_type: 'network_graph', directed: true, ...g }).viz.directed, true);
});

test('hierarchical tree from a nested root', () => {
  const r = ok({ visualization_type: 'tree_diagram', root: { name: 'CEO', children: [{ name: 'CTO', children: [{ name: 'Dev' }] }, { name: 'CFO' }] } });
  assert.equal(r.viz.nodes.length, 4);
  assert.equal(r.viz.edges.length, 3);
  assert.equal(r.viz.tree, true);
});

test('entity relationship keeps attributes', () => {
  const r = ok({ visualization_type: 'entity_relationship', nodes: [{ id: 'user', label: 'User', attributes: ['id', 'email'] }, { id: 'order' }], edges: [{ source: 'user', target: 'order', label: '1..*' }] });
  assert.deepEqual(r.viz.nodes[0].attributes, ['id', 'email']);
});

test('edges that mention unknown nodes create them with a warning; duplicate ids are errors', () => {
  const r = ok({ visualization_type: 'network_graph', nodes: [{ id: 'a' }], edges: [{ source: 'a', target: 'ghost' }] });
  assert.equal(r.viz.nodes.length, 2);
  assert.match(r.warnings.join(' '), /created automatically/);
  bad({ visualization_type: 'network_graph', nodes: [{ id: 'a' }, { id: 'a' }] }, /Duplicate/);
});

test('flowcharts are redirected to mermaid, not drawn here', () => {
  bad({ visualization_type: 'flowchart', nodes: [] }, /mermaid/);
});

test('malformed JSON: repaired when trivial, reported when not', () => {
  assert.equal(V.parse('{"visualization_type":"bar_chart","labels":["a"],"values":[1,],}').ok, true); // trailing commas
  assert.equal(V.parse('```json\n{"type":"bar","labels":["a"],"values":[1]}\n```').ok, true); // stray fence
  assert.equal(V.parse('{“type”: “bar”}').ok, true); // smart quotes
  const r = V.parse('{"type": "bar", "labels": [');
  assert.equal(r.ok, false);
  assert.match(r.error, /Invalid JSON/);
  assert.equal(V.parse('').ok, false);
  assert.equal(V.parse('[1,2,3]').ok, false);
});

test('missing / invalid data and unknown types are rejected with a reason', () => {
  bad({ visualization_type: 'bar_chart' }, /No data/);
  bad({ visualization_type: 'bar_chart', series: [{ name: 'x', data: [{ x: 'a', y: 'nope' }] }] }, /usable numeric/);
  bad({ visualization_type: 'sunburst_chart', series: [] }, /Unsupported/);
  bad({}, /Missing/);
  bad({ visualization_type: 'network_graph', nodes: [] }, /non-empty/);
});

test('size limits protect the browser', () => {
  const many = Array.from({ length: V.MAX_POINTS + 1 }, (_, i) => ({ x: 'p' + i, y: i }));
  bad({ visualization_type: 'bar_chart', series: [{ name: 's', data: many }] }, /Too many/);
  const nodes = Array.from({ length: V.MAX_NODES + 1 }, (_, i) => ({ id: 'n' + i }));
  bad({ visualization_type: 'network_graph', nodes }, /Too many nodes/);
});

test('several specs validate independently (multiple charts in one conversation)', () => {
  const specs = [
    { visualization_type: 'bar_chart', labels: ['a'], values: [1] },
    { visualization_type: 'line_chart', labels: ['a', 'b'], values: [1, 2] },
    { visualization_type: 'network_graph', nodes: [{ id: 'x' }] },
  ];
  assert.deepEqual(specs.map((s) => V.validate(s).ok), [true, true, true]);
});

// ---------------------------------------------------------------- extended chart types
test('every categorical type the tool can send validates', () => {
  const base = { title: 't', labels: ['a', 'b', 'c'], values: [3, 1, 2] };
  for (const t of ['stacked_100_bar_chart', 'step_chart', 'dot_plot', 'radar_chart', 'polar_area_chart', 'waterfall_chart', 'funnel_chart', 'heatmap', 'correlation_heatmap', 'treemap', 'pie_chart', 'donut_chart', 'area_chart']) {
    const r = ok({ visualization_type: t, ...base });
    assert.equal(r.viz.kind, 'chart', t);
    assert.deepEqual(r.viz.labels, ['a', 'b', 'c'], t);
  }
});

test('slope chart is a line with the slope flag via type', () => {
  const r = ok({ visualization_type: 'slope_chart', labels: ['2020', '2024'], values: [4, 9] });
  assert.equal(r.viz.shape, 'line');
  assert.equal(r.viz.type, 'slope_chart');
});

test('radar needs 3+ categories; treemap/funnel reject negatives', () => {
  bad({ visualization_type: 'radar_chart', labels: ['a', 'b'], values: [1, 2] }, /3 categories/);
  bad({ visualization_type: 'funnel_chart', labels: ['a', 'b'], values: [5, -1] }, /negative/);
  bad({ visualization_type: 'treemap', labels: ['a', 'b'], values: [0, 0] }, /positive/);
});

test('bubble chart needs a positive size', () => {
  const r = ok({ visualization_type: 'bubble_chart', series: [{ name: 's', data: [{ x: 1, y: 2, r: 5 }, { x: 2, y: 3 }] }] });
  assert.equal(r.viz.series[0].points.length, 1);
  assert.match(r.warnings.join(' '), /skipped/);
  bad({ visualization_type: 'bubble_chart', series: [{ name: 's', data: [{ x: 1, y: 2 }] }] }, /size/);
});

test('gauge validates range and clamps with a warning', () => {
  const r = ok({ visualization_type: 'gauge_chart', value: 140, min: 0, max: 100 });
  assert.match(r.warnings.join(' '), /clamped/);
  bad({ visualization_type: 'gauge_chart', value: 5, min: 10, max: 1 }, /min < max/);
});

test('box plot computes quartiles, whiskers and outliers', () => {
  const r = ok({ visualization_type: 'box_plot', groups: [{ name: 'g', values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 100] }] });
  const b = r.viz.boxes[0];
  assert.equal(b.median, 5.5);
  assert.equal(b.q1, 3.25);
  assert.equal(b.q3, 7.75);
  assert.deepEqual(b.outliers, [100]);
  assert.equal(b.max, 9);
  bad({ visualization_type: 'box_plot', groups: [{ name: 'g', values: [1, 2] }] }, /at least 3/);
});

test('candlestick rejects impossible candles', () => {
  const r = ok({ visualization_type: 'candlestick_chart', ohlc: [{ label: 'a', open: 1, high: 3, low: 0, close: 2 }, { label: 'b', open: 5, high: 4, low: 3, close: 4 }] });
  assert.equal(r.viz.candles.length, 1);
  assert.match(r.warnings.join(' '), /invalid/);
  bad({ visualization_type: 'candlestick_chart', ohlc: [{ label: 'b', open: 5, high: 4, low: 3, close: 4 }] }, /No valid candles/);
});

test('sankey assigns depths and rejects loops', () => {
  const r = ok({ visualization_type: 'sankey_diagram', edges: [{ source: 'a', target: 'b', value: 5 }, { source: 'b', target: 'c', value: 3 }, { source: 'a', target: 'c', value: 2 }] });
  const d = Object.fromEntries(r.viz.nodes.map((n) => [n.id, n.depth]));
  assert.deepEqual(d, { a: 0, b: 1, c: 2 });
  bad({ visualization_type: 'sankey_diagram', edges: [{ source: 'a', target: 'b', value: 1 }, { source: 'b', target: 'a', value: 1 }] }, /loop/);
  bad({ visualization_type: 'sankey_diagram', edges: [{ source: 'a', target: 'b', value: 0 }] }, /No valid flows/);
});

test('aliases for the new types', () => {
  for (const [alias, canon] of [['radar', 'radar_chart'], ['Spider Chart', 'radar_chart'], ['waterfall', 'waterfall_chart'], ['sankey', 'sankey_diagram'], ['boxplot', 'box_plot'], ['heat map', 'heatmap']]) {
    assert.equal(V.normalizeType(alias), canon);
  }
});

test('exporter: csv escaping and file-name slug', () => {
  global.document = { addEventListener() {}, documentElement: {} };
  global.getComputedStyle = () => ({ getPropertyValue: () => '' });
  const E = require('../frontend/js/exporter.js');
  assert.strictEqual(E.csvRows([['a', 'b,c'], ['x"y', 1]]), 'a,"b,c"\r\n"x""y",1');
  assert.strictEqual(E.slug('My Chart!'), 'My_Chart');
});
