"""Places on a map: geocoding (Nominatim / OpenStreetMap), driving distance (public OSRM) and an offline fallback.

Both services are free and need no key, but they are shared public servers: requests are cached in SQLite, spaced out
(Nominatim allows 1 per second) and identified with a User-Agent. When a service is unreachable the numbers fall back to straight-line
distances, and every result says which method produced it, so an estimate is never shown as a measured route.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Optional

import httpx

from . import db

UA = "LocalChat/1.0 (personal local app; +http://127.0.0.1:8000)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving/"
_last_call = 0.0
_gate: Optional[asyncio.Lock] = None
CACHE_DDL = "CREATE TABLE IF NOT EXISTS geo_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)"
_transport: Optional[httpx.AsyncBaseTransport] = None  # tests inject a mock transport


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15, headers={"User-Agent": UA, "Accept-Language": "en"}, transport=_transport)


def _cache_get(key: str) -> Optional[dict]:
    with db.conn() as c:
        c.execute(CACHE_DDL)
        row = c.execute("SELECT value FROM geo_cache WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _cache_put(key: str, value: dict) -> None:
    with db.conn() as c:
        c.execute(CACHE_DDL)
        c.execute("INSERT OR REPLACE INTO geo_cache(key, value, created_at) VALUES(?,?,?)", (key, json.dumps(value), time.time()))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def valid_coord(lat: object, lon: object) -> bool:
    try:
        return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180 and not (float(lat) == 0 and float(lon) == 0)
    except (TypeError, ValueError):
        return False


async def geocode(query: str, near: str = "") -> Optional[dict]:
    """-> {name, lat, lon, display} or None. `near` (a city or country) narrows an ambiguous name like "Baga Beach"."""
    q = re.sub(r"\s+", " ", (query or "")).strip()
    if len(q) < 2:
        return None
    full = f"{q}, {near}" if near and near.lower() not in q.lower() else q
    key = "g:" + full.lower()
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    global _last_call, _gate
    _gate = _gate or asyncio.Lock()
    async with _gate:  # one request at a time, at least 1.1 s apart
        wait = 1.1 - (time.monotonic() - _last_call)
        if wait > 0 and _transport is None:
            await asyncio.sleep(wait)
        try:
            async with _client() as c:
                r = await c.get(NOMINATIM, params={"q": full, "format": "jsonv2", "limit": 1, "addressdetails": 0})
            _last_call = time.monotonic()
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return None  # not cached: the service may just be down right now
    if not data or not valid_coord(data[0].get("lat"), data[0].get("lon")):
        _cache_put(key, {})
        return None
    out = {"name": q, "lat": round(float(data[0]["lat"]), 6), "lon": round(float(data[0]["lon"]), 6), "display": str(data[0].get("display_name", ""))[:160]}
    _cache_put(key, out)
    return out


async def geocode_many(names: list[str], near: str = "") -> list[Optional[dict]]:
    out = []
    for n in names:  # sequential on purpose: the public service asks for it
        out.append(await geocode(n, near))
    return out


async def route(points: list[tuple[float, float]]) -> dict:
    """Driving distance/time through the points in order. -> {km, minutes, method: 'osrm'|'straight-line', legs: [...]}."""
    if len(points) < 2:
        return {"km": 0.0, "minutes": 0, "method": "none", "legs": []}
    key = "r:" + ";".join(f"{la:.4f},{lo:.4f}" for la, lo in points)
    hit = _cache_get(key)
    if hit:
        return hit
    coords = ";".join(f"{lo:.6f},{la:.6f}" for la, lo in points)
    try:
        async with _client() as c:
            r = await c.get(OSRM + coords, params={"overview": "false", "steps": "false"})
        r.raise_for_status()
        j = r.json()
        if j.get("code") == "Ok" and j.get("routes"):
            rt = j["routes"][0]
            out = {"km": round(rt["distance"] / 1000, 1), "minutes": round(rt["duration"] / 60), "method": "osrm",
                   "legs": [{"km": round(l["distance"] / 1000, 1), "minutes": round(l["duration"] / 60)} for l in rt.get("legs", [])]}
            _cache_put(key, out)
            return out
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return straight_line(points)


def straight_line(points: list[tuple[float, float]], kmh: float = 40.0) -> dict:
    """Fallback: great-circle distance x 1.3 (roads are not straight) at an average speed. Always labelled as an estimate."""
    legs = []
    for a, b in zip(points, points[1:]):
        km = haversine_km(a, b) * 1.3
        legs.append({"km": round(km, 1), "minutes": round(km / kmh * 60)})
    return {"km": round(sum(l["km"] for l in legs), 1), "minutes": sum(l["minutes"] for l in legs), "method": "straight-line", "legs": legs}


def order_nearest(points: list[dict], start: Optional[int] = 0) -> list[dict]:
    """Visit order by nearest neighbour (cheap and good enough for the 3-8 stops of one day)."""
    todo = list(points)
    if not todo:
        return []
    cur = todo.pop(start or 0)
    ordered = [cur]
    while todo:
        nxt = min(todo, key=lambda p: haversine_km((cur["lat"], cur["lon"]), (p["lat"], p["lon"])))
        todo.remove(nxt)
        ordered.append(nxt)
        cur = nxt
    return ordered


async def build_map(args: dict, near: str = "") -> dict:
    """The `map` spec the browser draws. Points need either lat/lon or a place name to look up; unknown places are reported, not guessed."""
    raw = args.get("points") or args.get("places") or []
    if not isinstance(raw, list) or not raw:
        raise ValueError("A map needs `points`: a list of {name, lat?, lon?, group?, note?} (names are looked up when lat/lon are missing).")
    pts, skipped = [], []
    for i, p in enumerate(raw[:60]):
        if isinstance(p, str):
            p = {"name": p}
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            continue
        name = str(p["name"]).strip()[:80]
        lat, lon = p.get("lat"), p.get("lon", p.get("lng"))
        if not valid_coord(lat, lon):
            g = await geocode(name, str(args.get("near") or near))
            if not g:
                skipped.append(name)
                continue
            lat, lon = g["lat"], g["lon"]
        pts.append({"name": name, "lat": float(lat), "lon": float(lon), "group": str(p.get("group") or "")[:30],
                    "note": str(p.get("note") or "")[:160], "value": p.get("value") if isinstance(p.get("value"), (int, float)) else None,
                    "n": len(pts) + 1})
    if not pts:
        raise ValueError("None of the places could be located" + (": " + ", ".join(skipped[:5]) if skipped else "") + ".")
    spec = {"kind": "map", "title": str(args.get("title") or "Map")[:120], "points": pts, "route": bool(args.get("route")), "skipped": skipped}
    if spec["route"] and len(pts) >= 2:
        r = await route([(p["lat"], p["lon"]) for p in pts])
        spec["distance"] = {k: r[k] for k in ("km", "minutes", "method")}
    return spec
