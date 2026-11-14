"""Universal entity cards: identify what the user asks about, fetch facts and images, validate, and hand a structured card to the UI.

Design rules
  * The app (not the model) decides when a card is shown, retrieves everything, and validates the result.
  * Facts come from sources: Wikipedia (text), Wikidata (structured attributes), Wikimedia Commons (licensed images)
    and, for things Wikipedia doesn't cover or that change (products, prices), ordinary web pages. Nothing is invented:
    every web-derived attribute must literally appear in the fetched page text, or it is dropped.
  * Images are never downloaded or re-hosted: the card stores the original URL, a thumbnail URL, the source page and
    (for Commons) the licence. Web images are only kept if the server can verify they exist and are images.
  * The category is free-form; a handful of "families" (person, place, product, ...) only choose the layout.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

from . import llm

UA = "LocalChat/1.0 (personal local app; +http://127.0.0.1:8000)"
WIKI = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
MAX_GALLERY = 6
MAX_ATTRS = 24
MAX_SECTIONS = 6
FAMILIES = ("person", "place", "animal", "plant", "instrument", "vehicle", "laptop", "smartphone", "product", "company", "software", "food", "thing")


def new_client(**kw) -> httpx.AsyncClient:  # tests replace this with a MockTransport client
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0), headers={"User-Agent": UA, "Accept-Language": "en"}, follow_redirects=True, **kw)


# =============================================================== 1. intent planning (pure, rule based)
_LEAD = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|hey\s+)*(?:tell me (?:something |more |all )?about|tell me|who (?:is|was|are)|what(?:'s| is| are| was)|show me|show|"
    r"give me (?:info|information|details) (?:on|about)|info(?:rmation)? (?:on|about)|details (?:on|about|of)|describe|about|find|look up|search for|i want to know about|"
    r"specs? of|specifications? of|price of|cost of|how much (?:is|does|are) )\s+(?:the |a |an )?(?P<rest>.+?)\s*[?.!]*\s*$", re.I)
_IMAGES = re.compile(r"\b(images?|photos?|pictures?|pics?|wallpapers?)\b", re.I)
_COMPARE = re.compile(r"\b(compare|comparison|versus|vs\.?|difference(?:s)? between|which is better)\b", re.I)
_PRICE = re.compile(r"\b(price|cost|how much|buy|purchase|availability|in stock|where to buy|deal|cheapest)\b", re.I)
_EXPLAIN = re.compile(r"^\s*(how (?:does|do|did|can|to)|why|explain|what happens|difference between|steps? to)\b", re.I)
_PRONOUN = re.compile(r"\b(he|she|him|her|his|hers|it|its|they|them|their|this|that|these|those)\b", re.I)
_QUESTION_START = re.compile(r"^\s*(who|what|when|where|which|how|why|is|are|does|do|did|can|could|will|would|should)\b", re.I)
_FILLER = re.compile(r"^(?:the|a|an|my|your|our|some|any)\s+", re.I)
_VOLATILE_WORDS = re.compile(r"\b(price|cost|how much|buy|availability|in stock|deal|cheapest|launch date|release date|latest|current|ceo|net worth)\b", re.I)


_NOT_ENTITY = re.compile(r"\b(chart|graph|plot|dashboard|diagram|flow ?chart|table|spreadsheet|code|script|function|regex|sql|poem|story|essay|email|letter|summary|summari[sz]e|translate|quiz|flashcards?)\b", re.I)


def _split_entities(rest: str) -> list[str]:
    parts = re.split(r"\s+(?:and|vs\.?|versus|with|or|&)\s+|,\s*", rest, flags=re.I)
    return [_FILLER.sub("", p.strip(" .?!\"'")) for p in parts if p.strip(" .?!\"'")]


_IMPERATIVE = re.compile(r"^(write|create|make|build|generate|draw|code|translate|summari[sz]e|give|list|fix|help|open|run|calculate|compute|convert|remember|forget|send|email|plan|suggest|recommend|rewrite|check|analy[sz]e|solve|set|add|remove|delete|find me|search|let'?s|thanks?|thank|ok|okay|yes|no|hi|hello|hey)\b", re.I)
_POSSESSIVE = re.compile(r"\b(he|she|him|her|his|hers|it|its|they|them|their|theirs|my|your|our|me|i|you|we)\b", re.I)


def _entity_like(s: str) -> bool:
    """Looks like a named thing: capitalised words, digits (model numbers) or a short noun phrase."""
    words = s.split()
    if not words or len(words) > 8:
        return False
    caps = sum(1 for w in words if w[:1].isupper() or any(ch.isdigit() for ch in w))
    return caps >= 1 or len(words) <= 3


def plan(text: str, last_entity: Optional[dict] = None) -> Optional[dict]:
    """Decide whether this message should get an entity card. Returns None for ordinary questions.

    {"mode": "card"|"compare"|"images", "entities": [...], "volatile": bool, "sure": bool}
    `sure` = the phrasing is clearly about a named thing (still verified against Wikipedia/web before a card is shown).
    """
    t = (text or "").strip()
    if not t or len(t) > 300 or "\n" in t.strip() or t.startswith("```"):
        return None
    volatile = bool(_VOLATILE_WORDS.search(t))
    if _NOT_ENTITY.search(t):
        return None
    if _COMPARE.search(t):
        body = re.sub(r"^\s*(?:please\s+)?(?:compare|comparison of|comparison between|which is better|difference(?:s)? between)\s*", "", t, flags=re.I)
        ents = _split_entities(re.sub(r"\b(?:which is better|in terms of .*|on .*)$", "", body, flags=re.I))
        ents = [e for e in ents if _entity_like(e)]
        if len(ents) >= 2:
            return {"mode": "compare", "entities": ents[:4], "volatile": True, "sure": True}
    if _IMPERATIVE.match(t) and not _LEAD.match(t):
        return None
    if _EXPLAIN.match(t) and not _IMAGES.search(t):
        return None  # "how does a sitar produce sound?" → normal explanatory answer
    m = _LEAD.match(t)
    rest = None
    if m:
        rest = m.group("rest")
    elif not _QUESTION_START.match(t) and len(t.split()) <= 7 and not t.endswith("?"):
        rest = t  # a bare name: "ASUS ROG Zephyrus G16", "Kriti Sanon"
    if rest is None:
        return None
    mode = "card"
    if _IMAGES.search(rest) or re.match(r"^\s*(?:show|find)\b", t, re.I) and _IMAGES.search(t):
        mode = "images"
        rest = re.sub(r"^\s*(?:images?|photos?|pictures?|pics?)\s+(?:of|for)\s+", "", rest, flags=re.I)
        rest = re.sub(r"\b(?:images?|photos?|pictures?|pics?)\b", "", rest, flags=re.I).strip()
    rest = re.sub(r"\b(?:price|cost|specs?|specifications?|review|reviews|details|info|information)\s*$", "", rest, flags=re.I).strip(" ,.-")
    rest = _FILLER.sub("", rest)
    if not rest or len(rest) > 80:
        return None
    if _IMPERATIVE.match(rest) or _POSSESSIVE.search(rest):
        return None
    pron = _PRONOUN.search(rest) and len(rest.split()) <= 3
    if last_entity and pron:
        return None  # a follow-up about the entity already on screen
    if not _entity_like(rest):
        return None
    if not _LEAD.match(t) and not any(w[:1].isupper() or w[:1].isdigit() for w in rest.split()):
        return None  # a bare lowercase phrase ("machine learning") is a concept, not a named thing
    capitalised = any(w[:1].isupper() for w in rest.split()[1:]) or any(ch.isdigit() for ch in rest)
    return {"mode": mode, "entities": [rest], "volatile": volatile, "sure": capitalised}


# =============================================================== 2. small helpers
def clean_url(u: Any) -> str:
    u = str(u or "").strip()
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else ""


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-zऀ-ॿ]+", "", str(s).lower())


def _tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[0-9a-zऀ-ॿ]+", s.lower()) if len(w) > 1 or w.isdigit()]


def _title_score(query: str, title: str) -> float:
    base = re.sub(r"\(.*?\)", "", title)
    best = 0.0
    for variant in (base, title.replace("(", " ").replace(")", " ")):
        q, t = _norm(query), _norm(variant)
        if not q or not t:
            continue
        if q == t:
            return 1.0
        ratio = difflib.SequenceMatcher(None, q, t).ratio()
        qt, tt = set(_tokens(query)), set(_tokens(variant))
        cover = len(qt & tt) / max(1, len(qt))
        score = max(ratio, 0.5 * ratio + 0.5 * cover)
        if variant is base and tt and tt <= qt and len(tt) >= 2:
            score = max(score, 0.75)  # title is a precise part of the query ("Taj Mahal" in "the Taj Mahal in Agra")
        if qt and qt <= tt:
            score = max(score, 0.9 if len(qt) >= 2 else 0.8)  # "Python programming language" ⊂ "Python (programming language)"
        best = max(best, score)
    return best


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _trim(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > n * 0.5 else cut.rsplit(" ", 1)[0] + " …")


_JUNK_SECTIONS = re.compile(r"^(see also|references?|external links?|notes?|further reading|bibliography|citations?|footnotes?|sources?|gallery|awards? and nominations|discography|filmography|works cited|explanatory notes)$", re.I)


def split_sections(plain: str) -> tuple[str, list[dict]]:
    """Wikipedia plaintext → (intro, [{title, content}]) dropping reference-style sections."""
    parts = re.split(r"\n\s*(={2,4})\s*([^=\n]+?)\s*\1\s*\n", "\n" + plain)
    intro = parts[0].strip()
    out = []
    i = 1
    while i + 2 < len(parts) + 1 and i < len(parts):
        level, title, body = parts[i], parts[i + 1].strip(), parts[i + 2] if i + 2 < len(parts) else ""
        i += 3
        if _JUNK_SECTIONS.match(title):
            continue
        body = body.strip()
        if len(body) > 80:
            out.append({"title": title, "content": _trim(body, 900)})
    return intro, out


# =============================================================== 3. Wikipedia / Wikidata / Commons
async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    r = await client.get(url, params={**params, "format": "json", "formatversion": "2"})
    r.raise_for_status()
    return r.json()


_WORK = re.compile(r"album|film|song|novel|band|television|tv series|comic|video game|episode|musical(?! instrument)|single|book|poem|play\b|painting|sculpture|character|fictional|company|record label", re.I)
_NATURAL = re.compile(r"species|genus|plant|flower|animal|breed|bird|fish|tree|herb|shrub|mammal|insect|fungus|mineral|gemstone|instrument|fruit|vegetable|spice|dish|food", re.I)


async def wiki_candidates_by_text(client, query: str) -> list[dict]:
    """Pages whose *text* mentions the query and whose short description says they are a natural kind ("blue lotus" → Nymphaea caerulea)."""
    d = await _get(client, WIKI, {"action": "query", "list": "search", "srsearch": f'"{query}"', "srlimit": 8, "srnamespace": 0, "srprop": "snippet"})
    titles = [x["title"] for x in (d.get("query") or {}).get("search") or []]
    if not titles:
        return []
    d2 = await _get(client, WIKI, {"action": "query", "titles": "|".join(titles), "prop": "description|pageprops", "ppprop": "wikibase_item|disambiguation"})
    out = []
    for p in (d2.get("query") or {}).get("pages") or []:
        desc = p.get("description", "")
        if _NATURAL.search(desc) and not re.search(r"index|list of|same common name|disambig", desc, re.I) and not re.match(r"^(List|Lists|Index) of ", p["title"]) and "disambiguation" not in (p.get("pageprops") or {}):
            out.append({"title": p["title"], "qid": (p.get("pageprops") or {}).get("wikibase_item"), "short": desc, "score": 0.7, "via": "text"})
    order = {t: i for i, t in enumerate(titles)}
    out.sort(key=lambda x: order.get(x["title"], 99))
    return out


async def wiki_search(client, query: str, sure: bool = True) -> Optional[dict]:
    d = await _get(client, WIKI, {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 6, "gsrnamespace": 0,
        "prop": "pageprops|description", "ppprop": "wikibase_item|disambiguation",
    })
    pages = (d.get("query") or {}).get("pages") or []
    best, best_score = None, 0.0
    alts: list[str] = []
    for p in sorted(pages, key=lambda x: x.get("index", 99)):
        title = p.get("title", "")
        if "disambiguation" in (p.get("pageprops") or {}) or re.match(r"^(List|Lists|Index|Outline|Timeline) of ", title):
            continue
        sc = _title_score(query, title) - 0.02 * (p.get("index", 1) - 1)
        if not sure and _WORK.search(p.get("description", "")):
            sc -= 0.25  # a plain lowercase noun is more likely the thing itself than a book/film/band named after it
        if sc >= 0.62:
            alts.append(title)
        if sc > best_score:
            best, best_score = p, sc
    if not sure and (not best or best_score < 0.8 or _WORK.search(best.get("description", ""))):
        try:  # try the natural-kind route ("blue lotus" is a plant, not a Tintin album)
            nat = await wiki_candidates_by_text(client, query)
        except httpx.HTTPError:
            nat = []
        if nat:
            nat[0]["alternatives"] = [x["title"] for x in nat[1:4]] + [t for t in alts if t != nat[0]["title"]][:2]
            return nat[0]
    if not best or best_score < 0.62:
        return None
    return {"title": best["title"], "qid": (best.get("pageprops") or {}).get("wikibase_item"), "short": best.get("description", ""), "score": round(best_score, 2),
            "alternatives": [t for t in alts if t != best["title"] and not re.match(r"^(History|List|Lists|Index|Outline|Timeline|Controversies|Criticism) of ", t) and _norm(best["title"]) not in _norm(t) and _norm(t) not in _norm(best["title"])][:3]}


async def wiki_page(client, title: str) -> dict:
    d = await _get(client, WIKI, {
        "action": "query", "titles": title, "redirects": 1,
        "prop": "extracts|pageimages|pageprops|info|images", "explaintext": 1, "exsectionformat": "wiki", "exlimit": 1,
        "piprop": "original|thumbnail|name", "pithumbsize": 900, "inprop": "url", "imlimit": 40, "ppprop": "wikibase_item",
    })
    p = ((d.get("query") or {}).get("pages") or [{}])[0]
    return p


async def commons_info(client, file_titles: list[str]) -> list[dict]:
    """Licence/attribution + sized thumbnails for Commons files (by 'File:...' title)."""
    if not file_titles:
        return []
    d = await _get(client, COMMONS, {
        "action": "query", "titles": "|".join(file_titles[:20]), "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 640, "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription|Credit|AttributionRequired",
    })
    out = []
    for p in (d.get("query") or {}).get("pages") or []:
        ii = (p.get("imageinfo") or [None])[0]
        if not ii or not str(ii.get("mime", "")).startswith("image/"):
            continue
        md = ii.get("extmetadata") or {}
        v = lambda k: _strip_html((md.get(k) or {}).get("value", ""))  # noqa: E731
        out.append({
            "file": p.get("title"), "url": clean_url(ii.get("url")), "thumb_url": clean_url(ii.get("thumburl")) or clean_url(ii.get("url")),
            "width": ii.get("width"), "height": ii.get("height"),
            "source": "Wikimedia Commons", "source_url": clean_url(ii.get("descriptionurl")),
            "license": v("LicenseShortName"), "license_url": clean_url(v("LicenseUrl")), "credit": v("Artist")[:120],
            "title": re.sub(r"^File:|\.[a-z]{3,4}$", "", p.get("title", ""), flags=re.I).replace("_", " "),
        })
    return out


_IMG_BAD = re.compile(r"(logo|icon|flag of|symbol|signature|map of|map\b|locator|commons-|wiki|edit-|question|stub|disambig|ambox|padlock|folder|crystal|oojs|speaker|pictogram|coat of arms|\.svg)", re.I)
_IMG_OK = re.compile(r"\.(jpe?g|png|webp)$", re.I)


async def wikidata_entity(client, qid: str) -> dict:
    d = await _get(client, WIKIDATA, {"action": "wbgetentities", "ids": qid, "props": "claims|labels|descriptions|sitelinks", "languages": "en"})
    return (d.get("entities") or {}).get(qid) or {}


async def wikidata_labels(client, ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(i for i in ids if re.match(r"^Q\d+$", i)))[:60]
    out: dict[str, str] = {}
    for i in range(0, len(ids), 40):
        d = await _get(client, WIKIDATA, {"action": "wbgetentities", "ids": "|".join(ids[i:i + 40]), "props": "labels", "languages": "en"})
        for k, v in (d.get("entities") or {}).items():
            out[k] = ((v.get("labels") or {}).get("en") or {}).get("value", "")
    return out


def _claim_values(claims: dict, pid: str, current_only: bool = False) -> list[dict]:
    out, preferred = [], []
    for c in claims.get(pid, []) or []:
        if c.get("rank") == "deprecated":
            continue
        if current_only and (c.get("qualifiers") or {}).get("P582"):
            continue  # has an end date → no longer true
        dv = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if dv is not None:
            (preferred if c.get("rank") == "preferred" else out).append(dv)
    return preferred or out


# property → label, per family. Order = display order. (Generic ones apply to every family.)
PROPS: dict[str, list[tuple[str, str]]] = {
    "person": [("P569", "Born"), ("P570", "Died"), ("P19", "Birthplace"), ("P27", "Nationality"), ("P106", "Occupation"), ("P69", "Education"), ("P26", "Spouse"), ("P856", "Website")],
    "place": [("P17", "Country"), ("P131", "Located in"), ("P625", "Coordinates"), ("P571", "Built / founded"), ("P84", "Architect"), ("P149", "Architectural style"), ("P1435", "Heritage status"), ("P1082", "Population"), ("P2044", "Elevation (m)")],
    "animal": [("P225", "Scientific name"), ("P105", "Rank"), ("P171", "Parent taxon"), ("P141", "Conservation status"), ("P183", "Native to")],
    "plant": [("P225", "Scientific name"), ("P105", "Rank"), ("P171", "Parent taxon"), ("P141", "Conservation status"), ("P183", "Native to")],
    "instrument": [("P495", "Country of origin"), ("P1762", "Classification"), ("P61", "Inventor"), ("P571", "Introduced"), ("P1303", "Related to")],
    "vehicle": [("P176", "Manufacturer"), ("P571", "Introduced"), ("P577", "Released"), ("P516", "Engine"), ("P2067", "Mass"), ("P2043", "Length"), ("P2049", "Width"), ("P2048", "Height"), ("P2052", "Top speed"), ("P856", "Website")],
    "laptop": [("P176", "Manufacturer"), ("P577", "Released"), ("P879", "Processor"), ("P2067", "Mass"), ("P856", "Website")],
    "smartphone": [("P176", "Manufacturer"), ("P577", "Released"), ("P306", "Operating system"), ("P2067", "Mass"), ("P856", "Website")],
    "product": [("P176", "Manufacturer"), ("P577", "Released"), ("P571", "Introduced"), ("P2067", "Mass"), ("P856", "Website")],
    "company": [("P452", "Industry"), ("P571", "Founded"), ("P159", "Headquarters"), ("P112", "Founder"), ("P169", "CEO"), ("P1128", "Employees"), ("P856", "Website")],
    "software": [("P178", "Developer"), ("P577", "Released"), ("P306", "Platform"), ("P277", "Written in"), ("P275", "License"), ("P856", "Website")],
    "food": [("P495", "Country of origin"), ("P527", "Ingredients"), ("P1582", "Natural product of")],
    "thing": [("P495", "Country of origin"), ("P571", "Introduced"), ("P176", "Manufacturer"), ("P170", "Creator"), ("P856", "Website")],
}
_FAMILY_RULES: list[tuple[str, re.Pattern]] = [
    ("person", re.compile(r"^human$|fictional character|^person$", re.I)),
    ("animal", re.compile(r"dog breed|cat breed|breed of|horse breed|livestock|domesticated|bird|mammal|reptile|insect|fish", re.I)),
    ("instrument", re.compile(r"musical instrument|string instrument|percussion|wind instrument|chordophone|membranophone|aerophone|idiophone|lute|drum", re.I)),
    ("laptop", re.compile(r"laptop|notebook computer|netbook|ultrabook", re.I)),
    ("smartphone", re.compile(r"smartphone|mobile phone|feature phone|tablet computer|phablet|handheld device", re.I)),
    ("vehicle", re.compile(r"automobile|\bcars?\b|car model|motor ?cycle|vehicle|truck|sport utility|suv|aircraft|locomotive|ship class|bicycle|scooter|tractor", re.I)),
    ("software", re.compile(r"software|programming language|\w+[- ](?:oriented|based|level|scripting|markup|query) language|web browser|operating system|video game|mobile app|framework|library|web application|algorithm implementation|chatbot|language model", re.I)),
    ("company", re.compile(r"business|company|enterprise|corporation|organi[sz]ation|brand|manufacturer|startup|conglomerate|bank|airline|publisher|record label", re.I)),
    ("food", re.compile(r"dish|food|beverage|ingredient|spice|fruit|vegetable|cuisine|drink|dessert|snack", re.I)),
    ("place", re.compile(r"building|monument|mausoleum|temple|church|mosque|city|town|village|country|state|district|landmark|tourist|heritage|national park|mountain|river|lake|island|bridge|castle|fort|palace|museum|stadium|airport|region|capital", re.I)),
    ("product", re.compile(r"product|device|gadget|camera|console|headphone|processor|graphics|gpu|cpu|computer|television|appliance|furniture|tool|watch|machine|instrument|equipment|weapon", re.I)),
]
_ABSTRACT = re.compile(r"field of study|academic discipline|branch of|scientific theory|concept|theorem|mathematical|phenomenon|methodology|\bprocess\b|technique|paradigm|\bidea\b|philosophy|ideology|school of thought|physical quantity|unit of|scientific method|genre", re.I)


def family_from_types(type_labels: list[str], has_coords: bool, taxon_kingdom: str = "") -> tuple[str, bool]:
    """(family, abstract). `abstract` = a concept/field/theory rather than a concrete named thing → no card."""
    joined = " | ".join(type_labels)
    if type_labels and _ABSTRACT.search(type_labels[0]) and not has_coords:
        return "thing", True  # the main "instance of" is a field / concept / theory → not a concrete thing
    if re.search(r"taxon|species|genus|family", joined, re.I) or taxon_kingdom:
        return ("plant" if re.search(r"plant|fung|flower|tree|herb|shrub|Plantae", joined + " " + taxon_kingdom, re.I) else "animal"), False
    for fam, rx in _FAMILY_RULES:
        if rx.search(joined):
            return fam, False
    if has_coords:
        return "place", False
    if _ABSTRACT.search(joined):
        return "thing", True
    return "thing", False


def _fmt_time(v: dict) -> str:
    t = str(v.get("time", ""))
    m = re.match(r"^[+-](\d{4})-(\d\d)-(\d\d)", t)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    prec = v.get("precision", 11)
    try:
        if prec >= 11 and mo != "00" and d != "00":
            return datetime(int(y), int(mo), int(d)).strftime("%d %B %Y").lstrip("0")
        if prec == 10 and mo != "00":
            return datetime(int(y), int(mo), 1).strftime("%B %Y")
    except ValueError:
        pass
    return str(int(y))


SINGLE = {"P856", "P1128", "P1082", "P169", "P2044", "P569", "P570", "P577", "P571", "P159"}
_UNITS = {"Q11573": "m", "Q174789": "mm", "Q11574": "s", "Q11570": "kg", "Q28111": "kg", "Q828224": "km", "Q180154": "km/h", "Q182429": "m/s", "Q25235": "m", "Q174728": "cm", "Q11571": "g", "Q3710": "ft"}


async def wikidata_facts(client, qid: str) -> dict:
    """{"family", "abstract", "types": [...], "attributes": [{label,value,source_url}], "description"}"""
    ent = await wikidata_entity(client, qid)
    claims = ent.get("claims") or {}
    p31 = [v.get("id") for v in _claim_values(claims, "P31") if isinstance(v, dict)]
    p279 = [v.get("id") for v in _claim_values(claims, "P279") if isinstance(v, dict)]
    p171 = [v.get("id") for v in _claim_values(claims, "P171") if isinstance(v, dict)]
    has_coords = bool(_claim_values(claims, "P625"))
    labels = await wikidata_labels(client, p31 + p279 + p171)
    types = [labels.get(i, "") for i in p31 + p279 if labels.get(i)]
    # a taxon's kingdom decides plant vs animal; walk up the parent taxa a few steps
    kingdom = ""
    if re.search(r"taxon", " ".join(types), re.I) and p171:
        cur = p171[0]
        for _ in range(26):
            try:
                pe = await wikidata_entity(client, cur)
            except httpx.HTTPError:
                break
            lab = ((pe.get("labels") or {}).get("en") or {}).get("value", "")
            low = lab.lower()
            if low in ("plantae", "viridiplantae", "embryophyte", "tracheophyte", "plant", "angiosperms", "flowering plant", "magnoliopsida", "eudicots"):
                kingdom = "Plantae"
                break
            if low in ("animalia", "animal", "chordata", "chordate", "vertebrate", "mammalia", "mammal", "aves", "bird", "arthropod", "insect", "reptile", "actinopterygii", "fish"):
                kingdom = "Animalia"
                break
            if low in ("fungi", "bacteria", "archaea", "protista"):
                kingdom = lab
                break
            nxt = [v.get("id") for v in _claim_values(pe.get("claims") or {}, "P171") if isinstance(v, dict)]
            if not nxt:
                break
            cur = nxt[0]
    family, abstract = family_from_types(types, has_coords, kingdom)
    # collect values
    want = PROPS.get(family, PROPS["thing"])
    raw: dict[str, list] = {pid: _claim_values(claims, pid, current_only=True) for pid, _ in want}
    ids = [v.get("id") for vals in raw.values() for v in vals if isinstance(v, dict) and v.get("id")]
    unit_ids = [v.get("unit", "").rsplit("/", 1)[-1] for vals in raw.values() for v in vals if isinstance(v, dict) and v.get("unit")]
    lab2 = await wikidata_labels(client, ids + unit_ids)
    attrs = []
    for pid, label in want:
        vals = raw.get(pid) or []
        shown: list[str] = []
        for v in vals[: 1 if pid in SINGLE else 3]:
            if isinstance(v, dict) and "id" in v:
                s = lab2.get(v["id"], "")
            elif isinstance(v, dict) and "time" in v:
                s = _fmt_time(v)
            elif isinstance(v, dict) and "amount" in v:
                amt = str(v["amount"]).lstrip("+")
                u = _UNITS.get(v.get("unit", "").rsplit("/", 1)[-1]) or lab2.get(v.get("unit", "").rsplit("/", 1)[-1], "")
                s = f"{float(amt):g} {u}".strip() if re.match(r"^-?\d+(\.\d+)?$", amt) else amt
            elif isinstance(v, dict) and "latitude" in v:
                s = f"{v['latitude']:.4f}, {v['longitude']:.4f}"
            elif isinstance(v, str):
                s = v
            else:
                s = ""
            if s and s not in shown:
                shown.append(s)
        if shown:
            attrs.append({"label": label, "value": "; ".join(shown), "source_url": f"https://www.wikidata.org/wiki/{qid}"})
    return {"family": family, "abstract": abstract, "types": types, "attributes": attrs,
            "description": ((ent.get("descriptions") or {}).get("en") or {}).get("value", "")}


# =============================================================== 4. web enrichment (products, prices, things Wikipedia lacks)
OFFICIAL_HINT = re.compile(r"\.(gov|edu|ac)(\.[a-z]{2})?$|\.gov\.|nic\.in$|who\.int|un\.org|wikipedia\.org", re.I)
LOW_QUALITY = re.compile(r"(reddit|quora|pinterest|facebook|instagram|twitter|x\.com|tiktok|youtube|youtu\.be|aliexpress|alibaba|dhgate|temu|scribd|slideshare|medium\.com|answers\.com|wikihow)", re.I)
REVIEW_SITES = re.compile(r"(notebookcheck|gsmarena|rtings|tomshardware|cnet|theverge|techradar|pcmag|digitaltrends|engadget|91mobiles|gadgets360|autocar|zigwheels|cardekho|carwale|team-bhp|bbc|britannica|nationalgeographic|nature\.com|sciencedirect|reuters|forbes|ndtv|hindustantimes|indianexpress|timesofindia)", re.I)


def authority(url: str, name: str) -> int:
    """Higher = more trustworthy for facts. 3 official/institutional, 2 reputable publication, 1 other, 0 avoid."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if LOW_QUALITY.search(host):
        return 0
    toks = [t for t in _tokens(name) if len(t) > 2 and not t.isdigit()]
    if toks and (host.split(".")[0] in toks or any(t in host.replace("-", "") for t in toks[:2])):
        return 3
    if OFFICIAL_HINT.search(host):
        return 3
    if REVIEW_SITES.search(host):
        return 2
    return 1


