"""The built-in chart tool.

The model only supplies *data* in a small flat shape; this module checks it and turns it into the spec that
the browser (frontend/js/viz-schema.js + viz.js) validates and draws. The model never writes chart code.
"""
from __future__ import annotations

import math
from typing import Any

# canonical type -> short description shown to the model
TYPES: dict[str, str] = {
    "bar_chart": "compare categories",
    "horizontal_bar_chart": "compare categories with long names / rankings",
    "stacked_bar_chart": "totals split into parts (use series)",
    "stacked_100_bar_chart": "share of each part within each category (use series)",
    "line_chart": "trend over time (use series for several lines)",
    "step_chart": "values that change at distinct moments",
    "area_chart": "trend with magnitude",
    "slope_chart": "change between two points in time (exactly 2 labels)",
    "pie_chart": "parts of a whole",
    "donut_chart": "parts of a whole with a hole",
    "scatter_plot": "relationship of two numbers (series[].points = [{x,y}])",
    "bubble_chart": "three numbers per point (series[].points = [{x,y,r}])",
    "dot_plot": "individual values per category",
    "histogram": "distribution of raw numbers (put them in values)",
    "radar_chart": "compare several measures for one or more items (labels = measures)",
    "polar_area_chart": "category magnitudes around a circle",
    "waterfall_chart": "positive/negative changes building to a total (a label starting with 'Total' is drawn as a total)",
    "funnel_chart": "values shrinking through stages",
    "gauge_chart": "one value against a range (values=[v], min, max)",
    "box_plot": "spread, median and outliers per group (use groups)",
    "candlestick_chart": "open/high/low/close prices (use ohlc)",
    "heatmap": "grid of values: labels = columns, series = rows",
    "correlation_heatmap": "correlation matrix (-1..1): labels = variables, series = one row per variable",
    "treemap": "hierarchy-free proportions as nested rectangles (labels + values)",
    "sankey_diagram": "flow between stages (edges with value)",
    "network_graph": "who/what is connected to whom (nodes + edges)",
    "directed_graph": "connections with direction",
    "knowledge_graph": "entities and named relationships",
    "dependency_graph": "what depends on what",
    "tree_diagram": "parent-child hierarchy / org chart (edges = parent -> child)",
    "entity_relationship": "database tables / entities (nodes with attributes)",
}

ALIASES = {
    "bar": "bar_chart", "column": "bar_chart", "column_chart": "bar_chart", "line": "line_chart", "pie": "pie_chart",
    "donut": "donut_chart", "doughnut": "donut_chart", "scatter": "scatter_plot", "bubble": "bubble_chart",
    "radar": "radar_chart", "spider_chart": "radar_chart", "area": "area_chart", "stacked": "stacked_bar_chart",
    "stacked_bar": "stacked_bar_chart", "hbar": "horizontal_bar_chart", "horizontal_bar": "horizontal_bar_chart",
    "waterfall": "waterfall_chart", "funnel": "funnel_chart", "gauge": "gauge_chart", "boxplot": "box_plot",
    "box": "box_plot", "box_and_whisker": "box_plot", "candlestick": "candlestick_chart", "sankey": "sankey_diagram",
    "heat_map": "heatmap", "tree": "tree_diagram", "network": "network_graph", "graph": "network_graph",
    "directed": "directed_graph", "knowledge": "knowledge_graph", "dependency": "dependency_graph",
    "er": "entity_relationship", "erd": "entity_relationship", "polar_area": "polar_area_chart",
    "step": "step_chart", "slope": "slope_chart", "dot": "dot_plot", "hist": "histogram",
}

_GRAPHS = {"network_graph", "directed_graph", "knowledge_graph", "dependency_graph", "tree_diagram", "entity_relationship"}
MAX_POINTS = 2000


class ChartError(ValueError):
    """Bad chart arguments; the message goes back to the model so it can correct them."""


