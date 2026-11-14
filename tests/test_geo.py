"""Geocoding / routing / map tool with a mock transport (no network)."""
import asyncio
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import geo, tools  # noqa: E402

from test_agent import TempDB  # noqa: E402

PLACES = {"baga beach": ("15.5553", "73.7517"), "fort aguada": ("15.4926", "73.7733"), "anjuna": ("15.5732", "73.7410")}


def handler(request: httpx.Request) -> httpx.Response:
    if "nominatim" in request.url.host:
        q = request.url.params["q"].lower()
        for k, (la, lo) in PLACES.items():
            if k in q:
                return httpx.Response(200, json=[{"lat": la, "lon": lo, "display_name": k.title() + ", Goa, India"}])
        return httpx.Response(200, json=[])
    if "osrm" in request.url.host:
        return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 12500, "duration": 1500,
                                                                   "legs": [{"distance": 6000, "duration": 700}, {"distance": 6500, "duration": 800}]}]})
    return httpx.Response(404)


class GeoTests(TempDB):
    def setUp(self):
        super().setUp()
        geo._transport = httpx.MockTransport(handler)

    def tearDown(self):
        geo._transport = None
        super().tearDown()

    def test_geocode_and_cache(self):
        g = asyncio.run(geo.geocode("Baga Beach", "Goa"))
        self.assertAlmostEqual(g["lat"], 15.5553, places=3)
        geo._transport = httpx.MockTransport(lambda r: httpx.Response(500))  # a cache hit must not need the network
        self.assertEqual(asyncio.run(geo.geocode("Baga Beach", "Goa"))["lon"], g["lon"])

    def test_unknown_place_is_none_not_a_guess(self):
        self.assertIsNone(asyncio.run(geo.geocode("Qwertyland Nowhere")))

    def test_route_osrm_and_fallback(self):
        r = asyncio.run(geo.route([(15.55, 73.75), (15.49, 73.77), (15.57, 73.74)]))
        self.assertEqual((r["method"], r["km"], r["minutes"]), ("osrm", 12.5, 25))
        geo._transport = httpx.MockTransport(lambda r: httpx.Response(503))
        f = asyncio.run(geo.route([(15.0, 73.0), (15.5, 73.5)]))
        self.assertEqual(f["method"], "straight-line")
        self.assertGreater(f["km"], 60)

    def test_build_map_skips_unknown_and_validates_given_coordinates(self):
        spec = asyncio.run(geo.build_map({"title": "Goa", "route": True, "points": [
            {"name": "Baga Beach"}, {"name": "Fort Aguada", "group": "fort"}, {"name": "Nowhereville"}, {"name": "Bad", "lat": 999, "lon": 5}]}))
        self.assertEqual([p["name"] for p in spec["points"]], ["Baga Beach", "Fort Aguada"])
        self.assertEqual(set(spec["skipped"]), {"Nowhereville", "Bad"})
        self.assertEqual(spec["distance"]["method"], "osrm")

    def test_nothing_locatable_is_an_error(self):
        with self.assertRaises(ValueError):
            asyncio.run(geo.build_map({"points": ["Nowhereville"]}))

    def test_tool_returns_a_map_detail(self):
        res = asyncio.run(tools.create_map({}, "c", {"title": "T", "points": ["Anjuna", "Baga Beach"], "route": True}))
        self.assertTrue(res.ok)
        self.assertEqual(len(res.detail["map"]["points"]), 2)
        with self.assertRaises(tools.ToolError):
            asyncio.run(tools.create_map({}, "c", {"points": []}))

    def test_nearest_order(self):
        pts = [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 10}, {"lat": 0, "lon": 1}]
        self.assertEqual([p["lon"] for p in geo.order_nearest(pts)], [0, 1, 10])


if __name__ == "__main__":
    unittest.main()
