"""/planning -> travel: a day-by-day trip plan with a map, transport options, hotels, a budget and booking links.

What is measured vs. found vs. assumed is kept apart:
  measured  - distances and drive times between real, geocoded places (OpenStreetMap routing; straight-line estimate when it is unreachable)
  found     - transport options, hotels and attractions read from web pages the app opened (each name must occur in the page text)
  assumed   - the budget split. It is an allocation of the user's own budget, not a price quote, and is labelled as such.
Nothing is booked or paid: links open the provider's own search page, pre-filled."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

from .. import common, questions
from ..registry import Slot, Workflow, register
from ... import geo

ALLOCATION = {"transport": 0.30, "stay": 0.35, "food": 0.20, "activities": 0.10, "buffer": 0.05}
STAY_TIER = {"budget": "budget hotels hostels", "mid-range": "mid-range hotels", "luxury": "luxury hotels resorts"}


def prefill(text: str, ctx: dict) -> dict:
    out: dict = {}
    m = re.search(r"\b(\d{1,2})[- ]?(?:day|days|night|nights)\b", text, re.I)
    if m:
        out["days"] = m.group(1)
    d = re.search(r"\b(?:trip|tour|vacation|holiday|visit|travel|journey|getaway|itinerary)\s+(?:plan\s+)?(?:to|for|in)\s+([A-Z][\w .'\-]{1,40}?)(?=\s+(?:from|for|in|on|with|under|between|next|this)\b|[.,?!]|$)", text) \
        or re.search(r"\b(?:to|visit|in)\s+([A-Z][\w .'\-]{1,40}?)(?=\s+(?:from|for|in|on|with|under|between|next|this)\b|[.,?!]|$)", text)
    if d:
        out["destination"] = d.group(1).strip()
    o = re.search(r"\bfrom\s+([A-Z][\w .'\-]{1,40}?)(?=\s+(?:to|on|for|with|in|under|between|next|this)\b|[.,?!]|$)", text)
    if o:
        out["origin"] = o.group(1).strip()
    p = re.search(r"\b(\d{1,2})\s*(?:people|persons|adults|travell?ers|friends|of us)\b", text, re.I)
    if p:
        out["travellers"] = p.group(1)
    elif re.search(r"\bsolo\b|\balone\b|\bby myself\b", text, re.I):
        out["travellers"] = "1"
    elif re.search(r"\bcouple\b|\bhoneymoon\b", text, re.I):
        out["travellers"] = "2"
    b = re.search(r"(?:budget(?: of)?|under|within|upto|up to)\s*((?:₹|rs\.?|inr|\$|usd|€|£)?\s*[\d,\.]+\s*(?:k|lakhs?|l|cr)?)", text, re.I)
    if b:
        out["budget"] = b.group(1).strip()
    ds = questions.parse_dates(text)
    if ds:
        out["dates"] = {"start": ds[0].isoformat(), "end": ds[-1].isoformat()}
    for mode in ("train", "flight", "bus"):
        if re.search(rf"\b{mode}s?\b", text, re.I):
            out["transport"] = mode
    return out


def _clean_names(items: list[dict], key: str = "name") -> list[dict]:
    seen, out = set(), []
    for it in items:
        n = str(it.get(key) or "").strip()
        if n and n.lower() not in seen and len(n) < 70:
            seen.add(n.lower())
            out.append(it)
    return out


def chunk(seq: list, n: int) -> list[list]:
    """Split into n consecutive groups of near-equal size (kept in order so each day stays geographically coherent)."""
    n = max(1, n)
    size = math.ceil(len(seq) / n) if seq else 0
    return [seq[i * size:(i + 1) * size] for i in range(n) if seq[i * size:(i + 1) * size]]


async def run(rc) -> dict:
    s = rc.slots
    dest = str(s["destination"]).strip()
    origin = str(s.get("origin") or "").strip()
    flex = lambda v: v in (None, "", "flexible")
    days = int(float(s.get("days") or 3))
    days = max(1, min(days, 14))
    trav = max(1, int(float(s.get("travellers") or 2))) if not flex(s.get("travellers")) else 2
    budget_raw = "" if flex(s.get("budget")) else str(s["budget"])
    budget = questions.parse_number(budget_raw) if budget_raw else None
    cur = questions.currency_of(budget_raw) or ("INR" if re.search(r"india|goa|delhi|mumbai|pune|kerala|jaipur|bengal|chennai|hyderabad|manali|shimla", dest + " " + origin, re.I) else "")
    mode_pref = str(s.get("transport") or "flexible").lower()
    stay_pref = str(s.get("stay") or "mid-range").lower() if not flex(s.get("stay")) else "mid-range"
    dts = s.get("dates") if isinstance(s.get("dates"), dict) else {}
    start = date.fromisoformat(dts["start"]) if dts.get("start") else None
    end = date.fromisoformat(dts["end"]) if dts.get("end") and dts.get("end") != dts.get("start") else (start + timedelta(days=days - 1) if start else None)
    if start and end and (end - start).days + 1 != days and dts.get("end") and dts.get("end") != dts.get("start"):
        days = max(1, min((end - start).days + 1, 14))
    steps = 8
    notes: list[str] = []

    # 1) real places and distances
    await rc.progress(1, steps, f"Locating {dest}")
    g_dest = await geo.geocode(dest)
    g_org = await geo.geocode(origin) if origin and not flex(origin) else None
    leg = None
    if g_dest and g_org:
        leg = await geo.route([(g_org["lat"], g_org["lon"]), (g_dest["lat"], g_dest["lon"])])
    elif not g_dest:
        notes.append(f"“{dest}” could not be located on the map, so there is no route or distance information.")

    # 2) transport options found online
    await rc.progress(2, steps, f"Researching transport {origin} → {dest}")
    t_items: list[dict] = []
    if origin and not flex(origin):
        pages = await rc.research(f"{origin} to {dest} train flight bus options travel time fare", pages=2)
        text = "\n\n".join(p["text"] or p["snippet"] for p in pages)
        t_items = await common.extract_items(rc, text, f"List transport options between {origin} and {dest} (trains, flights, buses).",
                                             {"mode": '"train", "flight" or "bus"', "name": "operator, train or flight name/number", "duration": "travel time", "price": "fare/price"},
                                             ["name"], limit=6)
        t_items = [t for t in _clean_names(t_items) if t.get("mode") and str(t["mode"]).lower() in ("train", "flight", "bus")]
    # 3) hotels
    await rc.progress(3, steps, f"Finding {stay_pref} hotels in {dest}")
    pages = await rc.research(f"best {STAY_TIER.get(stay_pref, 'hotels')} in {dest} price per night", pages=2)
    h_items = _clean_names(await common.extract_items(rc, "\n\n".join(p["text"] or p["snippet"] for p in pages), f"List hotels or stays in {dest}.",
                                                       {"name": "hotel name", "price": "price per night", "area": "neighbourhood or area"}, ["name"], limit=5))
    # 4) attractions
    await rc.progress(4, steps, f"Collecting things to do in {dest}")
    pages = await rc.research(f"top things to do in {dest} attractions travel guide", pages=3)
    wv = await rc.research(f"{dest} wikivoyage see do", pages=1)
    want = days * 3 + 2
    a_items = _clean_names(await common.extract_items(rc, "\n\n".join(p["text"] or p["snippet"] for p in pages + wv),
                                                       f"List the tourist attractions, beaches, sights and notable places to visit in or near {dest}.",
                                                       {"name": "place name", "type": "beach, fort, temple, market, museum, park, viewpoint...", "area": "area or town"}, ["name"], limit=want + 4, max_tokens=1100))
    # 5) coordinates (each name must resolve to a real place; unknown ones are dropped, never guessed)
    await rc.progress(5, steps, "Placing everything on the map")
    placed = []
    for it in a_items[: want + 2]:
        gg = await geo.geocode(it["name"], dest)
        if gg and (not g_dest or geo.haversine_km((gg["lat"], gg["lon"]), (g_dest["lat"], g_dest["lon"])) < 150):
            placed.append({**it, "lat": gg["lat"], "lon": gg["lon"]})
    placed = placed[:want]
    hotels_placed = []
    for h in h_items[:3]:
        gg = await geo.geocode(h["name"], dest)
        if gg and (not g_dest or geo.haversine_km((gg["lat"], gg["lon"]), (g_dest["lat"], g_dest["lon"])) < 80):
            hotels_placed.append({**h, "lat": gg["lat"], "lon": gg["lon"]})
    if len(placed) < days:
        notes.append(f"Only {len(placed)} attraction(s) could be verified on the map, so some days are light. Use Revise or ask for more ideas.")

    # 6) days: keep nearby places together, then measure each day's route
    await rc.progress(6, steps, "Planning each day")
    anchor = hotels_placed[0] if hotels_placed else (g_dest and {"lat": g_dest["lat"], "lon": g_dest["lon"]})
    pts = geo.order_nearest(placed, 0) if placed else []
    if pts and anchor:
        first = min(range(len(pts)), key=lambda i: geo.haversine_km((anchor["lat"], anchor["lon"]), (pts[i]["lat"], pts[i]["lon"])))
        pts = geo.order_nearest(pts, first)
    day_groups = chunk(pts, days)
    times = ["09:30", "13:00", "16:30", "19:00"]
    itin, map_points, ev_ics = [], [], []
    total_km = 0.0
    for di, grp in enumerate(day_groups):
        r = await geo.route([(p["lat"], p["lon"]) for p in grp]) if len(grp) > 1 else {"km": 0.0, "minutes": 0, "method": "none", "legs": []}
        total_km += r["km"]
        items = []
        for si, p in enumerate(grp):
            km = r["legs"][si - 1]["km"] if si > 0 and si - 1 < len(r["legs"]) else None
            items.append({"time": times[min(si, len(times) - 1)], "title": p["name"], "note": " · ".join(filter(None, [str(p.get("type") or ""), str(p.get("area") or "")])), "km": km, "link": common.link_maps(f"{p['name']} {dest}")})
            map_points.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "group": f"Day {di + 1}", "note": str(p.get("type") or ""), "n": len(map_points) + 1})
            if start:
                ev_ics.append({"title": p["name"], "start": datetime.combine(start + timedelta(days=di), datetime.strptime(times[min(si, len(times) - 1)], "%H:%M").time()),
                               "end": datetime.combine(start + timedelta(days=di), datetime.strptime(times[min(si, len(times) - 1)], "%H:%M").time()) + timedelta(hours=2), "location": f"{p['name']}, {dest}"})
        label = (start + timedelta(days=di)).strftime("%a %d %b") if start else ""
        itin.append({"title": f"Day {di + 1}" + (f": {grp[0].get('area') or dest}" if grp else ""), "date": label, "items": items,
                     "summary": (f"about {r['km']} km, {r['minutes']} min driving ({'estimate' if r['method'] == 'straight-line' else 'OpenStreetMap routing'})" if r["km"] else "")})
    for hp in hotels_placed:
        map_points.append({"name": hp["name"], "lat": hp["lat"], "lon": hp["lon"], "group": "Hotel", "note": str(hp.get("price") or ""), "n": len(map_points) + 1})

    # 7) budget: what the user can spend (allocation) next to what was found online
    await rc.progress(7, steps, "Estimating costs")
    buf = float(rc.pref("budget.buffer_pct", 0) or 0) / 100
    alloc = {k: v for k, v in ALLOCATION.items()}
    if buf:
        alloc["buffer"] = min(0.25, alloc["buffer"] + buf)
        scale = sum(alloc.values())
        alloc = {k: v / scale for k, v in alloc.items()}
    transport_found = None
    cand = [t for t in t_items if mode_pref in ("flexible", "") or str(t["mode"]).lower() == mode_pref] or t_items
    prices = [common.parse_price(t.get("price")) for t in cand if common.parse_price(t.get("price"))]
    if prices:
        transport_found = min(prices) * trav * 2
    stay_found = None
    hp = [common.parse_price(h.get("price")) for h in h_items if common.parse_price(h.get("price"))]
    rooms = math.ceil(trav / 2)
    if hp:
        stay_found = sorted(hp)[len(hp) // 2] * max(1, days - 1) * rooms
    rows = []
    for key, label, found, note in (("transport", "Transport (round trip)", transport_found, f"cheapest {mode_pref if mode_pref != 'flexible' else 'option'} found × {trav} × 2" if transport_found else "no price found online: check the links"),
                                    ("stay", f"Stay ({max(1, days - 1)} nights, {rooms} room(s))", stay_found, "median hotel price found online" if stay_found else "no hotel price found: check the links"),
                                    ("food", "Food & drinks", None, "allocation only"), ("activities", "Activities & entry fees", None, "allocation only"), ("buffer", "Buffer for surprises", None, "allocation only")):
        rows.append({"label": label, "planned": common.fmt_money(budget * alloc[key], cur) if budget else None, "found": common.fmt_money(found, cur) if found else None, "note": note})
    total_found = sum(x for x in (transport_found, stay_found) if x)
    budget_block = {"type": "budget", "title": "Budget: your plan vs. prices found online", "rows": rows,
                    "total": {"planned": common.fmt_money(budget, cur) if budget else "—", "found": common.fmt_money(total_found, cur) + " (transport + stay only)" if total_found else ""},
                    "note": "“Planned” splits YOUR budget across categories (an assumption, not a quote). “Found online” are prices that appeared on the pages read; they change with dates and availability."}
    if budget and total_found and total_found > budget * (alloc["transport"] + alloc["stay"]) * 1.05:
        notes.append("Prices found for transport and stay are above the share of your budget planned for them: consider other dates, a cheaper transport mode or a lower hotel tier.")

    # 8) blocks and files
    await rc.progress(8, steps, "Putting the plan together")
    ci, co = (start.isoformat() if start else ""), (end.isoformat() if end else "")
    kpis = [{"label": "Days", "value": days}, {"label": "Travellers", "value": trav}]
    if leg:
        kpis.append({"label": f"{origin} → {dest}", "value": f"{leg['km']:.0f} km", "sub": f"{leg['minutes'] // 60}h {leg['minutes'] % 60}m by road" + ("" if leg["method"] == "osrm" else " (estimate)")})
    if budget:
        kpis.append({"label": "Budget", "value": common.fmt_money(budget, cur)})
    if total_km:
        kpis.append({"label": "Local driving", "value": f"≈ {total_km:.0f} km"})
    blocks: list[dict] = [{"type": "overview", "title": f"{days}-day trip to {dest}", "text": (f"From {origin}. " if origin and not flex(origin) else "") + (f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}. " if start and end else "Dates flexible. ")
                           + f"Stay preference: {stay_pref}; transport: {mode_pref}.", "kpis": kpis}]
    if map_points:
        blocks.append({"type": "map", "spec": {"kind": "map", "title": f"{dest}: places by day", "points": map_points, "route": False, "skipped": []}})
    if t_items:
        blocks.append({"type": "table", "title": "Transport options found online", "columns": ["Mode", "Name", "Duration", "Price", "Source"],
                       "rows": [[str(t["mode"]).title(), t.get("name") or "", t.get("duration") or "—", t.get("price") or "—", "web page"] for t in t_items],
                       "note": "Read from pages the app opened; fares and timetables change: confirm on the operator's site before booking."})
    if h_items:
        blocks.append({"type": "table", "title": "Stay options found online", "columns": ["Hotel", "Area", "Price per night"], "rows": [[h["name"], h.get("area") or "—", h.get("price") or "—"] for h in h_items]})
    if itin:
        blocks.append({"type": "itinerary", "title": "Day-by-day itinerary", "days": itin})
    blocks.append(budget_block)
    links = []
    if origin and not flex(origin):
        links += [{"label": "Flights", "url": common.link_flights(origin, dest, ci), "note": "Google Flights, pre-filled"}, {"label": "Trains", "url": common.link_trains(origin, dest), "note": "search"},
                  {"label": "Buses", "url": common.link_buses(origin, dest), "note": "search"}]
    links += [{"label": "Hotels", "url": common.link_hotels(dest, ci, co, trav), "note": "Booking.com search, pre-filled"}, {"label": f"{dest} on Google Maps", "url": common.link_maps(dest)}]
    blocks.append({"type": "links", "title": "Booking and planning links (nothing is booked for you)", "items": links})
    files = []
    md = [f"# {days}-day trip to {dest}", ""] + [f"## {d['title']} {d['date']}\n" + "\n".join(f"- {i['time']} {i['title']}" + (f" ({i['note']})" if i["note"] else "") for i in d["items"]) + "\n" for d in itin]
    files.append(rc.out_file(f"trip_{re.sub(r'[^a-z0-9]+', '_', dest.lower())}.md", "\n".join(md)))
    if ev_ics:
        files.append(rc.out_file(f"trip_{re.sub(r'[^a-z0-9]+', '_', dest.lower())}.ics", common.make_ics(ev_ics, f"Trip to {dest}")))
    if not t_items and origin and not flex(origin):
        notes.append("No transport fares could be read from the web pages found; use the links above.")
    if not h_items:
        notes.append("No hotel prices could be read from the web pages found; use the hotel link above.")
    notes.append("Names of places and hotels come from web pages and were checked to exist on the map; prices, opening hours and availability must be confirmed with the provider.")
    facts = [f"Trip: {days} days in {dest}" + (f" from {origin}" if origin and not flex(origin) else "") + f" for {trav} traveller(s). Stay preference {stay_pref}, transport {mode_pref}."]
    if leg:
        facts.append(f"Distance {origin} to {dest}: about {leg['km']} km, {leg['minutes']} minutes by road ({leg['method']}).")
    facts += [f"Day {i + 1}: " + ", ".join(x["title"] for x in d["items"]) + (f" ({d['summary']})" if d["summary"] else "") for i, d in enumerate(itin)]
    facts += [f"Transport found: {t.get('mode')} {t.get('name')} {t.get('duration') or ''} {t.get('price') or ''}".strip() for t in t_items[:4]]
    facts += [f"Hotel found: {h['name']} {h.get('price') or ''}".strip() for h in h_items[:3]]
    if budget:
        facts.append(f"Budget {common.fmt_money(budget, cur)} allocated: " + ", ".join(f"{k} {int(v * 100)}%" for k, v in alloc.items()) + " (an allocation, not quotes).")
    return {"blocks": blocks, "files": files, "notes": notes, "facts": facts, "summary": f"{days}-day trip to {dest}",
            "what": "an itinerary, interactive map, transport and hotel options, budget and booking links", "size": "5-8 sentences: the shape of the trip, the transport and stay situation, the budget picture, and what to verify before booking"}


register(Workflow(
    id="travel", skill="planning", title="Trip plan", description="A trip or holiday: transport, hotels, day-by-day itinerary, map and budget", uses_web=True,
    keywords=re.compile(r"\b(?:trip|travel|tour|vacation|holiday|itinerary|flight|hotel|visit|honeymoon|getaway|road ?trip|backpack\w*|sightseeing|weekend (?:in|at|to))\b|\d+[- ]day\b", re.I), prefill=prefill, run=run,
    slots=[Slot("destination", "Destination", "Where do you want to go?"),
           Slot("origin", "Travelling from", "Where are you travelling from?", pref="travel.origin"),
           Slot("dates", "Travel dates", "What are your travel dates?", kind="date", hint="start and end, or just a start date and I will use the trip length"),
           Slot("days", "Trip length (days)", "", kind="number", required=False, default=4),
           Slot("travellers", "Travellers", "How many people are travelling?", kind="number"),
           Slot("budget", "Budget", "What is your approximate total budget?", pref="travel.budget", hint="e.g. ₹40,000 in total"),
           Slot("transport", "Transport", "Which transport do you prefer?", kind="choice", choices=["train", "flight", "bus", "flexible"], pref="travel.transport"),
           Slot("stay", "Stay", "What type of accommodation do you prefer?", kind="choice", choices=["budget", "mid-range", "luxury", "flexible"], pref="travel.stay")]))
