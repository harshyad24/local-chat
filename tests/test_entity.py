"""Entity cards: planning, validation, grounding, comparison, and a mocked end-to-end lookup (no network)."""
import asyncio
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import entity as E  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Plan(unittest.TestCase):
    def test_named_things_get_cards(self):
        for t, name in [("Tell me about Kriti Sanon.", "Kriti Sanon"), ("Tell me about ASUS ROG Zephyrus G16.", "ASUS ROG Zephyrus G16"),
                        ("Show me the NVIDIA RTX 5090", "NVIDIA RTX 5090"), ("Mahindra Thar Roxx", "Mahindra Thar Roxx"), ("Tell me about the Taj Mahal.", "Taj Mahal")]:
            p = E.plan(t)
            self.assertEqual(p["mode"], "card", t)
            self.assertEqual(p["entities"], [name], t)

    def test_ordinary_messages_do_not(self):
        for t in ["How does a sitar produce sound?", "Write a poem about rain", "hello", "thanks a lot", "Show me a bar chart of sales",
                  "what are her best movies?", "Explain recursion", "summarize this", "tell me about it", "```code```"]:
            self.assertIsNone(E.plan(t), t)

    def test_follow_up_with_entity_on_screen(self):
        self.assertIsNone(E.plan("tell me about her", {"name": "Kriti Sanon"}))

    def test_compare_images_price(self):
        p = E.plan("Compare iPhone 17 Pro and Samsung Galaxy S26 Ultra")
        self.assertEqual((p["mode"], p["entities"]), ("compare", ["iPhone 17 Pro", "Samsung Galaxy S26 Ultra"]))
        self.assertEqual(len(E.plan("compare A1 vs B2 vs C3")["entities"]), 3)
        self.assertEqual(E.plan("images of Golden Retriever")["mode"], "images")
        self.assertTrue(E.plan("price of Samsung Galaxy S25 Ultra")["volatile"])

    def test_title_matching(self):
        self.assertEqual(E._title_score("Kriti Sanon", "Kriti Sanon"), 1.0)
        self.assertGreater(E._title_score("Python programming language", "Python (programming language)"), 0.85)
        self.assertLess(E._title_score("Kriti Sanon", "Kriti Kharbanda"), 0.8)
        self.assertLess(E._title_score("blue lotus", "Nymphaea caerulea"), 0.62)


