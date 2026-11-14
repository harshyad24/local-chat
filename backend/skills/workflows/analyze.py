"""/analyze - inspect an attached dataset or document. Tables are analysed by pandas code (profiling, data quality, KPIs, trends,
anomalies, correlations, charts, a cleaned copy); documents are summarised with their figures quoted from the text. Findings are
computed facts; assumptions are labelled as assumptions; the model only writes the narrative from those facts."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

from .. import analysis, common
from ..registry import Slot, Workflow, register
from ... import charts, dashboards, tools

TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".json"}
DOC_EXT = {".pdf", ".docx", ".txt", ".md"}


def pick_file(files: list[dict]) -> Optional[dict]:
    """The newest attached file that can be analysed."""
    for f in reversed(files or []):
        if f.get("ext") in TABLE_EXT | DOC_EXT:
            return {"id": f["id"], "name": f["name"], "path": f["path"], "ext": f["ext"]}
    return None


def prefill(text: str, ctx: dict) -> dict:
    out = {"goal": text.strip()} if text.strip() else {}
    f = pick_file(ctx.get("files") or [])
    if f:
        out["file"] = f
    return out


def _chart(args: dict):
    try:
        return charts.build(args)[0]
    except charts.ChartError:
        return None


async def tabular(rc, path: str, name: str, goal: str) -> dict:
    """The full analysis of one table. Returns everything /analyze shows and /create dashboard reuses."""
    await rc.progress(1, 6, "Reading the file")
    frames = await asyncio.to_thread(analysis.load, path)
    if not frames:
        raise ValueError("The file has no readable table.")
    sheet = max(frames, key=lambda k: frames[k].shape[0] * frames[k].shape[1])
    df = frames[sheet]
    notes = [f"Analysed sheet “{sheet}” (the largest of {len(frames)})."] if len(frames) > 1 else []
    await rc.progress(2, 6, "Checking data quality")
    q = await asyncio.to_thread(analysis.quality, df)
    nums = analysis.numeric_columns(df)
    cats = analysis.category_columns(df)
    date_col = analysis.find_date_column(df)
    metric = analysis.main_metric(df, goal)
    how = "mean" if metric and analysis.MEAN_WORDS.search(str(metric)) and not analysis.SUM_WORDS.search(str(metric)) else "sum"
    await rc.progress(3, 6, "Calculating KPIs, trends and anomalies")
    tr = await asyncio.to_thread(analysis.trend, df, date_col, metric, how) if date_col and metric else None
    cat_col = next((c for c in cats if c != date_col), None)
    bc = await asyncio.to_thread(analysis.by_category, df, cat_col, metric, how) if cat_col and metric else None
    outl = await asyncio.to_thread(analysis.outliers, df, nums)
    corr = await asyncio.to_thread(analysis.correlations, df, nums)
    rel = await asyncio.to_thread(analysis.relationship, df, goal)

    kpis = [{"label": "Rows", "value": f"{len(df):,}"}, {"label": "Columns", "value": f"{df.shape[1]}"}]
    for c in nums[:4]:
        m = "mean" if analysis.MEAN_WORDS.search(str(c)) and not analysis.SUM_WORDS.search(str(c)) else "sum"
        v = float(df[c].mean() if m == "mean" else df[c].sum())
        kpis.append({"label": f"{'Average' if m == 'mean' else 'Total'} {c}", "value": analysis.fmt(v)})
    kpis.append({"label": "Missing cells", "value": f"{q['missing_cells']:,}"})

    findings: list[dict] = []
    if metric and tr:
        findings.append({"kind": "finding", "text": f"{metric} by {tr['rule']} goes {tr['direction']}" + (f" ({tr['change_pct']:+.1f}% from the first to the last {tr['rule']})" if tr["change_pct"] is not None else "")
                         + f"; peak {tr['peak']['period']} ({analysis.fmt(tr['peak']['value'])}), lowest {tr['low']['period']} ({analysis.fmt(tr['low']['value'])})."})
        if tr.get("partial_excluded"):
            findings.append({"kind": "assumption", "text": f"Partly filled period(s) left out of the trend so they do not look like a drop or surge: {', '.join(tr['partial_excluded'])}."})
        for sp in tr["spikes"][:2]:
            findings.append({"kind": "warning", "text": f"Unusual {sp['change_pct']:+.1f}% change in {metric} at {sp['period']}: worth checking."})
    if bc:
        findings.append({"kind": "finding", "text": f"Top {cat_col}: {bc['labels'][0]} with {analysis.fmt(bc['values'][0])} ({bc['top_share']}% of the total)." if bc["top_share"] else f"Highest average {metric} by {cat_col}: {bc['labels'][0]} ({analysis.fmt(bc['values'][0])})."})
    for o in outl[:3]:
        findings.append({"kind": "warning", "text": f"{o['column']}: {o['count']:,} outliers ({o['pct']}%) outside {analysis.fmt(o['low'])}–{analysis.fmt(o['high'])}; largest values {', '.join(analysis.fmt(x) for x in o['examples'])}."})
    for c in corr[:3]:
        findings.append({"kind": "finding", "text": f"{c['a']} and {c['b']} move together ({analysis.strength(c['r'])}, r = {c['r']:+.2f}). Correlation does not show cause."})
    if rel:
        findings.append({"kind": "finding", "text": f"Relationship {rel['x']} → {rel['y']}: r = {rel['r']:+.2f} ({rel['strength']}), R² = {rel['r2']:.2f} on {rel['n']:,} rows; each +1 in {rel['x']} goes with {rel['slope']:+.3g} in {rel['y']}. This is association, not proof of cause."})
    for i in q["issues"][:5]:
        findings.append({"kind": "warning", "text": i})
    assumptions = []
    if metric:
        assumptions.append({"kind": "assumption", "text": f"“{metric}” was taken as the main measure ({'named in your request' if str(metric).lower() in goal.lower() else 'chosen from its name'}); aggregated by {how}."})
    if date_col:
        assumptions.append({"kind": "assumption", "text": f"“{date_col}” was treated as the date column."})
    if cat_col:
        assumptions.append({"kind": "assumption", "text": f"Grouped by “{cat_col}” (the first suitable category column)."})

    await rc.progress(4, 6, "Drawing charts")
    blocks: list[dict] = [{"type": "overview", "title": f"{name}: {len(df):,} rows × {df.shape[1]} columns", "text": f"Goal: {goal or 'general analysis'}.", "kpis": kpis[:7]}]
    chart_specs, raw_charts = [], []

    def mk(a: dict):
        spec = _chart(a)
        if spec:
            raw_charts.append(a)
        return spec

    if tr:
        sp = mk({"type": "line_chart", "title": f"{metric} by {tr['rule']}", "labels": tr["labels"], "values": tr["values"], "x_axis": tr["rule"].title(), "y_axis": str(metric)})
        if sp:
            chart_specs.append(sp)
    if bc:
        sp = mk({"type": "horizontal_bar_chart", "title": f"{metric} by {cat_col}", "labels": bc["labels"], "values": bc["values"]})
        if sp:
            chart_specs.append(sp)
    if rel:
        sp = mk({"type": "scatter_plot", "title": f"{rel['y']} vs {rel['x']} (r = {rel['r']:+.2f})", "x_axis": rel["x"], "y_axis": rel["y"], "series": [{"name": "rows", "points": rel["points"]}]})
        if sp:
            chart_specs.append(sp)
    if metric:
        vals = [float(v) for v in df[metric].dropna().head(2000)]
        sp = mk({"type": "histogram", "title": f"Distribution of {metric}", "values": vals}) if len(vals) > 10 else None
        if sp:
            chart_specs.append(sp)
    if len(nums) >= 3:
        cols = nums[:7]
        cm = df[cols].corr(numeric_only=True).round(2)
        sp = mk({"type": "correlation_heatmap", "title": "Correlation between numeric columns", "labels": [str(c) for c in cols],
                     "series": [{"name": str(r), "data": [float(x) for x in cm.loc[r].fillna(0)]} for r in cols]})
        if sp:
            chart_specs.append(sp)
    more = rc.pref("output.charts") == "more"
    for sp in chart_specs[: (6 if more else 4)]:
        blocks.append({"type": "chart", "spec": sp})
    blocks.append({"type": "findings", "title": "Findings (computed from your data)", "items": findings or [{"kind": "finding", "text": "No notable trends or anomalies were found."}]})
    if assumptions:
        blocks.append({"type": "findings", "title": "Assumptions I made", "items": assumptions})
    blocks.append({"type": "dq", "title": "Data-quality report", "rows": q["rows"][:40], "summary": f"{q['duplicates']:,} duplicate rows · {q['missing_cells']:,} empty cells · " + ("; ".join(q["issues"][:3]) or "no major problems found")})

    await rc.progress(5, 6, "Preparing downloads")
    cleaned, log = await asyncio.to_thread(analysis.clean, df)
    files = [rc.out_file(f"cleaned_{Path(name).stem}.csv", cleaned.to_csv(index=False))]
    sections = [{"heading": "Summary", "paragraphs": [f"File: {name} · {len(df):,} rows × {df.shape[1]} columns · goal: {goal or 'general analysis'}"]},
                {"heading": "Key figures", "bullets": [f"{k['label']}: {k['value']}" for k in kpis]},
                {"heading": "Findings", "bullets": [f["text"] for f in findings]},
                {"heading": "Assumptions", "bullets": [a["text"] for a in assumptions]},
                {"heading": "Data quality", "bullets": q["issues"] or ["No major problems found."]},
                {"heading": "Cleaning applied to the downloadable copy", "bullets": log or ["Nothing needed cleaning."]}]
    files.append(rc.out_file(f"analysis_{Path(name).stem}.docx", common.build_docx(f"Analysis of {name}", sections)))
    notes += [f"Cleaned copy: {l}" for l in log]
    facts = [f"File {name}: {len(df):,} rows, {df.shape[1]} columns. Goal: {goal or 'general'}."] + [f"{k['label']}: {k['value']}" for k in kpis] \
        + [f"[{f['kind']}] {f['text']}" for f in findings] + [f"[assumption] {a['text']}" for a in assumptions] + [f"Data quality: {i}" for i in q["issues"][:4]]
    return {"blocks": blocks, "files": files, "notes": notes, "facts": facts, "df": df, "kpis": kpis, "tr": tr, "bc": bc, "cat_col": cat_col, "metric": metric,
            "charts": chart_specs, "chart_args": raw_charts, "date_col": date_col, "nums": nums, "how": how}


def build_dashboard(res: dict, topic: str, name: str):
    """A dashboard laid out by the app from the figures `tabular` already computed. -> (spec, summary, warnings); raises DashboardError."""
    df = res["df"]
    kpis = []
    for k in res["kpis"][2:7]:
        label = k["label"]
        change = res["tr"]["change_pct"] if res["tr"] and res["metric"] and str(res["metric"]) in label and res["tr"].get("change_pct") is not None else None
        kpis.append({"label": label, "value": k["value"], **({"change": change} if change is not None else {})})
    kpis = [{"label": "Records", "value": f"{len(df):,}"}] + kpis
    cols_text = " ".join(map(str, df.columns)) + " " + topic
    args = {"template": dashboards.detect_template(cols_text), "kpis": kpis[:6], "charts": [dict(c) for c in res["chart_args"][:6]]}
    if res["cat_col"] and res["metric"]:
        g = df.groupby(res["cat_col"])[res["metric"]].agg("mean" if res["how"] == "mean" else "sum").sort_values(ascending=False)
        args["table"] = {"title": f"{res['cat_col']} ranked by {res['metric']}", "columns": [str(res["cat_col"]), str(res["metric"])],
                         "rows": [[str(i), analysis.fmt(float(v))] for i, v in g.head(10).items()]}
    spec, summary, warnings = dashboards.build(args)
    spec["title"] = f"{Path(name).stem.replace('_', ' ').title()} dashboard"
    return spec, summary, warnings


_FIG = re.compile(r"(?P<label>[A-Za-z][A-Za-z &/\-]{3,40}?)[:\s]+(?P<val>(?:₹|Rs\.?|\$|€|£)?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|crore|lakh|million|billion|bn|m|k|cr)?)", re.I)
_FINANCE = re.compile(r"revenue|net income|net profit|operating (?:income|profit)|ebitda|total assets|total liabilities|cash flow|gross margin|eps|dividend|growth|sales", re.I)


async def document(rc, path: str, name: str, goal: str) -> dict:
    await rc.progress(1, 4, "Reading the document")
    text = await asyncio.to_thread(tools._extract, Path(path))
    if not text or len(text.strip()) < 40:
        raise ValueError("No readable text was found in that document (a scan needs OCR; try the image or a text version).")
    words = len(re.findall(r"\w+", text))
    await rc.progress(2, 4, "Finding figures and key sections")
    figs, seen = [], set()
    for line in text.splitlines():
        if _FINANCE.search(line):
            for m in _FIG.finditer(line):
                key = (m.group("label").strip().lower(), m.group("val"))
                if key not in seen and re.search(r"\d", m.group("val")):
                    seen.add(key)
                    figs.append({"kind": "finding", "text": f"{m.group('label').strip()}: {m.group('val').strip()}  (from the document: “{line.strip()[:110]}”)"})
        if len(figs) >= 14:
            break
    heads = [l.strip() for l in text.splitlines() if 3 < len(l.strip()) < 70 and (l.strip().isupper() or re.match(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?[A-Z][A-Za-z ,&\-]+$", l.strip())) and len(l.split()) <= 8][:12]
    await rc.progress(3, 4, "Summarising")
    summary = await rc.text_call(f"Summarise this document for someone with the goal: {goal or 'a general overview'}. Give 5 bullet points with the main facts, "
                                 f"using only the text.\n\nTEXT:\n{text[:7000]}", max_tokens=600)
    blocks = [{"type": "overview", "title": name, "text": f"Goal: {goal or 'overview'}.", "kpis": [{"label": "Words", "value": f"{words:,}"}, {"label": "Figures found", "value": len(figs)}, {"label": "Headings", "value": len(heads)}]}]
    if summary:
        blocks.append({"type": "markdown", "title": "Summary (written by the local model from the text)", "text": summary})
    if figs:
        blocks.append({"type": "findings", "title": "Figures found in the document", "items": figs})
    if heads:
        blocks.append({"type": "markdown", "title": "Structure", "text": "\n".join("- " + h for h in heads)})
    rc.evidence.append(text[:50000])
    return {"blocks": blocks, "files": [], "notes": ["Only text that is in the document is quoted. Check any figure against the original before relying on it."],
            "facts": [f"Document {name}: {words:,} words.", f"Goal: {goal or 'overview'}"] + [f["text"] for f in figs[:10]] + ([f"Summary:\n{summary}"] if summary else [])}


async def run(rc) -> dict:
    f = rc.slots.get("file")
    goal = str(rc.slots.get("goal") or "")
    if not isinstance(f, dict) or not Path(str(f.get("path", ""))).exists():
        return {"error": "The attached file could not be found. Attach it again with the + button."}
    try:
        res = await (tabular(rc, f["path"], f["name"], goal) if f["ext"] in TABLE_EXT else document(rc, f["path"], f["name"], goal))
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - unreadable / malformed files must give a clear message, not a stack trace
        return {"error": f"Could not analyse {f['name']}: {type(exc).__name__}: {str(exc)[:150]}"}
    if res.get("df") is not None:
        # statistics involved -> charts (already in the result); asked for one, or enough dimensions to need one -> a dashboard as well
        asked = bool(dashboards.ASKS_DASHBOARD.search(goal))
        if asked or len(res.get("charts") or []) >= 4:
            try:
                spec, summ, warns = build_dashboard(res, goal, f["name"])
            except dashboards.DashboardError as exc:
                if asked:
                    res["notes"].append(f"A dashboard could not be laid out from this data ({exc}); the charts are shown instead.")
            else:
                blocks = [b for b in res["blocks"] if b["type"] != "chart"]  # the dashboard contains those charts
                res["blocks"] = blocks[:1] + [{"type": "dashboard", "spec": spec}] + blocks[1:]
                res["facts"].insert(1, f"A dashboard “{spec['title']}” ({summ}) was built from these same computed figures.")
                res["notes"].append("A dashboard was built because " + ("you asked for one." if asked else "the data has several dimensions (trend, categories, relationships). Use the PNG / Copy / CSV buttons on it to export.")
                                    + ("".join(f" Skipped: {w}." for w in warns[:2]) if warns else ""))
    res.pop("df", None)
    for k in ("tr", "bc", "charts", "chart_args", "nums", "kpis"):
        res.pop(k, None)
    res["summary"] = f"Analysed {f['name']}"
    res["what"] = "KPIs, charts, findings and a data-quality report"
    res["size"] = "5-8 sentences: the most important findings first, then the data-quality caveats, then 2-3 concrete recommendations that follow from the findings. Quote numbers exactly as given in the facts (including the largest outlier values) and do not guess what a column means beyond its name"
    return res


register(Workflow(
    id="analyze", skill="analyze", title="Data & document analysis", description="Inspect an attached dataset or document and report findings",
    keywords=re.compile(r"analy|dataset|csv|excel|xlsx|trend|anomal|correlat|relationship|report|financial", re.I), general=True, prefill=prefill, run=run,
    slots=[Slot("file", "File", "Which file should I analyse? Attach it with the + button in the message box and send it (a CSV, Excel sheet, PDF or Word file).", kind="file"),
           Slot("goal", "Goal", "", required=False)]))
