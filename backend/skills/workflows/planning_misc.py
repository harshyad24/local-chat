"""/planning -> entertainment (movies & outings), event (celebrations & parties) and schedule (meetings, routines, project timelines,
business schedules). Entertainment reads showtimes from web pages (names must occur in the text); events and schedules are deterministic."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from .. import common, questions, scheduler
from ..registry import Slot, Workflow, register
from ... import charts, geo


# ============================================================ entertainment
def prefill_ent(text: str, ctx: dict) -> dict:
    out: dict = {}
    c = re.search(r"\b(?:in|near|around|at)\s+([A-Z][\w .'\-]{1,30}?)(?=\s+(?:this|next|on|for|tonight|today|tomorrow|with|under)\b|[.,?!]|$)", text)
    if c:
        out["city"] = c.group(1).strip()
    ds = questions.parse_dates(text)
    if ds:
        out["when"] = {"start": ds[0].isoformat(), "end": ds[-1].isoformat()}
    g = re.search(r"\b(action|comedy|drama|thriller|horror|romance|romantic|sci-?fi|animation|animated|family|documentary|fantasy|mystery|crime|adventure)\b", text, re.I)
    if g:
        out["genre"] = g.group(1).lower()
    lang = re.search(r"\b(hindi|english|tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|gujarati|korean|japanese|spanish)\b", text, re.I)
    if lang:
        out["language"] = lang.group(1).title()
    return out


async def run_ent(rc) -> dict:
    s = rc.slots
    city = str(s["city"])
    flex = lambda v: v in (None, "", "flexible")
    genre = "" if flex(s.get("genre")) else str(s["genre"])
    lang = "" if flex(s.get("language")) else str(s["language"])
    w = s.get("when") if isinstance(s.get("when"), dict) else {}
    when_txt = (w.get("start") + (f" to {w['end']}" if w.get("end") and w["end"] != w["start"] else "")) if w.get("start") else "this weekend"
    steps = 4
    await rc.progress(1, steps, f"Searching showtimes in {city}")
    pages = await rc.research(f"movies now showing in {city} cinema showtimes {when_txt} {lang} {genre}".strip(), pages=3)
    more = await rc.research(f"{genre} {lang} movies in theatres {city} this week".strip(), pages=1)
    text = "\n\n".join(p["text"] or p["snippet"] for p in pages + more)
    await rc.progress(2, steps, "Reading movies and showtimes")
    items = await common.extract_items(rc, text, f"List movies that are playing in cinemas in or near {city}. Include the cinema/venue and showtimes if the text gives them.",
                                       {"title": "movie title", "genre": "genre", "language": "language", "rating": "rating or score", "venue": "cinema name", "showtimes": "times as written", "price": "ticket price"},
                                       ["title"], limit=8, max_tokens=1100)
    seen, movies = set(), []
    for it in items:
        k = str(it["title"]).lower()
        if k not in seen:
            seen.add(k)
            movies.append(it)
    if genre:
        pref = [m for m in movies if m.get("genre") and genre[:5] in str(m["genre"]).lower()]
        movies = pref + [m for m in movies if m not in pref]
    if lang:
        pref = [m for m in movies if m.get("language") and lang.lower() in str(m["language"]).lower()]
        movies = pref + [m for m in movies if m not in pref]
    await rc.progress(3, steps, "Locating cinemas")
    venues = list(dict.fromkeys(str(m["venue"]) for m in movies if m.get("venue")))[:6]
    pts = []
    for v in venues:
        g = await geo.geocode(v, city)
        if g:
            pts.append({"name": v, "lat": g["lat"], "lon": g["lon"], "group": "Cinema", "note": "", "n": len(pts) + 1})
    notes = ["Showtimes and prices come from web pages that may be out of date or incomplete: confirm on the ticket site before you go."]
    blocks = [{"type": "overview", "title": f"Entertainment in {city}", "text": f"{when_txt}. " + (f"Genre: {genre}. " if genre else "") + (f"Language: {lang}. " if lang else ""),
               "kpis": [{"label": "Movies found", "value": len(movies)}, {"label": "Cinemas", "value": len(venues)}]}]
    if movies:
        blocks.append({"type": "table", "title": "Movies playing", "columns": ["Title", "Genre", "Language", "Rating"], "rows": [[m["title"], m.get("genre") or "—", m.get("language") or "—", m.get("rating") or "—"] for m in movies]})
        shows = [[m["title"], m.get("venue") or "—", m.get("showtimes") or "—", m.get("price") or "—"] for m in movies if m.get("venue") or m.get("showtimes")]
        if shows:
            blocks.append({"type": "table", "title": "Showtimes and venues found", "columns": ["Movie", "Cinema", "Showtimes", "Price"], "rows": shows})
    else:
        notes.append("No movie listings could be read from the pages found (ticket sites often need JavaScript). Use the links below.")
    if pts:
        blocks.append({"type": "map", "spec": {"kind": "map", "title": "Cinemas", "points": pts, "route": False, "skipped": []}})
    best = movies[0] if movies else None
    if best:
        t0 = (re.search(r"\b(\d{1,2}[:.]\d{2}\s*(?:am|pm)?)", str(best.get("showtimes") or ""), re.I) or [None, "evening"])[1]
        stops = [{"time": "Before", "title": "Dinner or a snack nearby", "note": "pick a place near the cinema", "link": common.link_maps(f"restaurants near {best.get('venue') or city}")},
                 {"time": t0, "title": f"{best['title']}" + (f" at {best['venue']}" if best.get("venue") else ""), "note": "arrive 20 minutes early"},
                 {"time": "After", "title": "Coffee or dessert", "link": common.link_maps(f"dessert cafe near {best.get('venue') or city}")}]
        blocks.append({"type": "itinerary", "title": "Optional outing plan", "days": [{"title": "Movie night", "date": "", "items": stops}]})
    blocks.append({"type": "links", "title": "Tickets (opens the provider's own site)", "items": [{"label": "BookMyShow", "url": common.link_movies(city)},
                                                                                                    {"label": "All showtimes (Google)", "url": common.link_search(f"movie showtimes {city}")},
                                                                                                    {"label": "Cinemas near you", "url": common.link_maps(f"cinema {city}")}]})
    facts = [f"Entertainment in {city} ({when_txt})." + (f" Preferences: {genre} {lang}." if genre or lang else "")] + [f"Movie: {m['title']} ({m.get('genre') or 'genre n/a'}, {m.get('language') or 'language n/a'}) at {m.get('venue') or 'venue n/a'} {m.get('showtimes') or ''}".strip() for m in movies[:6]]
    return {"blocks": blocks, "notes": notes, "facts": facts, "summary": f"{len(movies)} movies in {city}", "what": "movie picks, showtimes, cinema map, an outing plan and ticket links",
            "size": "3-5 sentences: the best matches for the user's taste and where to check showtimes"}


# ============================================================ events
EVENT_TASKS = {
    "wedding": [(240, "Fix the date, budget and guest-list size"), (210, "Book the venue and caterer"), (180, "Book photographer / videographer"), (150, "Shop for outfits and rings"), (120, "Send save-the-dates"),
                (90, "Plan decor, music and entertainment"), (60, "Send formal invitations"), (45, "Arrange accommodation and transport for guests"), (30, "Final tastings and fittings"),
                (14, "Confirm all vendors and headcount"), (7, "Seating plan and run-of-show"), (2, "Pack essentials; delegate day-of tasks"), (0, "Wedding day")],
    "birthday": [(30, "Decide theme, budget and guest list"), (21, "Book venue or plan the home setup"), (14, "Send invitations"), (10, "Order the cake and plan food"), (7, "Buy decorations and return gifts"),
                 (3, "Confirm RSVPs and plan games or activities"), (1, "Prepare food, decorate, charge cameras"), (0, "Party day")],
    "conference": [(90, "Set goals, budget and date"), (75, "Book venue and AV"), (60, "Line up speakers and agenda"), (45, "Open registration and promote"), (30, "Arrange catering and signage"),
                   (14, "Send reminders and logistics to attendees"), (7, "Rehearse sessions; print badges"), (1, "Set up the venue and test AV"), (0, "Event day"), (-3, "Send thanks and collect feedback")],
    "party": [(21, "Pick date, theme and guest list"), (14, "Book venue or plan the space; send invitations"), (10, "Plan menu and drinks"), (5, "Buy supplies and decorations"), (2, "Confirm guests; prepare playlist"),
              (0, "Party day")],
    "generic": [(30, "Define purpose, date, budget and guest list"), (21, "Book venue and key vendors"), (14, "Send invitations and promote"), (7, "Confirm vendors and headcount; prepare materials"), (1, "Set up and run a final check"),
                (0, "Event day"), (-2, "Thank-yous and feedback")],
}
BUDGET_SPLIT = {
    "wedding": {"Venue & catering": 0.50, "Photography & video": 0.10, "Decor & flowers": 0.10, "Attire & beauty": 0.10, "Music & entertainment": 0.05, "Invitations & misc": 0.05, "Contingency": 0.10},
    "birthday": {"Venue & food": 0.50, "Cake": 0.10, "Decor": 0.15, "Entertainment & games": 0.10, "Return gifts": 0.10, "Contingency": 0.05},
    "conference": {"Venue & AV": 0.40, "Catering": 0.25, "Speakers & travel": 0.15, "Marketing": 0.10, "Materials & staff": 0.05, "Contingency": 0.05},
    "party": {"Food & drinks": 0.50, "Venue": 0.20, "Decor": 0.10, "Music & entertainment": 0.10, "Contingency": 0.10},
    "generic": {"Venue": 0.35, "Food & drinks": 0.30, "Decor & materials": 0.15, "Entertainment": 0.10, "Contingency": 0.10},
}


def event_type(text: str) -> str:
    t = (text or "").lower()
    for k, rx in (("wedding", r"wedding|engagement|marriage|reception|sangeet|mehndi"), ("birthday", r"birthday|bday|anniversary"), ("conference", r"conference|seminar|workshop|summit|meetup|webinar|hackathon|corporate"),
                  ("party", r"party|celebration|get-?together|farewell|baby shower|housewarming|diwali|christmas|new year")):
        if re.search(rx, t):
            return k
    return "generic"


def prefill_event(text: str, ctx: dict) -> dict:
    out = {"kind": event_type(text)}
    ds = questions.parse_dates(text)
    if ds:
        out["date"] = {"start": ds[0].isoformat(), "end": ds[0].isoformat()}
    g = re.search(r"(\d{1,4})\s*(?:guests?|people|attendees|persons|participants)", text, re.I)
    if g:
        out["guests"] = g.group(1)
    b = re.search(r"(?:budget(?: of)?|under|within|upto|up to)\s*((?:₹|rs\.?|inr|\$|usd|€|£)?\s*[\d,\.]+\s*(?:k|lakhs?|l|cr)?)", text, re.I)
    if b:
        out["budget"] = b.group(1).strip()
    return out


async def run_event(rc) -> dict:
    s = rc.slots
    kind = event_type(str(s.get("kind") or "")) if str(s.get("kind") or "").lower() not in EVENT_TASKS else str(s["kind"]).lower()
    d = date.fromisoformat(s["date"]["start"]) if isinstance(s.get("date"), dict) and s["date"].get("start") else date.today() + timedelta(days=30)
    guests = int(float(s.get("guests") or 0)) or None
    budget_raw = "" if s.get("budget") in (None, "flexible") else str(s["budget"])
    budget = questions.parse_number(budget_raw) if budget_raw else None
    cur = questions.currency_of(budget_raw)
    location = "" if s.get("location") in (None, "flexible") else str(s["location"])
    today = date.today()
    await rc.progress(1, 3, "Building the timeline")
    tasks = []
    for off, text in EVENT_TASKS[kind]:
        due = d - timedelta(days=off)
        late = due < today
        tasks.append({"id": f"t{off}", "text": text + (" (ASAP: the date has passed)" if late and off > 0 else ""), "due": (today if late and off > 0 else due).strftime("%d %b")})
    days_left = (d - today).days
    notes = []
    if days_left < max(o for o, _ in EVENT_TASKS[kind]) * 0.4:
        notes.append(f"Only {days_left} day(s) remain, much shorter than the usual lead time: tasks whose date has passed are marked ASAP, so start with those.")
    await rc.progress(2, 3, "Allocating the budget")
    split = BUDGET_SPLIT[kind]
    blocks = [{"type": "overview", "title": f"{kind.title()} plan for {d.strftime('%d %b %Y')}", "text": (f"{guests} guests. " if guests else "") + (f"Location: {location}. " if location else ""),
               "kpis": [{"label": "Days left", "value": days_left}, {"label": "Tasks", "value": len(tasks)}] + ([{"label": "Guests", "value": guests}] if guests else []) + ([{"label": "Budget", "value": common.fmt_money(budget, cur)}] if budget else [])},
              {"type": "countdown", "items": [{"label": f"the {kind}", "date": d.isoformat(), "days": days_left}]},
              {"type": "checklist", "title": "Planning checklist (tick items off)", "key": "event_done", "items": tasks}]
    if budget:
        rows = [{"label": k, "planned": common.fmt_money(budget * v, cur), "found": None, "note": f"{int(v * 100)}% (typical split)"} for k, v in split.items()]
        per = f" About {common.fmt_money(budget / guests, cur)} per guest." if guests else ""
        blocks.append({"type": "budget", "title": "Budget allocation", "rows": rows, "total": {"planned": common.fmt_money(budget, cur)}, "note": "A typical split for this kind of event: an allocation of your budget, not a quote." + per})
        spec = None
        try:
            spec = charts.build({"type": "donut_chart", "title": "Budget split", "labels": list(split), "values": [round(budget * v) for v in split.values()]})[0]
        except charts.ChartError:
            pass
        if spec:
            blocks.append({"type": "chart", "spec": spec})
    if location:
        await rc.progress(3, 3, f"Looking for venues in {location}")
        pages = await rc.research(f"{kind} venues in {location} price capacity", pages=2)
        v = await common.extract_items(rc, "\n\n".join(p["text"] or p["snippet"] for p in pages), f"List venues or banquet halls for a {kind} in {location}.", {"name": "venue name", "capacity": "guest capacity", "price": "price"}, ["name"], limit=5)
        if v:
            blocks.append({"type": "table", "title": f"Venue ideas found online ({location})", "columns": ["Venue", "Capacity", "Price"], "rows": [[x["name"], x.get("capacity") or "—", x.get("price") or "—"] for x in v], "note": "Call to confirm availability and prices."})
        blocks.append({"type": "links", "title": "Search", "items": [{"label": f"{kind.title()} venues in {location}", "url": common.link_maps(f"{kind} venue {location}")}, {"label": "Caterers", "url": common.link_maps(f"caterers {location}")}]})
    events = [{"title": f"{kind.title()}: {t['text']}", "start": (d - timedelta(days=off)) if (d - timedelta(days=off)) >= today else today, "end": None} for off, t in zip([o for o, _ in EVENT_TASKS[kind]], tasks)]
    files = [rc.out_file(f"{kind}_plan.ics", common.make_ics(events, f"{kind.title()} plan"))]
    return {"blocks": blocks, "files": files, "notes": notes + ["The checklist and budget split are standard templates for this kind of event, adjusted to your date: edit them to fit your plans."],
            "facts": [f"{kind} on {d.isoformat()} ({days_left} days left)" + (f", {guests} guests" if guests else "") + (f", budget {common.fmt_money(budget, cur)}" if budget else "") + ".", f"Checklist has {len(tasks)} tasks; the first due: {tasks[0]['text']}."],
            "summary": f"{kind.title()} plan", "what": "a countdown, dated checklist, budget split and calendar reminders", "size": "3-5 sentences: what to do first, the budget picture and the biggest risk"}


# ============================================================ schedule (meetings, routines, projects, business)
def prefill_sched(text: str, ctx: dict) -> dict:
    out: dict = {}
    t = text.lower()
    out["kind"] = "project" if re.search(r"project|timeline|milestone|sprint|launch|roadmap", t) else "routine" if re.search(r"routine|daily|habit|morning|workout", t) else "meeting" if re.search(r"meeting|appointment|call|interview", t) else "general"
    ds = questions.parse_dates(text)
    if ds:
        out["range"] = {"start": ds[0].isoformat(), "end": ds[-1].isoformat()}
    return out


async def run_sched(rc) -> dict:
    s = rc.slots
    kind = str(s.get("kind") or "general")
    rng = s.get("range") if isinstance(s.get("range"), dict) else {}
    start = max(date.today(), date.fromisoformat(rng["start"])) if rng.get("start") else date.today()
    end = date.fromisoformat(rng["end"]) if rng.get("end") and rng["end"] != rng.get("start") else start + timedelta(days=13)
    hrs = re.findall(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", str(s.get("hours") or "09:00-18:00"), re.I)
    def to_t(m, default):
        if not m:
            return default
        h, mi, ap = int(m[0]), int(m[1] or 0), (m[2] or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return time(min(h, 23), mi)
    ws, we = to_t(hrs[0] if hrs else None, time(9, 0)), to_t(hrs[1] if len(hrs) > 1 else None, time(18, 0))
    if we <= ws:
        ws, we = time(9, 0), time(18, 0)
    await rc.progress(1, 3, "Understanding your tasks")
    items = [str(x) for x in (s.get("items") or [])]
    data = await rc.json_call(
        f"Turn these into schedulable tasks. Today is {date.today().isoformat()}. Reply with JSON only: "
        '{"tasks": [{"name": "", "minutes": 60, "deadline": "YYYY-MM-DD or null", "priority": 1}]} (priority 1 high, 2 normal, 3 low). '
        "Use the durations and deadlines the user gave (convert hours to minutes); if no duration is given use 60.\n\nTASKS:\n" + "\n".join(f"- {i}" for i in items), max_tokens=900)
    tasks = []
    for t in (data.get("tasks") if isinstance(data, dict) else None) or []:
        if isinstance(t, dict) and str(t.get("name", "")).strip():
            try:
                mins = int(float(t.get("minutes") or 60))
            except (TypeError, ValueError):
                mins = 60
            dl = None
            try:
                dl = date.fromisoformat(str(t.get("deadline"))) if t.get("deadline") else None
            except ValueError:
                dl = None
            tasks.append(scheduler.Task(str(t["name"]).strip()[:80], max(10, min(mins, 2400)), dl, int(t.get("priority") or 2) if str(t.get("priority") or 2).isdigit() else 2))
    if not tasks:
        return {"error": "I could not turn that into tasks. List them like “Prepare report – 3h – due Friday”."}
    await rc.progress(2, 3, "Scheduling")
    res = scheduler.schedule_tasks(tasks, start, end, ws, we, skip_weekends=kind != "routine")
    ev = res["events"]
    by: dict[str, list] = {}
    for e in ev:
        by.setdefault(e["start"].date().isoformat(), []).append(e)
    rows = [{"date": k, "day": date.fromisoformat(k).strftime("%A"), "slots": [{"id": f"{k}-{i}", "time": f"{e['start'].strftime('%H:%M')}–{e['end'].strftime('%H:%M')}", "subject": e["name"], "kind": "study" if e["priority"] != 1 else "mock", "topic": ("due " + e["deadline"]) if e["deadline"] else ""} for i, e in enumerate(v)]} for k, v in sorted(by.items())]
    blocks = [{"type": "overview", "title": f"{kind.title()} schedule: {len(tasks)} tasks", "text": f"{start.isoformat()} to {end.isoformat()}, working hours {ws.strftime('%H:%M')}–{we.strftime('%H:%M')}.",
               "kpis": [{"label": "Tasks", "value": len(tasks)}, {"label": "Blocks scheduled", "value": len(ev)}, {"label": "Unscheduled", "value": len(res["unscheduled"])}]},
              {"type": "calendar", "title": "Calendar", "events": [{"date": e["start"].date().isoformat(), "title": e["name"], "kind": "mock" if e["priority"] == 1 else ""} for e in ev]},
              {"type": "timetable", "title": "Day by day (tick as you finish)", "key": "sched_done", "rows": rows[:21]}]
    if kind == "project":
        first, last = {}, {}
        for e in ev:
            base = re.sub(r" \(part \d+\)$", "", e["name"])
            first.setdefault(base, e["start"])
            last[base] = e["end"]
        blocks.append({"type": "table", "title": "Project timeline", "columns": ["Task", "Start", "Finish", "Deadline"],
                       "rows": [[k, v.strftime("%a %d %b %H:%M"), last[k].strftime("%a %d %b %H:%M"), next((t.deadline.isoformat() for t in tasks if t.name == k and t.deadline), "—")] for k, v in first.items()]})
    notes = ["The schedule was built by code from your durations and deadlines (earliest deadline first, 15-minute buffers, no weekends unless it is a routine)."]
    for u in res["unscheduled"]:
        notes.append(f"Could not fit “{u['name']}” ({u['minutes_left']} min left): {u['reason']}.")
    files = [rc.out_file("schedule.ics", common.make_ics([{"title": e["name"], "start": e["start"], "end": e["end"]} for e in ev], "Schedule")),
             rc.out_file("schedule.csv", common.csv_text(["Task", "Start", "End", "Deadline"], [[e["name"], e["start"].isoformat(sep=" "), e["end"].isoformat(sep=" "), e["deadline"]] for e in ev]))]
    return {"blocks": blocks, "files": files, "notes": notes, "summary": f"Scheduled {len(tasks)} tasks",
            "facts": [f"{len(tasks)} tasks scheduled between {start} and {end}.", *[f"Not scheduled: {u['name']} ({u['reason']})" for u in res["unscheduled"]]],
            "what": "a calendar, day-by-day schedule and .ics export", "size": "3-4 sentences: how the work is laid out and anything that does not fit"}


register(Workflow(
    id="entertainment", skill="planning", title="Movies & outings", description="Movies, cinema shows, streaming ideas and nearby entertainment for a day or weekend", uses_web=True,
    keywords=re.compile(r"\b(?:movies?|films?|cinema|shows?|showtimes?|theatre|theater|netflix|streaming|concert|outing|date night|weekend plans?|things to do tonight|bookmyshow)\b", re.I), prefill=prefill_ent, run=run_ent,
    slots=[Slot("city", "City", "Which city or area?", pref="planning.city"),
           Slot("when", "When", "", kind="date", required=False, default="this weekend"),
           Slot("genre", "Genre", "Which genres do you like?", hint="e.g. thriller, comedy, any"),
           Slot("language", "Language", "Which language do you prefer for movies?", kind="choice", choices=["Hindi", "English", "Tamil", "Telugu", "Malayalam", "Kannada", "Marathi", "any"]),
           Slot("budget", "Budget", "", required=False), Slot("time_pref", "Showtime", "", required=False)]))
register(Workflow(
    id="event", skill="planning", title="Event plan", description="Celebrations, weddings, parties, conferences: checklist, timeline and budget", uses_web=True,
    keywords=re.compile(r"\b(?:event|party|wedding|birthday|celebration|conference|seminar|ceremony|reception|gathering|farewell|baby shower|housewarming|anniversary|workshop|meetup)\b", re.I), prefill=prefill_event, run=run_event,
    slots=[Slot("kind", "Event type", "", required=False),
           Slot("date", "Event date", "When is the event?", kind="date"),
           Slot("guests", "Guests", "Roughly how many guests?", kind="number"),
           Slot("budget", "Budget", "What is your total budget?", pref="planning.budget"),
           Slot("location", "Location", "", required=False)]))
register(Workflow(
    id="schedule", skill="planning", title="Schedule", description="Meetings, appointments, daily routines, project timelines and business schedules", general=True,
    keywords=re.compile(r"\b(?:schedule|calendar|meeting|appointment|routine|daily|project|timeline|milestones?|deadlines?|roadmap|agenda|time ?table|time management|plan my (?:day|week|month))\b", re.I), prefill=prefill_sched, run=run_sched,
    slots=[Slot("kind", "Type", "", required=False),
           Slot("items", "Tasks", "What needs to be scheduled? List each task with a duration and deadline if you have them.", kind="list", hint="e.g. Prepare report – 3h – due Friday; Team sync – 30 min"),
           Slot("range", "Dates", "Which dates should I schedule within?", kind="date"),
           Slot("hours", "Working hours", "", required=False, default="09:00-18:00")]))