class Validation(unittest.TestCase):
    def test_validate_cleans_and_rejects(self):
        card = E.validate_card({"entity": {
            "name": " Thing ", "family": "weird", "description": "d",
            "image": {"url": "javascript:alert(1)"},
            "gallery": [{"url": "https://a.org/x.jpg"}, {"url": "https://a.org/x.jpg"}, {"url": "data:image/png;base64,AAA"}],
            "attributes": [{"label": "A", "value": "1"}, {"label": "a", "value": "dup"}, {"label": "", "value": "x"}, {"label": "B", "value": ""}],
            "sources": [{"title": "t", "url": "ftp://x"}, {"title": "ok", "url": "https://ok.org/p"}]}})
        e = card["entity"]
        self.assertEqual(e["name"], "Thing")
        self.assertEqual(e["family"], "thing")
        self.assertIsNone(e["image"])  # unsafe scheme dropped
        self.assertEqual([g["url"] for g in e["gallery"]], ["https://a.org/x.jpg"])  # deduped, data: dropped
        self.assertEqual([a["label"] for a in e["attributes"]], ["A"])
        self.assertEqual([s["url"] for s in e["sources"]], ["https://ok.org/p"])
        with self.assertRaises(ValueError):
            E.validate_card({"entity": {"name": "Empty"}})
        with self.assertRaises(ValueError):
            E.validate_card({"nope": 1})

    def test_grounding_drops_invented_specs_and_prices(self):
        page = "The laptop has 32GB RAM and a 2TB SSD. Price: Rs. 4,59,990 for this configuration."
        raw = {"attributes": [{"label": "RAM", "value": "32GB"}, {"label": "SSD", "value": "2TB"},
                              {"label": "GPU", "value": "RTX 5090"}, {"label": "Weight", "value": "1.9 kg"}],
               "price": {"value": "Rs. 4,59,990", "note": "this config"}}
        attrs, price = E.ground_attributes(raw, page, "https://x.org/p", "", "2026-01-01")
        self.assertEqual([a["label"] for a in attrs], ["RAM", "SSD"])  # RTX 5090 and 1.9 kg are not in the page
        self.assertEqual(price["value"], "Rs. 4,59,990")
        self.assertTrue(price["volatile"])
        self.assertEqual(price["as_of"], "2026-01-01")
        _, bad = E.ground_attributes({"price": {"value": "$999"}}, page, "u", "", "t")
        self.assertIsNone(bad)  # a price that isn't on the page is never shown

    def test_image_relevance_and_authority(self):
        self.assertTrue(E.relevant_image("ASUS ROG Zephyrus G16", {"title": "Asus ROG Zephyrus G16 (2024) review", "url": "", "image": ""}))
        self.assertFalse(E.relevant_image("ASUS ROG Zephyrus G16", {"title": "Cute cat photo", "url": "", "image": ""}))
        self.assertEqual(E.authority("https://www.asus.com/laptops/x", "ASUS ROG Zephyrus G16"), 3)
        self.assertEqual(E.authority("https://www.reddit.com/r/x", "ASUS ROG"), 0)
        self.assertEqual(E.clean_url("javascript:x"), "")

    def test_family_detection(self):
        self.assertEqual(E.family_from_types(["human"], False)[0], "person")
        self.assertEqual(E.family_from_types(["mausoleum"], True)[0], "place")
        self.assertEqual(E.family_from_types(["car"], False)[0], "vehicle")
        self.assertEqual(E.family_from_types(["type of musical instrument"], False)[0], "instrument")
        self.assertEqual(E.family_from_types(["academic discipline"], False), ("thing", True))  # abstract -> no card
        self.assertEqual(E.family_from_types(["taxon"], False, "Plantae")[0], "plant")

    def test_sections_skip_reference_blocks(self):
        txt = "Intro.\n\n== Career ==\n" + "Career text. " * 20 + "\n\n== References ==\nx\n\n=== Sub ===\n" + "Sub text here. " * 20
        intro, secs = E.split_sections(txt)
        self.assertEqual(intro, "Intro.")
        self.assertEqual([s["title"] for s in secs], ["Career", "Sub"])


class Compare(unittest.TestCase):
    def test_rows_mark_differences_and_missing(self):
        def card(name, attrs):
            return E.validate_card({"entity": {"name": name, "family": "smartphone", "description": "d", "attributes": attrs}})
        a = card("A", [{"label": "RAM", "value": "8GB"}, {"label": "Battery", "value": "5000 mAh"}, {"label": "Price", "value": "$999", "volatile": True}])
        b = card("B", [{"label": "RAM", "value": "12GB"}, {"label": "Battery", "value": "5000 mAh"}])
        c = E.compare([a, b])
        rows = {r["label"]: r for r in c["rows"]}
        self.assertTrue(rows["RAM"]["differs"])
        self.assertFalse(rows["Battery"]["differs"])
        self.assertEqual(rows["Price"]["values"], ["$999", ""])  # a missing value stays empty, never guessed
        self.assertTrue(rows["Price"]["differs"])
        self.assertTrue(c["rows"][0]["differs"])  # differences first
        self.assertTrue(any("Prices" in n for n in c["notes"]))


class History(unittest.TestCase):
    def test_last_entity_and_hint(self):
        hist = [{"role": "user", "content": "hi"},
                {"role": "assistant", "meta": {"tools": [{"name": "entity_card", "ok": True, "detail": {"card": {"entity": {"name": "Kriti Sanon", "category": "human", "description": "An actress."}}}}]}},
                {"role": "user", "content": "her movies?"}]
        ent = E.last_entity(hist)
        self.assertEqual(ent["name"], "Kriti Sanon")
        self.assertIn("Kriti Sanon", E.followup_hint(ent))
        self.assertIsNone(E.last_entity([{"role": "assistant", "meta": {"tools": []}}]))


