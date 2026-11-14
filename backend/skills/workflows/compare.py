"""/compare - side-by-side comparison with explicit criteria.

Items come from the user, or are found on the web when only a category is named ("smartphones under 30000"). Criteria and their weights come
from the user's priorities. Every value must be found in a page the app actually read (otherwise it shows '-'), and the score is computed
by code from the numeric criteria only, with the criteria, weights and coverage shown. The model never invents a score.
"""
from __future__ import annotations

import re

from .. import common, questions
from ..registry import Slot, Workflow, register
from ... import charts

KIND_RX = {
    "product": re.compile(r"\b(?:phones?|smartphones?|laptops?|gpus?|rtx|radeon|cameras?|tvs?|headphones?|earbuds?|watch(?:es)?|tablets?|cars?|bikes?|scooters?|processors?|cpus?|iphone|galaxy|pixel|macbook)\b", re.I),
    "education": re.compile(r"\b(?:universit\w+|colleges?|degree|master'?s|bachelor'?s|mba|phd|courses?|schools?|programs?|programmes?)\b", re.I),
    "job": re.compile(r"\b(?:job offers?|offers?|salary|salaries|employers?|jobs?|companies|company)\b", re.I),
}
DEFAULT_CRITERIA = {
    "product": [("Price", "", False), ("Performance", "", True), ("Battery / efficiency", "", True), ("Display / build", "", None), ("Warranty", "", None)],
    "education": [("Tuition fee per year", "", False), ("Programme length", "", False), ("Ranking", "", None), ("Location", "", None), ("Scholarships", "", None)],
    "job": [("Salary", "", True), ("Location", "", None), ("Benefits", "", None), ("Growth opportunities", "", None), ("Work hours / flexibility", "", None)],
    "generic": [("Cost", "", False), ("Key features", "", None), ("Main advantage", "", None), ("Main drawback", "", None)],
}
_SPLIT = re.compile(r"\s*(?:,|;|\bvs\.?\b|\bversus\b|\band\b|\bor\b|\bagainst\b)\s*", re.I)


def prefill(text: str, ctx: dict) -> dict:
    out: dict = {}
    t = text.strip()
    m = re.search(r"\bcompare\s+(.+?)(?:\s+(?:for|on|in terms of|based on|by|with respect to)\s+(.+))?$", t, re.I)
    body = (m.group(1) if m else t).strip(" .?")
    # "A vs B" / "A, B and C": only when it really lists named things (not "three universities for a master's degree")
    if re.search(r"\bvs\.?\b|\bversus\b", body, re.I) or (re.search(r",|\band\b", body, re.I) and not re.match(r"(?:two|three|four|\d+|some|the best|top)\b", body, re.I)
                                                          and not re.search(r"\b(?:under|below|within|around|best|top|cheap)\b", body, re.I)):
        parts = [p.strip(" .") for p in _SPLIT.split(body) if p.strip(" .")]
        parts = [re.sub(r"^(?:the|these|those|my)\s+", "", p, flags=re.I) for p in parts]
        if 2 <= len(parts) <= 6 and all(len(p.split()) <= 8 for p in parts):
            out["items"] = parts
    if m and m.group(2):
        out["priorities"] = m.group(2).strip()
    out["topic"] = body
    cur = questions.currency_of(t)
    if cur:
        out["currency"] = cur
    b = re.search(r"(?:under|below|within|upto|up to|budget of|budget)\s*(?:₹|rs\.?|inr|\$|usd|€|£)?\s*([\d,\.]+\s*(?:k|lakhs?|l|cr)?)", t, re.I)
    if b:
        out["budget"] = b.group(1).strip()
    return out


def _kind(text: str) -> str:
    for k, rx in KIND_RX.items():
        if rx.search(text or ""):
            return k
    return "generic"