def _meta(html: str, *names: str) -> str:
    for n in names:
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)["\']', html, re.I) or \
            re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(n)}["\']', html, re.I)
        if m:
            return m.group(1).strip()
    return ""


async def _fetch_page(url: str) -> Optional[dict]:
    from . import tools  # local import: tools imports many modules
    try:
        body, ctype, final = await tools._download(url)
    except Exception:
        return None
    if "html" not in ctype and ctype:
        return None
    html = body.decode("utf-8", "replace")
    try:
        text, title, date = await asyncio.to_thread(tools._html_to_text, html, final)
    except Exception:
        return None
    if len((text or "").strip()) < 200:
        return None
    img = _meta(html, "og:image", "twitter:image")
    if img.startswith("//"):
        img = "https:" + img
    elif img.startswith("/"):
        p = urlparse(final)
        img = f"{p.scheme}://{p.netloc}{img}"
    return {"url": final, "title": (title or _meta(html, "og:title"))[:160], "text": text, "date": date or _meta(html, "article:modified_time", "article:published_time", "og:updated_time")[:25], "image": clean_url(img), "site": _meta(html, "og:site_name") or (urlparse(final).hostname or "").removeprefix("www.")}


async def verify_image(client, url: str) -> bool:
    """The URL must be public http(s), answer, and be an image. (We never keep the bytes.)"""
    from . import tools
    if not clean_url(url):
        return False
    try:
        await tools._check_url(url)  # refuses private / local addresses
        r = await client.get(url, headers={"Range": "bytes=0-2048"})
        return r.status_code < 400 and r.headers.get("content-type", "").lower().startswith("image/")
    except Exception:
        return False


