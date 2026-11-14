"""Dashboards: the model supplies numbers, the app owns the layout.

Every dashboard follows the same pattern whatever model wrote it: header → KPI cards → charts (the first, a trend,
full width) → one detail table. The templates below say which KPIs and charts each kind of dashboard needs; the
chosen template is told to the model in the system prompt, and the browser (frontend/js/viz.js) draws the result.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import charts

# id -> (name, kpi roles, chart roles [(title, preferred type)], table title)
TEMPLATES: dict[str, dict[str, Any]] = {
    "sales": {
        "name": "Sales Performance",
        "kpis": ["Total Revenue", "Total Orders", "Total Profit", "Profit Margin", "Average Order Value", "Sales Growth %"],
        "charts": [("Monthly revenue trend", "line_chart"), ("Sales by product category", "bar_chart"),
                   ("Sales contribution by region", "donut_chart"), ("Top 10 products by revenue", "horizontal_bar_chart"),
                   ("Weekly sales trend", "area_chart")],
        "table": "Top and bottom performing products",
    },
    "financial": {
        "name": "Financial",
        "kpis": ["Total Revenue", "Total Expenses", "Net Profit", "Gross Profit Margin", "Operating Expenses", "Cash Flow"],
        "charts": [("Revenue vs expenses over time", "line_chart"), ("Revenue to net profit breakdown", "waterfall_chart"),
                   ("Expense distribution", "donut_chart"), ("Budget vs actual spending", "bar_chart"),
                   ("Cash flow trend", "area_chart")],
        "table": "Monthly financial statement",
    },
    "hr": {
        "name": "HR Analytics",
        "kpis": ["Total Employees", "New Hires", "Attrition Rate", "Average Salary", "Employee Satisfaction", "Absenteeism Rate"],
        "charts": [("Headcount trend", "line_chart"), ("Employees by department", "bar_chart"),
                   ("Workforce composition", "donut_chart"), ("Attrition by department", "bar_chart"),
                   ("Salary distribution", "histogram")],
        "table": "Department metrics",
    },
    "marketing": {
        "name": "Marketing Analytics",
        "kpis": ["Campaign Spend", "Impressions", "Clicks", "CTR", "Conversions", "ROAS"],
        "charts": [("Campaign performance over time", "line_chart"), ("Performance by channel", "bar_chart"),
                   ("Impressions to conversions", "funnel_chart"), ("Budget allocation", "donut_chart"),
                   ("Ad spend vs conversions", "scatter_plot")],
        "table": "Campaign performance",
    },
    "ecommerce": {
        "name": "E-commerce",
        "kpis": ["Total Sales", "Total Orders", "Total Customers", "Conversion Rate", "Cart Abandonment Rate", "Average Order Value"],
        "charts": [("Daily sales trend", "line_chart"), ("Best-selling products", "horizontal_bar_chart"),
                   ("Customer purchase journey", "funnel_chart"), ("Order status", "donut_chart"),
                   ("Sales by location", "bar_chart")],
        "table": "Product performance",
    },
    "executive": {
        "name": "Executive Summary",
        "kpis": ["Total Revenue", "Net Profit", "Revenue Growth", "Operating Margin", "Customer Growth", "Target Achievement"],
        "charts": [("Revenue and profit trend", "line_chart"), ("Department performance", "bar_chart"),
                   ("Profit breakdown", "waterfall_chart"), ("Target achievement", "gauge_chart")],
        "table": "Business unit performance",
    },
    "operations": {
        "name": "Operations",
        "kpis": ["Total Production", "Production Efficiency", "Defect Rate", "Downtime", "On-Time Delivery", "Operating Cost"],
        "charts": [("Production trend", "line_chart"), ("Production by facility", "bar_chart"),
                   ("Defect categories (largest first)", "bar_chart"), ("Machine status", "donut_chart"),
                   ("Downtime trend", "area_chart")],
        "table": "Production performance",
    },
    "customer": {
        "name": "Customer Analytics",
        "kpis": ["Total Customers", "New Customers", "Returning Customers", "Retention Rate", "Churn Rate", "Customer Lifetime Value"],
        "charts": [("Customer growth trend", "line_chart"), ("Customers by segment", "bar_chart"),
                   ("New vs returning customers", "donut_chart"), ("Retention by cohort", "heatmap"),
                   ("Customer spending distribution", "histogram")],
        "table": "Top customers",
    },
    "general": {
        "name": "Dashboard",
        "kpis": ["Headline metric 1", "Headline metric 2", "Headline metric 3", "Headline metric 4"],
        "charts": [("Trend over time", "line_chart"), ("Comparison by category", "bar_chart"), ("Share of total", "donut_chart")],
        "table": "Detail",
    },
}

_KEYWORDS: list[tuple[str, str]] = [
    ("executive", r"executive|\bceo\b|\bboard\b|c-suite|leadership"),
    ("hr", r"\bhr\b|human resources|employee|attrition|headcount|workforce|hiring|payroll"),
    ("marketing", r"marketing|campaign|\bads?\b|advertis|impressions|\bctr\b|\broas\b|seo|social media"),
    ("ecommerce", r"e-?commerce|online store|web ?shop|shopify|cart|checkout"),
    ("operations", r"operations?|production|manufactur|factory|supply chain|logistics|downtime|warehouse"),
    ("customer", r"customer|churn|retention|\bclv\b|\bltv\b|subscribers?"),
    ("financial", r"financ|expenses?|cash ?flow|budget|p&l|profit and loss|balance sheet|accounting"),
    ("sales", r"sales|revenue|orders|\bsell"),
]
ASKS_DASHBOARD = re.compile(r"\b(?:dashboards?|kpis?|scorecards?|executive summary|bi report)\b", re.I)


def detect_template(text: str) -> str:
    for tid, pattern in _KEYWORDS:
        if re.search(pattern, text or "", re.I):
            return tid
    return "general"


def blueprint(tid: str) -> str:
    """The pattern a model must follow for this template (goes into the system prompt)."""
    t = TEMPLATES.get(tid) or TEMPLATES["general"]
    kpis = "; ".join(t["kpis"])
    charts_ = "; ".join(f"{i}. {title} ({typ.replace('_chart', '').replace('_', ' ')})" for i, (title, typ) in enumerate(t["charts"], 1))
    return (
        f"Use template \"{tid}\" ({t['name']}). KPI cards (4-6, pick the ones the data supports): {kpis}. "
        f"Charts in this order: {charts_}. Table: {t['table']} (max 10 rows). "
        "Skip a card or chart the data cannot support — never invent numbers to fill a slot."
    )


# ----------------------------------------------------------------- the tool
def spec_for_model() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "create_dashboard",
            "description": (
                "Build a dashboard from data; the app lays it out in a fixed pattern (KPI cards, charts, table). Call it once "
                "with real numbers. Never write dashboard code. After it appears, write 2-4 sentences of insight."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {"type": "string", "enum": sorted(TEMPLATES), "description": "dashboard kind"},
                    "title": {"type": "string"},
                    "period": {"type": "string", "description": "e.g. 'Jan-Dec 2025'"},
                    "kpis": {
                        "type": "array",
                        "description": "4-6 cards: {label, value (number or text), unit ('$','%',…), change (percent vs before)}",
                        "items": {"type": "object"},
                    },
                    "charts": {
                        "type": "array",
                        "description": "2-7 charts, each with the same fields as create_chart (type, title, labels, values/series…)",
                        "items": {"type": "object"},
                    },
                    "table": {
                        "type": "object",
                        "description": "{title, columns: [..], rows: [[..]]}",
                    },
                },
                "required": ["template", "kpis", "charts"],
            },
        },
    }


class DashboardError(ValueError):
    pass


# A small model asked to fill every slot of a template will make up numbers. Charts that admit it are refused.
_INVENTED = re.compile(r"\b(estimated?|estimates|projected|assumed|assumption|sample data|dummy|illustrative|hypothetical|placeholder|mock|fake|example data|simulated|approximate)\b", re.I)


def _signature(item: dict) -> str:
    """Two charts with the same labels and values are the same chart."""
    return repr((item.get("labels"), item.get("values"), item.get("series"), item.get("groups"), item.get("edges"), item.get("ohlc")))


def _fmt_number(x: float) -> str:
    a = abs(x)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= limit:
            v = x / limit
            return (f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:.0f}") + suffix
    return f"{x:,.2f}".rstrip("0").rstrip(".") if a % 1 else f"{int(x):,}"


def _parse_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*[-+]?[\d,]*\.?\d+\s*", v)
        if m:
            return float(v.replace(",", ""))
    return None


_PREFIX_UNITS = set("$€£¥₹")


def format_value(value: Any, unit: str = "") -> str:
    unit = (unit or "").strip()
    num = _parse_number(value)
    if num is None:
        return str(value).strip()[:24]
    body = _fmt_number(num)
    if unit and unit[0] in _PREFIX_UNITS:
        return unit + body
    if unit in ("%", "x", "×"):
        return body + unit
    return body + (" " + unit if unit else "")


def _cell(v: Any) -> str:
    """Table cell text: numbers get thousands separators, everything else is shown as written."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if v % 1 else f"{int(v):,}"
    return str(v)[:60]