def spec_for_model() -> dict:
    """OpenAI/Ollama function spec."""
    desc = (
        "Draw a chart or graph from data; the app draws it (never write chart code or ASCII art). Use it when the user "
        "asks for a chart/graph/plot or 3+ numbers read better visually. Real data only. Afterwards write 1-3 sentences "
        "on what it shows; don't repeat the data. Data shapes: most types = labels + values (or series [{name, values}]); "
        "scatter/bubble = series [{name, points:[{x,y,r}]}]; histogram = values (raw numbers); gauge = values [v] + min,max; "
        "box_plot = groups [{name, values}]; candlestick = ohlc [{label,open,high,low,close}]; heatmap = labels (columns) + "
        "series (rows); sankey = edges [{source,target,value}]; graphs/trees = nodes + edges [{source,target,label}]. "
        "Flowcharts/sequence diagrams: use a ```mermaid block instead."
    )
    num_list = {"type": "array", "items": {"type": "number"}}
    return {
        "type": "function",
        "function": {
            "name": "create_chart",
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(TYPES), "description": "Chart type"},
                    "title": {"type": "string", "description": "Short title"},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "Category / x-axis names (same length as values)"},
                    "values": {**num_list, "description": "One series of numbers, same order as labels"},
                    "series": {
                        "type": "array",
                        "description": "Several series: [{name, values:[...]}]; scatter/bubble: [{name, points:[{x,y,r}]}]",
                        "items": {"type": "object"},
                    },
                    "x_axis": {"type": "string", "description": "x-axis title"},
                    "y_axis": {"type": "string", "description": "y-axis title / unit"},
                    "nodes": {"type": "array", "items": {}, "description": "Graph nodes: names or {id,label,group,attributes}"},
                    "edges": {"type": "array", "items": {"type": "object"}, "description": "Graph edges / sankey flows: {source,target,label,value}"},
                    "groups": {"type": "array", "items": {"type": "object"}, "description": "box_plot: [{name, values:[raw numbers]}]"},
                    "ohlc": {"type": "array", "items": {"type": "object"}, "description": "candlestick: [{label,open,high,low,close}]"},
                    "min": {"type": "number", "description": "gauge minimum"},
                    "max": {"type": "number", "description": "gauge maximum"},
                },
                "required": ["type", "title"],
            },
        },
    }


# ------------------------------------------------------------------ helpers
def _num(v: Any, what: str) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ChartError(f"{what} must be a number, got {v!r}.")
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            raise ChartError(f"{what} must be a finite number.")
        return v
    if isinstance(v, str):
        t = v.strip().replace(",", "").rstrip("%")
        try:
            f = float(t)
        except ValueError:
            raise ChartError(f"{what} must be a number, got {v!r}.") from None
        if math.isfinite(f):
            return f
    raise ChartError(f"{what} must be a number, got {v!r}.")


def _list(v: Any, what: str) -> list:
    if v is None:
        return []
    if isinstance(v, str):  # models sometimes pass JSON as a string
        import json

        try:
            v = json.loads(v)
        except ValueError:
            raise ChartError(f"{what} must be a list.") from None
    if not isinstance(v, list):
        raise ChartError(f"{what} must be a list.")
    return v


def normalize_type(raw: Any) -> str:
    k = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    k = ALIASES.get(k, k)
    if k not in TYPES:
        raise ChartError(f"Unknown chart type {raw!r}. Valid types: {', '.join(TYPES)}.")
    return k


def _series(args: dict, labels: list) -> list[dict]:
    """Normalise values / series into [{name, values:[number|None]}]."""
    out = []
    raw = _list(args.get("series"), "series")
    if raw:
        for i, s in enumerate(raw):
            if isinstance(s, dict):
                name = str(s.get("name") or s.get("label") or f"Series {i + 1}")
                vals = s.get("values", s.get("data"))
            else:
                name, vals = f"Series {i + 1}", s
            vals = _list(vals, f"series[{i}].values")
            if vals and isinstance(vals[0], dict):  # [{x,y}] -> y
                vals = [v.get("y", v.get("value")) for v in vals]
            out.append({"name": name, "values": [_num(v, f"series[{i}] value") for v in vals]})
    elif args.get("values") is not None:
        vals = _list(args.get("values"), "values")
        out.append({"name": str(args.get("y_axis") or "Value"), "values": [_num(v, "value") for v in vals]})
    return out


def _check_lengths(labels: list, series: list[dict]) -> None:
    for s in series:
        if len(s["values"]) != len(labels):
            raise ChartError(
                f"labels has {len(labels)} items but series '{s['name']}' has {len(s['values'])} values — "
                "they must be the same length and in the same order."
            )