def _ddg_images(query: str, n: int = 10) -> list[dict]:
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception:
            return []
    try:
        return list(DDGS().images(query, max_results=n, safesearch="moderate"))
    except Exception:
        return []


def relevant_image(name: str, item: dict) -> bool:
    """A web image is kept only if its title / page mentions most of the entity's distinctive words."""
    toks = [t for t in _tokens(name) if len(t) > 1]
    if not toks:
        return False
    hay = _norm(" ".join([item.get("title", ""), item.get("url", ""), item.get("image", "")]))
    hits = sum(1 for t in toks if t in hay or _norm(t) in hay)
    return hits >= max(1, int(len(toks) * 0.6 + 0.5))


GROUND_SYSTEM = (
    "You extract facts about ONE named product/thing from web page text. Reply with ONLY JSON: "
    '{"attributes": [{"label": "short name e.g. Processor", "value": "value copied exactly as written in the text"}], '
    '"price": {"value": "e.g. $1,999 or ₹89,999, copied exactly", "note": "which configuration/variant it is for"} or null, '
    '"category": "one or two words, e.g. laptop, smartphone, SUV, software"}. '
    "Copy values EXACTLY as they appear. Only include a fact if the text states it for this exact product/model. "
    "Do not guess, convert, or combine different configurations. Skip anything not stated. Max 20 attributes."
)


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _in_text(value: str, raw_text: str, collapsed: str, normed: str) -> bool:
    """Is `value` really in the page? Anything with digits must match exactly (so "$999" can never hide inside "4,59,990");
    plain words may differ in punctuation/spacing only."""
    v = _collapse(value)
    if len(v) < 2:
        return False
    if re.search(r"\d", v):
        return re.search(r"(?<![\w.,])" + re.escape(v) + r"(?![\w])", collapsed) is not None
    return v in collapsed or (len(_norm(value)) >= 3 and _norm(value) in normed)