def _kpi(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or item.get("name") or "").strip()[:40]
    if not label or item.get("value") is None:
        return None
    change = _parse_number(str(item.get("change")).replace("%", "")) if item.get("change") not in (None, "") else None
    return {"label": label, "value": format_value(item["value"], str(item.get("unit") or "")), "change": change}


def _coerce_chart(item: dict) -> dict:
    """Chart kinds in the dashboard guide that have no drawing of their own become the closest one that does."""
    item = dict(item)
    t = str(item.get("type") or item.get("visualization_type") or item.get("chart_type") or "").lower().replace(" ", "_")
    if t in ("pareto", "pareto_chart") and isinstance(item.get("values"), list) and isinstance(item.get("labels"), list):
        pairs = sorted(zip(item["labels"], item["values"]), key=lambda p: -(p[1] if isinstance(p[1], (int, float)) else 0))
        item["labels"], item["values"] = [p[0] for p in pairs], [p[1] for p in pairs]
        t = "bar_chart"
    elif t in ("map", "choropleth", "geo_map", "bullet", "bullet_chart", "column"):
        t = "horizontal_bar_chart" if t in ("map", "choropleth", "geo_map") else "bar_chart"
    item["type"] = t
    item.pop("visualization_type", None)
    item.pop("chart_type", None)
    return item