async def run(rc) -> dict:
    s = rc.slots
    topic = str(s.get("topic") or "")
    items = [str(i) for i in (s.get("items") or [])]
    priorities = "" if s.get("priorities") in (None, "flexible") else str(s.get("priorities"))
    budget_raw = "" if s.get("budget") in (None, "flexible") else str(s.get("budget"))
    budget = questions.parse_number(budget_raw) if budget_raw else None
    cur = s.get("currency") or questions.currency_of(budget_raw + " " + topic) or ""
    kind = _kind(" ".join(items) + " " + topic)
    maxn = int(rc.s.get("skill_max_items") or 4)
    steps = 4 + min(len(items) or maxn, maxn)
    step = 0

    # 1) find the options when only a category was named
    if len(items) < 2:
        step += 1
        await rc.progress(step, steps, f"Finding candidates for “{topic[:50]}”")
        pages = await rc.research(f"best {topic} comparison", pages=2)
        text = "\n\n".join(p["text"] or p["snippet"] for p in pages)
        found = await common.extract_items(rc, text, f"List the specific {topic} options that are being recommended or compared (real product / institution / company names).",
                                           {"name": "the exact name"}, ["name"], limit=maxn + 2)
        items = list(dict.fromkeys(i["name"] for i in found if i.get("name")))[:maxn]
    items = items[:maxn]
    if len(items) < 2:
        return {"error": "I couldn't identify at least two things to compare. Tell me which ones (for example “A and B”)."}
    kind = _kind(" ".join(items) + " " + topic) if kind == "generic" else kind

    # 2) criteria and weights from the user's priorities
    step += 1
    await rc.progress(step, steps, "Choosing comparison criteria")
    defaults = DEFAULT_CRITERIA[kind]
    data = await rc.json_call(
        f"Choose 5 to 7 criteria for comparing: {', '.join(items)} (kind: {kind}). The user's priorities: {priorities or 'not stated'}. "
        f"Budget: {budget_raw or 'not stated'}. Include a price/cost criterion when relevant. For each criterion say whether it is a plain number "
        "(numeric true/false), the unit, and whether higher is better (true/false/null if it is not numeric), and a weight 1-5 reflecting the priorities. "
        'Reply with JSON only: {"criteria": [{"name": "", "unit": "", "numeric": true, "higher_better": true, "weight": 3}]}', max_tokens=500)
    crit = []
    for c in (data.get("criteria") if isinstance(data, dict) else None) or []:
        if isinstance(c, dict) and str(c.get("name", "")).strip():
            w = c.get("weight")
            crit.append({"name": str(c["name"]).strip()[:40], "unit": str(c.get("unit") or "")[:12], "numeric": bool(c.get("numeric")),
                         "higher_better": c.get("higher_better") if isinstance(c.get("higher_better"), bool) else None,
                         "weight": int(w) if isinstance(w, (int, float)) and 1 <= w <= 5 else 3})
    if len(crit) < 3:
        rc.notes.append("The model did not return usable criteria, so standard criteria for this kind of comparison were used.")
        crit = [{"name": n, "unit": u, "numeric": hb is not None, "higher_better": hb, "weight": 3} for n, u, hb in defaults]
    crit = crit[:7]

    # 3) research every item and read the values off the pages (a second, targeted search for what is still missing)
    async def read_values(item: str, text: str, wanted: list[dict]) -> dict:
        if not text.strip() or not wanted:
            return {}
        listing = "\n".join(f'- "{c["name"]}"' + (f' (number, unit {c["unit"]})' if c["numeric"] and c["unit"] else " (number)" if c["numeric"] else "") for c in wanted)
        d = await rc.json_call(
            f'From the text, give the value of each criterion for "{item}". Copy the value exactly as written in the text (with its unit); use null when the text '
            f'does not state it for this exact item. Reply with JSON only: {{"values": {{"criterion": "value or null"}}}}\n\nCRITERIA:\n{listing}\n\nTEXT:\n{text[:7500]}', max_tokens=700)
        raw = d.get("values") if isinstance(d, dict) else {}
        tn = common.verify._norm(text)
        return {c["name"]: str(raw[c["name"]])[:80] for c in wanted if (raw or {}).get(c["name"]) not in (None, "", "null") and common._in_text(raw[c["name"]], tn)}

    cols: list[dict] = []
    for item in items:
        step += 1
        await rc.progress(step, steps, f"Researching {item}")
        hint = {"product": "specifications price review", "education": "tuition fees ranking programme", "job": "salary benefits", "generic": "features price"}[kind]
        pages = await rc.research(f"{item} {hint}", pages=2, focus=" ".join(c["name"] for c in crit))
        text = "\n\n".join(p["text"] or p["snippet"] for p in pages)
        values = await read_values(item, text, crit)
        gaps = [c for c in crit if c["name"] not in values]
        if len(values) < max(2, len(crit) // 2) and gaps:  # thin result: one more search aimed at what is missing
            more = await rc.research(f"{item} " + " ".join(c["name"] for c in gaps[:4]), pages=2, focus=" ".join(c["name"] for c in gaps))
            extra = await read_values(item, "\n\n".join(p["text"] or p["snippet"] for p in more), gaps)
            values.update(extra)
            pages += more
        url = next((p["url"] for p in pages if p.get("text")), pages[0]["url"] if pages else "")
        cols.append({"item": item, "values": values, "url": url})
        if not values:
            rc.notes.append(f"No reliable figures were found for {item}; it is shown with gaps instead of estimates.")

    # 4) scores: numeric criteria only, min-max across the items that have a value, explicit weights and coverage
    step += 1
    await rc.progress(step, steps, "Scoring")
    scored = [c for c in crit if c["numeric"] and c["higher_better"] is not None]
    nums = {c["name"]: [common.parse_price(col["values"].get(c["name"])) for col in cols] for c in scored}
    rows = []
    for c in crit:
        vals = [col["values"].get(c["name"]) for col in cols]
        best = None
        if c["name"] in nums and sum(v is not None for v in nums[c["name"]]) >= 2:
            seq = nums[c["name"]]
            pick = max if c["higher_better"] else min
            bv = pick(v for v in seq if v is not None)
            best = seq.index(bv)
        rows.append({"name": c["name"], "unit": c["unit"], "weight": c["weight"], "direction": ("higher is better" if c["higher_better"] else "lower is better" if c["higher_better"] is False else "not scored"),
                     "values": vals, "best": best, "numeric": c["name"] in nums})
    scores = []
    for i, col in enumerate(cols):
        tot, wsum, used = 0.0, 0, 0
        for c in scored:
            seq = nums[c["name"]]
            have = [v for v in seq if v is not None]
            if seq[i] is None or len(have) < 2 or max(have) == min(have):
                continue
            norm = (seq[i] - min(have)) / (max(have) - min(have))
            tot += c["weight"] * (norm if c["higher_better"] else 1 - norm)
            wsum += c["weight"]
            used += 1
        scores.append({"item": col["item"], "pct": round(100 * tot / wsum) if wsum and used >= 2 else None, "criteria_used": used})
    ranked = sorted([x for x in scores if x["pct"] is not None], key=lambda x: -x["pct"])

    # 5) strengths / weaknesses from the numbers, never from the model's opinion
    pc = []
    for i, col in enumerate(cols):
        pros = [f'Best {r["name"].lower()}: {r["values"][i]}' for r in rows if r["best"] == i and r["values"][i]]
        cons = []
        for c in scored:
            seq = nums[c["name"]]
            have = [v for v in seq if v is not None]
            if seq[i] is not None and len(have) >= 2 and seq[i] == (min(have) if c["higher_better"] else max(have)) and max(have) != min(have):
                cons.append(f'Weakest {c["name"].lower()}: {col["values"].get(c["name"])}')
        pc.append({"item": col["item"], "pros": pros[:4], "cons": cons[:4]})

    blocks = [{"type": "overview", "title": f"Comparing {', '.join(items)}", "text": (f"Priorities: {priorities}. " if priorities else "") + (f"Budget: {budget_raw}. " if budget_raw else "")
               + f"{len(cols)} options, {len(crit)} criteria.", "kpis": [{"label": "Options", "value": len(cols)}, {"label": "Criteria", "value": len(crit)},
                                                                      {"label": "Scored criteria", "value": len(scored)}]},
              {"type": "comparison", "items": [c["item"] for c in cols], "urls": [c["url"] for c in cols], "rows": rows,
               "explain": "Scores use only criteria that are plain numbers. Each is scaled from 0 (worst of the options) to 1 (best), multiplied by its weight (1-5, from your priorities) and averaged. "
                          "Criteria with no figure for an option are skipped for that option, so look at the coverage column."},
              {"type": "scores", "rows": scores}]
    if len(ranked) >= 2:
        try:
            spec, _ = charts.build({"type": "horizontal_bar_chart", "title": "Weighted score (%)", "labels": [x["item"] for x in ranked], "values": [x["pct"] for x in ranked]})
            blocks.append({"type": "chart", "spec": spec})
        except charts.ChartError:
            pass
    blocks.append({"type": "proscons", "rows": pc})
    price_rows = [r for r in rows if re.search(r"price|cost|fee|tuition|salary", r["name"], re.I)]
    if price_rows and budget:
        pr = price_rows[0]
        cost = [{"item": col["item"], "value": common.parse_price(pr["values"][i]), "text": pr["values"][i]} for i, col in enumerate(cols)]
        blocks.append({"type": "table", "title": f"{pr['name']} against your budget ({common.fmt_money(budget, cur)})", "columns": ["Option", pr["name"], "Difference to budget"],
                       "rows": [[c["item"], c["text"] or "—", ("—" if c["value"] is None else common.fmt_money(c["value"] - budget, cur) + (" over" if c["value"] > budget else " under"))] for c in cost]})
    win = ranked[0] if ranked else None
    rec = (f"Best fit for your stated priorities: {win['item']} ({win['pct']}% weighted score on {win['criteria_used']} scored criteria)." if win
           else "Not enough comparable numbers were found to compute a fair score (a score needs at least two criteria with figures for the options); judge from the table and the notes below.")
    blocks.append({"type": "recommendation", "text": rec, "caveat": "Figures come from the pages listed in Sources and may be out of date; check the seller or institution before deciding."})

    facts = [f"Options: {', '.join(items)}.", f"User priorities: {priorities or 'not stated'}; budget: {budget_raw or 'not stated'}.", rec]
    for r in rows:
        facts.append(f"{r['name']} ({r['direction']}, weight {r['weight']}): " + "; ".join(f"{cols[i]['item']} = {v or 'not found'}" for i, v in enumerate(r["values"])))
    facts += [f"{p['item']}: " + ("; ".join(p["pros"] + p["cons"]) or "no standout numbers") for p in pc]
    return {"blocks": blocks, "facts": facts, "summary": f"Compared {len(cols)} options on {len(crit)} criteria",
            "what": "a side-by-side table, weighted scores and strengths/weaknesses", "size": "5-8 sentences: the recommendation first, then the main trade-offs and what to check"}


register(Workflow(
    id="compare", skill="compare", title="Comparison", description="Compare products, institutions, offers or any options on explicit criteria",
    keywords=re.compile(r"compar|vs\.?|versus|which is better|better than|difference", re.I), general=True, uses_web=True, prefill=prefill, run=run,
    slots=[
        Slot("topic", "Topic", "What is being compared?", required=False),
        Slot("items", "Options", "Which options should I compare? (2-4, separated by commas)", kind="list", required=False, hint="Leave empty and I will find the main candidates",
             ask_if=lambda s: not s.get("items") and bool(re.search(r"\b(?:these|this|my|attached|both|the two|offers?)\b", str(s.get("topic") or ""), re.I))),
        Slot("priorities", "Priorities", "What matters most to you in this decision?", hint="e.g. price, performance, battery life, location, career growth"),
        Slot("budget", "Budget", "Do you have a budget or price limit?", required=True, pref="compare.budget",
             ask_if=lambda s: bool(re.search(r"\b(?:phones?|smartphones?|laptops?|gpus?|cameras?|tvs?|cars?|bikes?|headphones?|buy|price|cheap|under|offer)\b", str(s.get("topic") or ""), re.I)) and not s.get("budget")),
        Slot("currency", "Currency", "", required=False),
    ]))