def ground_attributes(raw: dict, page_text: str, url: str, date: str, today: str) -> tuple[list[dict], Optional[dict]]:
    """Keep only attributes whose value really appears in the page text. Returns (attributes, price)."""
    collapsed, normed = _collapse(page_text), _norm(page_text)
    attrs, seen = [], set()
    for a in (raw.get("attributes") or [])[:30]:
        if not isinstance(a, dict):
            continue
        label, value = str(a.get("label", "")).strip()[:40], str(a.get("value", "")).strip()[:160]
        if not label or not value or label.lower() in seen:
            continue
        if not _in_text(value, page_text, collapsed, normed):
            continue  # not literally in the source -> dropped
        seen.add(label.lower())
        attrs.append({"label": label, "value": value, "source_url": url})
    price = None
    p = raw.get("price")
    if isinstance(p, dict) and p.get("value"):
        pv = str(p["value"]).strip()
        if re.search(r"\d", pv) and re.search(r"[$€£₹¥]|rs\.?|inr|usd|eur", pv, re.I) and _in_text(pv, page_text, collapsed, normed):
            price = {"label": "Price", "value": pv[:60], "source_url": url, "volatile": True,
                     "as_of": date or today, "note": (str(p.get("note") or "")[:100] + " · " if p.get("note") else "") + "Prices change — confirm with the seller"}
    return attrs, price