def build(args: dict) -> tuple[dict, str, list[str]]:
    """-> (spec, summary, warnings). Raises DashboardError when too little survives to make a dashboard."""
    if not isinstance(args, dict):
        raise DashboardError("Arguments must be an object.")
    tid = str(args.get("template") or "general").lower().replace(" ", "_")
    tid = {"hr_analytics": "hr", "e-commerce": "ecommerce", "sales_performance": "sales", "finance": "financial",
           "marketing_analytics": "marketing", "customer_analytics": "customer"}.get(tid, tid)
    if tid not in TEMPLATES:
        tid = "general"
    t = TEMPLATES[tid]
    warnings: list[str] = []

    kpis = [k for k in (_kpi(x) for x in (args.get("kpis") or [])[:12]) if k][:8]
    if len(kpis) < 3:
        raise DashboardError(
            "Dashboard not built: it needs at least 3 KPI cards, each {label, value, unit?, change?}. "
            f"For this template use cards such as: {', '.join(t['kpis'])}."
        )

    built: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate((args.get("charts") or [])[:9]):
        if not isinstance(raw, dict):
            warnings.append(f"chart {i + 1} skipped (not an object)")
            continue
        if _INVENTED.search(str(raw.get("title") or "")):
            warnings.append(f"chart {i + 1} (\"{str(raw.get('title'))[:40]}\") skipped: only real data from the user or a tool may be charted, never estimates")
            continue
        sig = _signature(raw)
        if sig in seen:
            warnings.append(f"chart {i + 1} skipped: it repeats an earlier chart's data")
            continue
        seen.add(sig)
        try:
            spec, summary = charts.build(_coerce_chart(raw))
        except charts.ChartError as exc:
            warnings.append(f"chart {i + 1} skipped: {exc}")
            continue
        built.append({"spec": spec, "summary": summary})
    if len(built) < 2:
        raise DashboardError(
            "Dashboard not built: it needs at least 2 valid charts (same fields as create_chart). "
            + ("Problems: " + "; ".join(warnings) if warnings else "")
        )
    # the layout: a trend-like first chart takes the full row; the rest sit two to a row
    for i, c in enumerate(built[:7]):
        c["wide"] = i == 0 and c["spec"]["visualization_type"] in ("line_chart", "area_chart", "step_chart", "bar_chart", "waterfall_chart")
        if not c["spec"].get("title") and i < len(t["charts"]):
            c["spec"]["title"] = t["charts"][i][0]
    built = built[:7]
    # an odd number of half-width charts leaves a hole: let the last one span the row
    halves = [c for c in built if not c["wide"]]
    if halves and len(halves) % 2 == 1:
        halves[-1]["wide"] = True

    table = None
    tb = args.get("table")
    if isinstance(tb, dict) and isinstance(tb.get("columns"), list) and isinstance(tb.get("rows"), list) and tb["columns"]:
        cols = [str(c)[:30] for c in tb["columns"][:8]]
        rows = []
        for r in tb["rows"][:15]:
            if isinstance(r, dict):
                r = [r.get(c) for c in tb["columns"][:8]]
            if isinstance(r, list):
                rows.append([_cell(v) for v in (r + [""] * len(cols))[: len(cols)]])
        if rows:
            table = {"title": str(tb.get("title") or t["table"])[:80], "columns": cols, "rows": rows}

    spec = {
        "kind": "dashboard",
        "template": tid,
        "template_name": t["name"],
        "title": str(args.get("title") or f"{t['name']} dashboard")[:100],
        "period": str(args.get("period") or "")[:60],
        "kpis": kpis,
        "charts": built,
        "table": table,
    }
    summary = f"{len(kpis)} KPIs, {len(built)} charts" + (", 1 table" if table else "")
    return spec, summary, warnings
