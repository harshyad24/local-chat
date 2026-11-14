"""Deterministic data analysis with pandas: profiling, data quality, trends, anomalies, correlations, KPIs, charts.
Every number shown to the user is computed here, in code; the model only writes about numbers it is handed."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

MAX_ROWS = 500_000
DATA_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json"}
SUM_WORDS = re.compile(r"revenue|sales|amount|total|profit|cost|spend|expense|quantity|qty|units|orders|income|price_total|value", re.I)
MEAN_WORDS = re.compile(r"price|rate|age|score|rating|margin|percent|pct|discount|salary|duration|time|weight|height|temperature", re.I)
METRIC_PRIORITY = [re.compile(r"revenue|sales|income|profit|turnover", re.I), re.compile(r"amount|total|value", re.I), re.compile(r"quantity|qty|units|orders", re.I),
                   re.compile(r"cost|spend|expense", re.I), MEAN_WORDS]
DATE_WORDS = re.compile(r"date|time|month|year|day|period|week|created|ordered|timestamp", re.I)
ID_WORDS = re.compile(r"(?:^|_|\b)(?:id|uuid|sku|code|zip|pincode|phone|index)(?:$|_|\b)", re.I)


def is_text(s: pd.Series) -> bool:
    """Text column. pandas 3 stores strings with its own `str` dtype, older versions as `object`: accept both."""
    return s.dtype == object or pd.api.types.is_string_dtype(s) or str(s.dtype).startswith("category")


def load(path: str) -> dict[str, pd.DataFrame]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".csv", ".tsv", ".txt", ".json") and b"\x00" in p.read_bytes()[:4096]:
        raise ValueError("That file is binary, not a text table. Export it as CSV or attach the Excel file.")
    if ext in (".xlsx", ".xlsm", ".xls"):
        frames = pd.read_excel(p, sheet_name=None)
    elif ext == ".json":
        frames = {"data": pd.read_json(p)}
    else:
        frames = {"data": pd.read_csv(p, sep=None, engine="python", nrows=MAX_ROWS)}
    frames = {str(k): v.head(MAX_ROWS) for k, v in frames.items() if len(v.columns) and len(v)}
    if not frames:
        raise ValueError("The file has no rows of data.")
    return frames


def find_date_column(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    for c in df.columns:
        if is_text(df[c]) and DATE_WORDS.search(str(c)):
            parsed = pd.to_datetime(df[c], errors="coerce", format="mixed")
            if parsed.notna().mean() >= 0.8:
                return c
    return None


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]) and not ID_WORDS.search(str(c))
            and df[c].nunique(dropna=True) > 1]


def category_columns(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        if is_text(df[c]):
            n = df[c].nunique(dropna=True)
            if 2 <= n <= 30 and not ID_WORDS.search(str(c)):
                out.append(c)
    return out


def main_metric(df: pd.DataFrame, goal: str = "") -> Optional[str]:
    nums = numeric_columns(df)
    if not nums:
        return None
    for c in nums:  # the user named it
        if str(c).lower() in (goal or "").lower():
            return c
    for rx in METRIC_PRIORITY:  # the business outcome first (revenue), the levers after it (spend), generic words last
        for c in nums:
            if rx.search(str(c)):
                return c
    return nums[0]


def fmt(v: float) -> str:
    """Plain numbers with thousands separators (no lakh/crore or K/M shorthand: it is easy to misread, for people and for models)."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:,.1f}".rstrip("0").rstrip(".")
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def quality(df: pd.DataFrame) -> dict:
    rows = []
    issues: list[str] = []
    for c in df.columns:
        s = df[c]
        miss = int(s.isna().sum())
        note = ""
        if is_text(s):
            num = pd.to_numeric(s.dropna().astype(str).str.replace(r"[,$₹€£%\s]", "", regex=True), errors="coerce")
            if len(num) and num.notna().mean() > 0.9 and not ID_WORDS.search(str(c)):
                note = "stored as text but looks numeric"
                issues.append(f"Column “{c}” is stored as text but looks numeric.")
            elif s.dropna().astype(str).str.strip().ne(s.dropna().astype(str)).any():
                note = "has leading/trailing spaces"
        elif s.nunique(dropna=True) <= 1 and len(s) > 1:
            note = "constant column"
        if re.search(r"qty|quantity|price|amount|revenue|sales|units", str(c), re.I) and pd.api.types.is_numeric_dtype(s) and (s < 0).any():
            note = (note + "; " if note else "") + f"{int((s < 0).sum())} negative values"
            issues.append(f"Column “{c}” has {int((s < 0).sum())} negative values (returns or errors?).")
        rows.append({"column": str(c), "type": str(s.dtype), "missing_pct": round(100 * miss / max(len(df), 1), 1), "unique": int(s.nunique(dropna=True)), "note": note})
        if miss / max(len(df), 1) > 0.2:
            issues.append(f"Column “{c}” is {round(100 * miss / len(df))}% empty.")
    dups = int(df.duplicated().sum())
    if dups:
        issues.append(f"{dups:,} exact duplicate rows ({round(100 * dups / len(df), 1)}%).")
    return {"rows": rows, "duplicates": dups, "issues": issues, "missing_cells": int(df.isna().sum().sum())}