# =============================================================== 5. card building + validation
def guess_family(name: str, text: str = "") -> str:
    s = f"{name} {text}".lower()
    for fam, rx in (
        ("laptop", r"\b(laptop|notebook|zephyrus|macbook|thinkpad|ideapad|xps|chromebook|legion|predator|omen)\b"),
        ("smartphone", r"\b(smartphone|iphone|galaxy|pixel|oneplus|redmi|realme|phone)\b"),
        ("vehicle", r"\b(car|suv|sedan|hatchback|motorcycle|bike|truck|thar|swift|creta|nexon|tesla|mustang)\b"),
        ("software", r"\b(software|app|application|framework|library|operating system|browser|ide|sdk)\b"),
        ("company", r"\b(company|corporation|inc|ltd|brand|startup)\b"),
    ):
        if re.search(rx, s):
            return fam
    return "product"


def validate_card(card: dict) -> dict:
    """Return a cleaned copy that is safe to render. Raises ValueError if there is nothing worth showing."""
    if not isinstance(card, dict) or not isinstance(card.get("entity"), dict):
        raise ValueError("card has no entity")
    e = card["entity"]
    name = str(e.get("name") or "").strip()[:120]
    if not name:
        raise ValueError("entity has no name")
    fam = e.get("family") if e.get("family") in FAMILIES else "thing"

    def img(i):
        if not isinstance(i, dict):
            return None
        url = clean_url(i.get("url")) or clean_url(i.get("thumb_url"))
        if not url:
            return None
        return {"url": url, "thumb_url": clean_url(i.get("thumb_url")) or url, "title": str(i.get("title") or "")[:140],
                "source": str(i.get("source") or "")[:80], "source_url": clean_url(i.get("source_url")),
                "license": str(i.get("license") or "")[:60], "license_url": clean_url(i.get("license_url")), "credit": str(i.get("credit") or "")[:120]}

    attrs, seen = [], set()
    for a in e.get("attributes") or []:
        if not isinstance(a, dict):
            continue
        lab, val = str(a.get("label") or "").strip()[:48], str(a.get("value") or "").strip()[:240]
        if not lab or not val or lab.lower() in seen:
            continue
        seen.add(lab.lower())
        item = {"label": lab, "value": val}
        for k in ("source_url",):
            if clean_url(a.get(k)):
                item[k] = clean_url(a[k])
        if a.get("volatile"):
            item.update(volatile=True, as_of=str(a.get("as_of") or "")[:40], note=str(a.get("note") or "")[:160])
        attrs.append(item)
    secs = []
    for sct in e.get("sections") or []:
        if isinstance(sct, dict) and str(sct.get("title") or "").strip() and len(str(sct.get("content") or "").strip()) > 20:
            secs.append({"title": str(sct["title"]).strip()[:60], "content": str(sct["content"]).strip()[:1500]})
    srcs, seen_u = [], set()
    for x in e.get("sources") or []:
        if isinstance(x, dict) and clean_url(x.get("url")) and clean_url(x["url"]) not in seen_u:
            seen_u.add(clean_url(x["url"]))
            srcs.append({"title": str(x.get("title") or urlparse(x["url"]).hostname or "")[:120], "url": clean_url(x["url"]),
                         **({"date": str(x["date"])[:30]} if x.get("date") else {}), **({"kind": str(x["kind"])[:20]} if x.get("kind") else {})})
    gallery, seen_g = [], set()
    main = img(e.get("image"))
    if main:
        seen_g.add(main["url"])
    for g in e.get("gallery") or []:
        gi = img(g)
        if gi and gi["url"] not in seen_g:
            seen_g.add(gi["url"])
            gallery.append(gi)
    out = {
        "name": name, "category": str(e.get("category") or fam)[:60], "family": fam,
        "description": str(e.get("description") or "").strip()[:700], "image": main, "gallery": gallery[:MAX_GALLERY],
        "attributes": attrs[:MAX_ATTRS], "sections": secs[:MAX_SECTIONS], "sources": srcs[:12],
        "notes": [str(n)[:220] for n in (e.get("notes") or []) if str(n).strip()][:5],
        "identity": e.get("identity") if isinstance(e.get("identity"), dict) else {},
    }
    if not (out["description"] or out["attributes"] or out["sections"] or out["image"]):
        raise ValueError("nothing to show")
    return {"type": "entity_card", "mode": card.get("mode") if card.get("mode") in ("card", "images") else "card", "entity": out}