def _categorical(t: str, args: dict, spec: dict, *, single: bool = False, non_negative: bool = False, min_points: int = 1) -> int:
    labels = [str(x) for x in _list(args.get("labels"), "labels")]
    series = _series(args, labels)
    if not series or not any(v is not None for s in series for v in s["values"]):
        raise ChartError("No numbers given. Provide `labels` and `values` (or `series`).")
    if not labels:
        labels = [str(i + 1) for i in range(len(series[0]["values"]))]
    _check_lengths(labels, series)
    if len(labels) < min_points:
        raise ChartError(f"{t} needs at least {min_points} labels.")
    if single:
        series = series[:1]
    if non_negative and any(v is not None and v < 0 for s in series for v in s["values"]):
        raise ChartError(f"{t} values must not be negative.")
    if len(labels) * len(series) > MAX_POINTS:
        raise ChartError(f"Too many data points (max {MAX_POINTS}).")
    spec["labels"] = labels
    spec["series"] = [{"name": s["name"], "data": [{"x": l, "y": v} for l, v in zip(labels, s["values"])]} for s in series]
    return len(labels) * len(series)


def build(args: dict) -> tuple[dict, str]:
    """-> (spec for the browser, short summary). Raises ChartError with a message the model can act on."""
    if not isinstance(args, dict):
        raise ChartError("Arguments must be an object.")
    t = normalize_type(args.get("type") or args.get("visualization_type") or args.get("chart_type"))
    spec: dict[str, Any] = {"visualization_type": t, "title": str(args.get("title") or "")[:160]}
    for k in ("x_axis", "y_axis"):
        if args.get(k):
            spec[k] = str(args[k])[:80]
    n: int

    if t in _GRAPHS:
        nodes = _list(args.get("nodes"), "nodes")
        edges = _list(args.get("edges"), "edges")
        if not nodes and edges:  # nodes can be inferred from the edges
            seen: list[str] = []
            for e in edges:
                if isinstance(e, dict):
                    for k in (e.get("source", e.get("from")), e.get("target", e.get("to"))):
                        if k is not None and str(k) not in seen:
                            seen.append(str(k))
            nodes = seen
        if not nodes:
            raise ChartError("A graph needs `nodes` (names) and `edges` ([{source,target,label}]).")
        if len(nodes) > 500 or len(edges) > 2000:
            raise ChartError("Graph too large (max 500 nodes, 2000 edges).")
        for e in edges:
            if not isinstance(e, dict) or ("source" not in e and "from" not in e):
                raise ChartError("Every edge must be an object like {\"source\": \"A\", \"target\": \"B\", \"label\": \"…\"}.")
        spec["nodes"], spec["edges"] = nodes, edges
        n = len(nodes)
        return spec, f"{n} nodes, {len(edges)} edges"

    if t == "sankey_diagram":
        edges = _list(args.get("edges"), "edges")
        if not edges:
            raise ChartError("A sankey diagram needs `edges`: [{source, target, value}].")
        for e in edges:
            if not isinstance(e, dict) or "source" not in e or "target" not in e:
                raise ChartError("Every flow must look like {\"source\": \"A\", \"target\": \"B\", \"value\": 10}.")
            v = _num(e.get("value"), "flow value")
            if v is None or v <= 0:
                raise ChartError("Every flow needs a positive numeric `value`.")
            e["value"] = v
        spec["edges"] = edges
        return spec, f"{len(edges)} flows"

    if t in ("scatter_plot", "bubble_chart"):
        bubble = t == "bubble_chart"
        raw = _list(args.get("series"), "series")
        if not raw and args.get("labels") and args.get("values"):
            raise ChartError("Scatter/bubble charts need `series: [{name, points: [{x, y}]}]` with numeric x and y.")
        series = []
        total = 0
        for i, s in enumerate(raw):
            pts = _list(s.get("points", s.get("data", s.get("values"))) if isinstance(s, dict) else s, f"series[{i}].points")
            data = []
            for p in pts:
                if isinstance(p, (list, tuple)):
                    p = dict(zip(("x", "y", "r"), p))
                if not isinstance(p, dict):
                    raise ChartError("Each point must be {x, y}" + (", r}" if bubble else "}") + ".")
                x, y = _num(p.get("x"), "x"), _num(p.get("y"), "y")
                if x is None or y is None:
                    raise ChartError("Every point needs numeric x and y.")
                d = {"x": x, "y": y}
                if bubble:
                    r = _num(p.get("r", p.get("size", p.get("z"))), "r")
                    if r is None:
                        raise ChartError("Bubble points need a numeric size `r`.")
                    d["r"] = r
                data.append(d)
            total += len(data)
            series.append({"name": str(s.get("name") if isinstance(s, dict) and s.get("name") else f"Series {i + 1}"), "data": data})
        if not total:
            raise ChartError("No points given.")
        if total > MAX_POINTS:
            raise ChartError(f"Too many points (max {MAX_POINTS}).")
        spec["series"] = series
        return spec, f"{total} points"

    if t == "histogram":
        vals = [_num(v, "value") for v in _list(args.get("values"), "values")]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            raise ChartError("A histogram needs a list of raw numbers in `values` (at least 2).")
        if len(vals) > MAX_POINTS:
            raise ChartError(f"Too many values (max {MAX_POINTS}).")
        spec["series"] = [{"name": "values", "data": vals}]
        return spec, f"{len(vals)} values"

    if t == "gauge_chart":
        vals = _list(args.get("values"), "values")
        v = _num(vals[0] if vals else args.get("value"), "value")
        if v is None:
            raise ChartError("A gauge needs the value in `values: [v]`, plus `min` and `max`.")
        lo = _num(args.get("min"), "min")
        hi = _num(args.get("max"), "max")
        spec.update({"value": v, "min": 0 if lo is None else lo, "max": 100 if hi is None else hi})
        if spec["max"] <= spec["min"]:
            raise ChartError("`max` must be greater than `min`.")
        return spec, f"value {v:g}"

    if t == "box_plot":
        groups = _list(args.get("groups"), "groups")
        if not groups:
            raise ChartError("A box plot needs `groups`: [{name, values:[raw numbers]}].")
        out = []
        for i, g in enumerate(groups):
            if not isinstance(g, dict):
                raise ChartError("Each group must be {name, values:[…]}.")
            vals = [_num(v, "value") for v in _list(g.get("values", g.get("data")), "group values")]
            vals = [v for v in vals if v is not None]
            if len(vals) < 3:
                raise ChartError(f"Group '{g.get('name', i + 1)}' needs at least 3 numbers.")
            out.append({"name": str(g.get("name") or g.get("label") or f"Group {i + 1}"), "values": vals})
        spec["groups"] = out
        return spec, f"{len(out)} groups"

    if t == "candlestick_chart":
        rows = _list(args.get("ohlc"), "ohlc")
        if not rows:
            raise ChartError("A candlestick chart needs `ohlc`: [{label, open, high, low, close}].")
        out = []
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                raise ChartError("Each ohlc row must be an object.")
            vals = {k: _num(r.get(k), k) for k in ("open", "high", "low", "close")}
            if any(v is None for v in vals.values()):
                raise ChartError("Every ohlc row needs numeric open, high, low and close.")
            if vals["low"] > min(vals["open"], vals["close"]) or vals["high"] < max(vals["open"], vals["close"]):
                raise ChartError(f"ohlc row {i + 1}: low must be <= open/close and high must be >= open/close.")
            out.append({"label": str(r.get("label") or r.get("date") or r.get("x") or i + 1), **vals})
        spec["ohlc"] = out
        return spec, f"{len(out)} candles"

    # ---- categorical family
    single = t in ("pie_chart", "donut_chart", "polar_area_chart", "funnel_chart", "treemap", "waterfall_chart")
    nonneg = t in ("pie_chart", "donut_chart", "polar_area_chart", "funnel_chart", "treemap")
    min_points = 2 if t == "slope_chart" else 3 if t == "radar_chart" else 1
    n = _categorical(t, args, spec, single=single, non_negative=nonneg, min_points=min_points)
    if t == "slope_chart" and len(spec["labels"]) != 2:
        raise ChartError("A slope chart compares exactly 2 labels (e.g. 2020 and 2024).")
    return spec, f"{n} values"