def outliers(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = []
    for c in cols[:8]:
        s = df[c].dropna()
        if len(s) < 20:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        bad = s[(s < lo) | (s > hi)]
        if len(bad):
            top = bad.abs().sort_values(ascending=False).head(3)
            out.append({"column": str(c), "count": int(len(bad)), "pct": round(100 * len(bad) / len(s), 1), "low": float(lo), "high": float(hi),
                        "examples": [float(s.loc[i]) for i in top.index]})
    return out


def correlations(df: pd.DataFrame, cols: list[str], top: int = 5) -> list[dict]:
    cols = cols[:12]
    if len(cols) < 2:
        return []
    cm = df[cols].corr(numeric_only=True)
    pairs = []
    for i, a in enumerate(cm.columns):
        for b in cm.columns[i + 1:]:
            r = cm.loc[a, b]
            if pd.notna(r):
                pairs.append({"a": str(a), "b": str(b), "r": round(float(r), 3)})
    pairs.sort(key=lambda p: -abs(p["r"]))
    return pairs[:top]


def strength(r: float) -> str:
    a = abs(r)
    return "very strong" if a >= 0.8 else "strong" if a >= 0.6 else "moderate" if a >= 0.4 else "weak" if a >= 0.2 else "negligible"


def trend(df: pd.DataFrame, date_col: str, metric: str, how: str = "sum") -> Optional[dict]:
    d = df[[date_col, metric]].copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce", format="mixed")
    d = d.dropna()
    if len(d) < 6:
        return None
    span = (d[date_col].max() - d[date_col].min()).days
    rule = "MS" if span > 75 else "W" if span > 21 else "D"
    g = d.set_index(date_col)[metric].resample(rule).agg(how if how in ("sum", "mean") else "sum")
    g = g[g.index.notna()]
    partial = []
    if rule == "MS" and len(g) >= 5 and how == "sum":  # a sum over a half-filled first/last month looks like a collapse or a surge
        lo, hi = d[date_col].min(), d[date_col].max()
        if lo.day > 5:
            g, partial = g.iloc[1:], partial + [lo.strftime("%Y-%m")]
        if hi.day < hi.days_in_month - 4:
            g, partial = g.iloc[:-1], partial + [hi.strftime("%Y-%m")]
    if len(g) < 3:
        return None
    y = g.values.astype(float)
    x = np.arange(len(y))
    slope = float(np.polyfit(x, y, 1)[0]) if len(y) > 1 else 0.0
    first, last = float(y[0]), float(y[-1])
    chg = (last - first) / abs(first) * 100 if first else None
    pct = np.diff(y) / np.where(y[:-1] == 0, np.nan, np.abs(y[:-1]))
    z = (pct - np.nanmean(pct)) / (np.nanstd(pct) or 1)
    spikes = [{"period": g.index[i + 1].strftime("%Y-%m-%d" if rule != "MS" else "%Y-%m"), "change_pct": round(float(pct[i] * 100), 1)}
              for i in range(len(pct)) if not np.isnan(z[i]) and abs(z[i]) > 2.2][:4]
    fmtp = "%Y-%m" if rule == "MS" else "%Y-%m-%d"
    return {"labels": [i.strftime(fmtp) for i in g.index], "values": [round(float(v), 2) for v in y], "rule": {"MS": "month", "W": "week", "D": "day"}[rule],
            "partial_excluded": partial, "direction": "up" if slope > 0 else "down" if slope < 0 else "flat", "change_pct": None if chg is None else round(chg, 1),
            "peak": {"period": g.idxmax().strftime(fmtp), "value": float(g.max())}, "low": {"period": g.idxmin().strftime(fmtp), "value": float(g.min())}, "spikes": spikes}


def by_category(df: pd.DataFrame, cat: str, metric: str, how: str = "sum", top: int = 10) -> Optional[dict]:
    g = df.groupby(cat, dropna=True)[metric].agg(how if how in ("sum", "mean") else "sum").sort_values(ascending=False)
    if len(g) < 2:
        return None
    tot = float(g.sum()) if how == "sum" else None
    head = g.head(top)
    return {"labels": [str(i) for i in head.index], "values": [round(float(v), 2) for v in head.values],
            "top_share": round(float(head.iloc[0]) / tot * 100, 1) if tot else None, "n": int(len(g)), "how": how}


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    log = []
    d = df.copy()
    before = len(d)
    d = d.dropna(how="all").dropna(axis=1, how="all")
    for c in d.columns:
        if is_text(d[c]):
            d[c] = d[c].astype(str).where(d[c].notna(), None).map(lambda v: v.strip() if isinstance(v, str) else v)
    dc = find_date_column(d)
    if dc and not pd.api.types.is_datetime64_any_dtype(d[dc]):
        d[dc] = pd.to_datetime(d[dc], errors="coerce", format="mixed")
        log.append(f"Parsed “{dc}” as dates.")
    n = int(d.duplicated().sum())
    if n:
        d = d.drop_duplicates()
        log.append(f"Removed {n:,} exact duplicate rows.")
    if len(d) != before - n:
        log.append(f"Dropped {before - n - len(d):,} completely empty rows.")
    return d, log


def _find_cols_in_text(df: pd.DataFrame, text: str) -> list[str]:
    found = []
    low = (text or "").lower()
    for c in numeric_columns(df):
        name = str(c).lower().replace("_", " ")
        if str(c).lower() in low or name in low:
            found.append(c)
    return found


def relationship(df: pd.DataFrame, goal: str) -> Optional[dict]:
    """If the goal names two numeric columns ("advertising expenditure and sales"), measure how they move together."""
    cols = _find_cols_in_text(df, goal)
    if len(cols) < 2:  # partial-word match: "advertising" -> "Advertising_Spend"
        toks = [t for t in re.findall(r"[a-z]{4,}", (goal or "").lower())]
        for c in numeric_columns(df):
            if c not in cols and any(t in str(c).lower() for t in toks):
                cols.append(c)
    if len(cols) < 2:
        return None
    a, b = cols[0], cols[1]
    d = df[[a, b]].dropna()
    if len(d) < 8:
        return None
    r = float(d[a].corr(d[b]))
    slope, intercept = np.polyfit(d[a].astype(float), d[b].astype(float), 1)
    pts = d.sample(min(len(d), 400), random_state=1)
    return {"x": str(a), "y": str(b), "r": round(r, 3), "r2": round(r * r, 3), "strength": strength(r), "n": int(len(d)), "slope": float(slope),
            "points": [{"x": float(x), "y": float(y)} for x, y in zip(pts[a], pts[b])]}