# =============================================================== 6. the lookup pipeline
async def lookup(s: dict, model: str, query: str, *, mode: str = "card", volatile: bool = False, sure: bool = True, search_fn=None, client=None) -> dict:
    """Identify `query`, gather facts + images, return {"card": validated card, "context": text for the LLM, "sources": [...]}
    or {"card": None, "reason": ...} when nothing trustworthy was found."""
    own = client is None
    client = client or new_client()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    t0 = time.monotonic()
    try:
        entity: dict = {"name": query.strip(), "category": "", "family": "thing", "description": "", "image": None, "gallery": [],
                        "attributes": [], "sections": [], "sources": [], "notes": [], "identity": {"confidence": "low"}}
        context: list[str] = []
        abstract = False
        hit = None
        try:
            hit = await wiki_search(client, query, sure)
        except httpx.HTTPError:
            hit = None
        wiki_text = ""
        if hit:
            page = await wiki_page(client, hit["title"])
            plain = (page.get("extract") or "")[:80000]
            intro, secs = split_sections(plain)
            qid = (page.get("pageprops") or {}).get("wikibase_item") or hit.get("qid")
            facts = {"family": "thing", "abstract": False, "types": [], "attributes": [], "description": hit.get("short", "")}
            if qid:
                try:
                    facts = await wikidata_facts(client, qid)
                except httpx.HTTPError:
                    pass
            abstract = facts["abstract"]
            entity.update(
                name=page.get("title") or hit["title"], family=facts["family"],
                category=(facts["types"][0] if facts["types"] else hit.get("short") or facts["family"]),
                description=_trim(intro, 650) or facts["description"], attributes=facts["attributes"], sections=secs[:MAX_SECTIONS],
                identity={"qid": qid, "wikipedia": page.get("fullurl"), "match": hit["score"], "confidence": "high" if hit["score"] >= 0.85 else "medium",
                          "summary": hit.get("short", "")},
            )
            if hit.get("alternatives"):
                entity["notes"].append("Other possible matches: " + ", ".join(hit["alternatives"]) + ". Say which one you meant if this isn’t it.")
            if page.get("fullurl"):
                entity["sources"].append({"title": f"{entity['name']} — Wikipedia", "url": page["fullurl"], "kind": "encyclopedia"})
            if qid:
                entity["sources"].append({"title": f"{entity['name']} — Wikidata", "url": f"https://www.wikidata.org/wiki/{qid}", "kind": "database"})
            # images: the lead image first, then other photos used in the article (all with Commons licence info)
            files = []
            lead = page.get("pageimage")
            if lead:
                files.append("File:" + lead)
            files += [i.get("title") for i in page.get("images") or [] if isinstance(i, dict) and i.get("title") and _IMG_OK.search(i["title"]) and not _IMG_BAD.search(i["title"])]
            try:
                infos = await commons_info(client, list(dict.fromkeys(files))[:16])
            except httpx.HTTPError:
                infos = []
            infos.sort(key=lambda x: 0 if x["file"] == ("File:" + (lead or "").replace("_", " ")) else 1)
            if infos:
                entity["image"] = infos[0]
                entity["gallery"] = infos[1:1 + MAX_GALLERY]
            wiki_text = plain
            if (abstract or (not sure and facts["family"] == "thing")) and mode != "images":
                return {"card": None, "reason": "concept", "family": facts["family"]}  # a general idea, not a named thing → plain answer
        # ---- web path: no Wikipedia page, or something that changes / is a product
        need_web = (not hit) or volatile or entity["family"] in ("laptop", "smartphone", "product", "vehicle", "software") or mode == "images"
        pages: list[dict] = []
        if need_web:
            try:
                search = search_fn or _default_search
                suffix = " price" if volatile else (" specifications" if entity["family"] in ("laptop", "smartphone", "product", "vehicle", "software", "thing") else "")
                items = await search(s, f"{entity['name']}{suffix}" if hit or True else query)
            except Exception:
                items = []
            items = [i for i in items if clean_url(i.get("url")) and authority(i["url"], entity["name"]) > 0]
            items.sort(key=lambda i: -authority(i["url"], entity["name"]))
            if not hit and items:
                # no encyclopedia page: describe from the best search snippet (text written by a source, not by us)
                entity["description"] = _trim(items[0].get("snippet", ""), 400)
                entity["family"] = guess_family(entity["name"], " ".join(i.get("snippet", "") for i in items[:3]))
                entity["category"] = entity["family"]
                entity["identity"] = {"confidence": "low", "note": "No encyclopedia entry found; built from web sources."}
            fetched = await asyncio.gather(*[_fetch_page(i["url"]) for i in items[:3]])
            pages = [p for p in fetched if p]
            for i in items[:5]:
                entity["sources"].append({"title": i.get("title") or urlparse(i["url"]).hostname, "url": i["url"], "kind": {3: "official", 2: "publication", 1: "web"}.get(authority(i["url"], entity["name"]), "web")})
            # grounded spec / price extraction from the single best page (never mixing pages = never mixing configurations)
            if pages and entity["family"] in ("laptop", "smartphone", "product", "vehicle", "software", "thing", "company", "food") and model:
                best = max(pages, key=lambda p: (authority(p["url"], entity["name"]), len(p["text"])))
                try:
                    raw_json = await llm.complete(
                        s, model, [{"role": "system", "content": GROUND_SYSTEM},
                                   {"role": "user", "content": f"Product: {entity['name']}\nPage: {best['url']}\n\n{best['text'][:6000]}"}],
                        max_tokens=700, json_mode=True, num_ctx=4096)
                    raw = json.loads(re.search(r"\{.*\}", raw_json or "", re.S).group(0)) if raw_json else {}
                except Exception:
                    raw = {}
                attrs, price = ground_attributes(raw, best["text"], best["url"], best.get("date", ""), today)
                have = {a["label"].lower() for a in entity["attributes"]}
                entity["attributes"] += [a for a in attrs if a["label"].lower() not in have]
                if price:
                    entity["attributes"].insert(0, price)
                elif volatile:
                    entity["notes"].append("No current price could be verified from the sources found.")
                if attrs:
                    entity["notes"].append(f"Specifications are quoted from {best['site']}; other configurations or regions may differ.")
                if not entity["description"]:
                    entity["description"] = _trim(best["text"], 400)
                if raw.get("category") and not hit:
                    entity["category"] = str(raw["category"])[:40]
            # images from the web when there is no licensed Commons image (or the user asked for pictures)
            if not entity["image"] or mode == "images":
                cands: list[dict] = []
                for p in pages:
                    if p.get("image"):
                        cands.append({"url": p["image"], "thumb_url": p["image"], "title": p["title"], "source": p["site"], "source_url": p["url"]})
                ddg = await asyncio.to_thread(_ddg_images, entity["name"], 12)
                for d in ddg:
                    it = {"url": clean_url(d.get("image")), "thumb_url": clean_url(d.get("thumbnail")) or clean_url(d.get("image")), "title": d.get("title", ""),
                          "source": (urlparse(d.get("url", "")).hostname or d.get("source") or "").removeprefix("www."), "source_url": clean_url(d.get("url"))}
                    if it["url"] and relevant_image(entity["name"], it):
                        cands.append(it)
                ok = await asyncio.gather(*[verify_image(client, c["thumb_url"]) for c in cands[:10]])
                good, seen = [], set(i["url"] for i in ([entity["image"]] if entity["image"] else []) + entity["gallery"])
                for c, k in zip(cands[:10], ok):
                    if k and c["url"] not in seen:
                        seen.add(c["url"]); good.append(c)
                if good:
                    if not entity["image"]:
                        entity["image"] = good[0]; good = good[1:]
                    entity["gallery"] = (entity["gallery"] + good)[:MAX_GALLERY]
                    entity["notes"].append("Web images stay on their source sites; open the source page to check usage rights.")
        # ---- assemble
        try:
            card = validate_card({"type": "entity_card", "mode": mode, "entity": entity})
        except ValueError:
            return {"card": None, "reason": "nothing_found"}
        card["retrieved"] = today
        card["ms"] = round((time.monotonic() - t0) * 1000)
        e = card["entity"]
        lines = [f"ENTITY: {e['name']} ({e['category']}). Identification confidence: {e['identity'].get('confidence', 'unknown')}."]
        if e["description"]:
            lines.append("Summary: " + e["description"])
        if e["attributes"]:
            lines.append("Verified facts: " + "; ".join(f"{a['label']}: {a['value']}" for a in e["attributes"][:20]))
        for sc in e["sections"][:5]:
            lines.append(f"{sc['title']}: {sc['content']}")
        for p in pages[:2]:
            lines.append(f"[web: {p['site']}, {p.get('date') or 'date not stated'}] {_trim(p['text'], 1200)}")
        return {"card": card, "context": "\n".join(lines), "sources": e["sources"]}
    finally:
        if own:
            await client.aclose()