def wiki_handler(request: httpx.Request) -> httpx.Response:
    q = dict(request.url.params)
    host = request.url.host

    def j(d):
        return httpx.Response(200, json=d)

    if host == "en.wikipedia.org":
        if q.get("generator") == "search":
            return j({"query": {"pages": [{"pageid": 1, "title": "Test Person", "index": 1, "description": "Indian actress", "pageprops": {"wikibase_item": "Q1"}}]}})
        return j({"query": {"pages": [{"title": "Test Person", "fullurl": "https://en.wikipedia.org/wiki/Test_Person", "pageimage": "Test.jpg",
                                       "pageprops": {"wikibase_item": "Q1"},
                                       "images": [{"title": "File:Test.jpg"}, {"title": "File:Flag of India.svg"}, {"title": "File:Other.jpg"}],
                                       "extract": "Test Person is an actress.\n\n== Career ==\n" + "She acted in films. " * 12}]}})
    if host == "www.wikidata.org":
        ids = q.get("ids", "")
        if q.get("props") == "labels":
            names = {"Q5": "human", "Q668": "India", "Q33999": "actor"}
            return j({"entities": {i: {"labels": {"en": {"value": names.get(i, i)}}} for i in ids.split("|")}})
        val = lambda v: {"mainsnak": {"datavalue": {"value": v}}}  # noqa: E731
        return j({"entities": {"Q1": {"labels": {"en": {"value": "Test Person"}}, "descriptions": {"en": {"value": "Indian actress"}}, "claims": {
            "P31": [val({"id": "Q5"})], "P27": [val({"id": "Q668"})], "P106": [val({"id": "Q33999"})],
            "P569": [val({"time": "+1990-07-27T00:00:00Z", "precision": 11})]}}}})
    if host == "commons.wikimedia.org":
        def info(t):
            return {"title": t, "imageinfo": [{"url": f"https://upload.wikimedia.org/{t[5:]}", "thumburl": f"https://upload.wikimedia.org/thumb/{t[5:]}",
                                               "mime": "image/jpeg", "descriptionurl": f"https://commons.wikimedia.org/wiki/{t}",
                                               "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "<a>A Photographer</a>"}}}]}
        return j({"query": {"pages": [info(t) for t in q["titles"].split("|")]}})
    return httpx.Response(404)


class MockedLookup(unittest.TestCase):
    def test_person_end_to_end_offline(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(wiki_handler))
        r = run(E.lookup({}, "", "Test Person", client=client, search_fn=lambda s, q: asyncio.sleep(0, result=[])))
        e = r["card"]["entity"]
        self.assertEqual((e["name"], e["family"]), ("Test Person", "person"))
        self.assertEqual({a["label"]: a["value"] for a in e["attributes"]}, {"Born": "27 July 1990", "Nationality": "India", "Occupation": "actor"})
        self.assertEqual(e["image"]["license"], "CC BY 4.0")
        self.assertEqual(e["image"]["credit"], "A Photographer")
        self.assertTrue(e["gallery"][0]["url"].endswith("Other.jpg"))  # the flag was filtered out
        self.assertEqual([s["kind"] for s in e["sources"]], ["encyclopedia", "database"])
        self.assertEqual(e["sections"][0]["title"], "Career")
        self.assertIn("Verified facts", r["context"])

    def test_not_found_gives_no_card(self):
        def empty(request):
            return httpx.Response(200, json={"query": {"pages": []}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(empty))
        r = run(E.lookup({}, "", "Zzzz Qqqq", client=client, search_fn=lambda s, q: asyncio.sleep(0, result=[])))
        self.assertIsNone(r["card"])


if __name__ == "__main__":
    unittest.main()