async def _default_search(s: dict, query: str) -> list[dict]:
    from . import tools
    return await tools._search_items(s, query)


# =============================================================== 7. comparison
def compare(cards: list[dict]) -> dict:
    ents = [c["entity"] for c in cards]
    labels: list[str] = []
    for e in ents:
        for a in e["attributes"]:
            if a["label"] not in labels:
                labels.append(a["label"])
    rows = []
    for lab in labels:
        vals = []
        for e in ents:
            hit = next((a for a in e["attributes"] if a["label"] == lab), None)
            vals.append(hit["value"] if hit else "")
        filled = [v for v in vals if v]
        if not filled:
            continue
        rows.append({"label": lab, "values": vals, "differs": len({_norm(v) for v in filled}) > 1 or len(filled) < len(vals),
                     "volatile": any(a.get("volatile") for e in ents for a in e["attributes"] if a["label"] == lab)})
    rows.sort(key=lambda r: (not r["differs"], labels.index(r["label"])))
    notes = ["Only facts found in sources are shown; blank cells mean a value wasn't found, not that it doesn't exist."]
    if any(r["volatile"] for r in rows):
        notes.append("Prices are as of the retrieval date and may differ by seller, region and configuration.")
    fams = {e["family"] for e in ents}
    if len(fams) > 1:
        notes.append("These items are different kinds of things, so some attributes don't line up.")
    return {"type": "entity_compare", "entities": ents, "rows": rows[:40], "notes": notes}


# =============================================================== 8. chat integration
def last_entity(history: list[dict]) -> Optional[dict]:
    """The entity most recently shown as a card in this conversation (so "what are her best movies?" knows who "her" is)."""
    for m in reversed(history or []):
        if m.get("role") != "assistant":
            continue
        for t in reversed((m.get("meta") or {}).get("tools") or []):
            d = t.get("detail") or {}
            if t.get("name") == "entity_card" and t.get("ok"):
                if d.get("card"):
                    e = d["card"]["entity"]
                    return {"name": e["name"], "category": e["category"], "description": e["description"][:300]}
                if d.get("compare"):
                    return {"name": " vs ".join(x["name"] for x in d["compare"]["entities"]), "category": "comparison", "description": ""}
    return None


def followup_hint(ent: dict) -> str:
    return (f"\n\nCONTEXT: The user was just shown an information card about \u201c{ent['name']}\u201d ({ent['category']}). Words like she, he, her, his, it, its, they, "
            f"this, that in the next messages refer to {ent['name']} unless the user names something else. When you search the web, include \u201c{ent['name']}\u201d in the query."
            + (f" Known summary: {ent['description']}" if ent.get("description") else ""))


ANSWER_RULES = (
    "How to answer: do not repeat the card's list of facts. Answer what the user actually asked in a few clear sentences using only the information above "
    "(or web tools if you need more). Say so when something is not in the sources. Never invent prices, specifications, dates or image links, and do not output HTML or image URLs. "
    "Cite sources as [n] where numbers are given."
)


async def prelude(ctx, pl: dict):
    """Run the lookup for a planned entity request. Yields the same UI events as a tool call and extends the system prompt.
    Yields nothing at all when the topic turns out to be a general concept or nothing trustworthy is found."""
    from . import agent
    st = ctx.state
    names, mode = pl["entities"], pl["mode"]
    entry = {"id": f"{len(st['tools'])}", "name": "entity_card", "args": {"query": " | ".join(names), "mode": mode}, "status": "running"}
    st["tools"].append(entry)
    yield {"type": "tool_start", "id": entry["id"], "name": "entity_card", "args": entry["args"]}
    results = await asyncio.gather(
        *[lookup(ctx.s, ctx.model, n, mode="images" if mode == "images" else "card", volatile=pl["volatile"], sure=pl["sure"]) for n in names],
        return_exceptions=True)
    cards = [r["card"] for r in results if isinstance(r, dict) and r.get("card")]
    ctxs = [r["context"] for r in results if isinstance(r, dict) and r.get("card")]
    if not cards or (mode == "compare" and len(cards) < 2):
        st["tools"].remove(entry)
        yield {"type": "tool_result", "id": entry["id"], "ok": False, "summary": "", "detail": {}, "files": [], "hidden": True}
        return
    sources = []
    if mode == "compare":
        detail = {"compare": compare(cards), "cards": cards}
        summary = "Compared " + " vs ".join(c["entity"]["name"] for c in cards)
        for c in cards:
            sources += c["entity"]["sources"][:3]
    else:
        detail = {"card": cards[0]}
        summary = cards[0]["entity"]["name"] + (f" · {cards[0]['entity']['category']}" if cards[0]["entity"]["category"] else "")
        sources = cards[0]["entity"]["sources"][:6]
    nums = [agent._add_source(st, x["title"], x["url"]) for x in sources]
    entry.update(status="done", ok=True, summary=summary, detail=detail, files=[], model_text="")
    yield {"type": "tool_result", "id": entry["id"], "ok": True, "summary": summary, "detail": detail, "files": [], "declined": False}
    yield {"type": "sources", "sources": st["sources"]}
    src_lines = "Sources: " + "; ".join(f"[{n}] {x['title']}" for n, x in zip(nums, sources))
    extra = "\n\nENTITY LOOKUP (done by the app; the user is already looking at a visual card with the basics, images and sources, right above your reply):\n" + "\n\n".join(ctxs) + "\n" + src_lines
    if mode == "images":
        extra += "\nThe user asked for pictures; they are in the card. Reply with one short sentence."
    elif mode == "compare":
        extra += "\nThe card is a side-by-side table. Summarise the most important differences and who each is best for, in a short paragraph. Prices may be missing: say so rather than guessing."
    else:
        extra += "\n" + ANSWER_RULES
    ctx.prompt[0]["content"] += extra
