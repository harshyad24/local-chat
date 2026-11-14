"""Tool implementations for the agent loop.

Every tool is a coroutine `(settings, chat_id, args) -> ToolResult`.
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

from . import charts, dashboards, db, library, mcp_client, n8n

UA = "Mozilla/5.0 (compatible; LocalChat/1.0; +local)"
WIKI_UA = "LocalChat/1.0 (personal local app; +http://127.0.0.1:8000)"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
RUNNER = Path(__file__).with_name("sandbox_runner.py")
MAX_FETCH_BYTES = 4_000_000


class ToolError(Exception):
    """Expected failure; the message is shown to the model."""


@dataclass
class ToolResult:
    ok: bool
    content: str  # what the model sees
    summary: str = ""  # short text for the UI
    detail: dict = field(default_factory=dict)  # structured data for the expandable UI block
    files: list[dict] = field(default_factory=list)  # generated files [{name,url,mime}]
    sources: list[dict] = field(default_factory=list)  # [{title,url}] to be numbered as citations


# ------------------------------------------------------------------ specs
def _fn(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


SPECS = {
    "create_chart": charts.spec_for_model(),
    "create_dashboard": dashboards.spec_for_model(),
    "create_map": _fn(
        "create_map",
        "Show places on an interactive map (OpenStreetMap), optionally joined by a route in the order given. Use it when the user asks for "
        "a map, a route, an itinerary or where places are. Give place NAMES (the app looks up the coordinates; never guess coordinates). "
        "Add `near` (a city or country) for ambiguous names.",
        {
            "title": {"type": "string"},
            "near": {"type": "string", "description": "City or country that narrows ambiguous names, e.g. 'Goa, India'"},
            "route": {"type": "boolean", "description": "Join the points in order with a line and show the driving distance"},
            "points": {"type": "array", "description": "Places to show",
                       "items": {"type": "object", "properties": {"name": {"type": "string"}, "group": {"type": "string", "description": "Category, e.g. hotel, beach, day 1"},
                                                                  "note": {"type": "string"}, "value": {"type": "number"}}, "required": ["name"]}},
        },
        ["points"],
    ),
    "build_n8n_workflow": n8n.spec_for_model(),
    "convert_document": _fn(
        "convert_document",
        "Convert a document with LibreOffice (e.g. docx to pdf, xlsx to csv, pptx to pdf). `file` is an attached file's id or "
        "the name of a file in this chat's folder; `to` is the target format.",
        {"file": {"type": "string", "description": "file id or file name"},
         "to": {"type": "string", "description": "pdf, docx, odt, xlsx, ods, csv, pptx, odp, txt, html, rtf or png"}},
        ["file", "to"],
    ),
    "save_memory": _fn(
        "save_memory",
        "Save one lasting fact about the user (name, job, preferences, ongoing projects) so it is remembered in future chats. "
        "Only when the user asks you to remember something or states a stable personal fact. Never secrets.",
        {"fact": {"type": "string", "description": "One short sentence, e.g. 'Prefers metric units'"}},
        ["fact"],
    ),
    "search_knowledge": _fn(
        "search_knowledge",
        "Semantic search over the project's attached documents and the user's saved memories. Use it to find passages "
        "relevant to a question before answering from project files. Returns the best-matching excerpts with their file names.",
        {"query": {"type": "string", "description": "What to look for, in natural language"}},
        ["query"],
    ),
    "web_search": _fn(
        "web_search",
        "Search the web for current or factual information. Returns titles, URLs and snippets.",
        {"query": {"type": "string", "description": "The search query"}},
        ["query"],
    ),
    "fetch_page": _fn(
        "fetch_page",
        "Download a web page and return its readable text. Use it to read a result in full.",
        {"url": {"type": "string", "description": "Full http(s) URL"}},
        ["url"],
    ),
    "read_file": _fn(
        "read_file",
        "Read a file the user attached (PDF, DOCX, CSV, XLSX, text or code). Long files are "
        "returned in chunks; pass `offset` to continue.",
        {
            "file_id": {"type": "string", "description": "The file id shown in the attachment note"},
            "offset": {"type": "integer", "description": "Character offset to start from (default 0)"},
        },
        ["file_id"],
    ),
    "run_python": _fn(
        "run_python",
        "Run Python 3 code (pandas, numpy and matplotlib are available) and return stdout/stderr. "
        "Use it for exact calculations and for analysing attached data files. For ordinary charts prefer "
        "create_chart. No network access. Attached files are in the working directory under their original names. "
        "Files written there (e.g. plt.savefig('name.png')) are shown to the user.",
        {"code": {"type": "string", "description": "Complete Python program"}},
        ["code"],
    ),
    "current_datetime": _fn(
        "current_datetime", "Get the current local date, time and weekday.", {}, []
    ),
    "search_chat": _fn(
        "search_chat",
        "Search EARLIER messages of this same conversation: URLs, file names, numbers and instructions "
        "the user gave before. Always use it before telling the user you can't find something they "
        "said earlier. Use an empty query (or 'urls') to list every link mentioned so far.",
        {"query": {"type": "string", "description": "Keywords to look for, or 'urls'"}},
        [],
    ),
}


def enabled_tools(s: dict, *, web: bool, has_files: bool, convert: bool = False, n8n_on: bool = False, knowledge: bool = False,
                  maps: bool = False) -> list[str]:
    # built in and always on: the app draws charts and dashboards, the model only supplies data
    names: list[str] = ["create_chart", "create_dashboard"]
    if maps:
        names.append("create_map")  # only offered when the message is about maps/routes: fewer tools = better tool choice on small models
    if s.get("memory_enabled", True):
        names.append("save_memory")
    if web and s.get("tool_web_search", True):
        names.append("web_search")
    if web and s.get("tool_fetch_page", True):
        names.append("fetch_page")
    if has_files and s.get("tool_read_file", True):
        names.append("read_file")
    if knowledge:
        names.append("search_knowledge")  # only offered when the project has indexed documents
    if s.get("tool_run_python", True):
        names.append("run_python")  # built in; confirmed by the user before each run (Settings → Tools)
    if s.get("tool_datetime", True):
        names.append("current_datetime")
    if convert and s.get("tool_convert", True) and find_soffice():
        names.append("convert_document")
    if n8n_on and n8n.configured(s):
        names.append("build_n8n_workflow")
    names += mcp_client.enabled_tool_names(s)  # tools of the MCP servers the user connected and switched on
    return names


# ------------------------------------------------------------- web search
async def _search_items(s: dict, query: str) -> list[dict]:
    n = max(1, min(int(s.get("search_results") or 6), 12))
    provider = s.get("search_provider") or "ddgs"
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
        if provider == "searxng":
            base = (s.get("searxng_url") or "").rstrip("/")
            if not base:
                raise ToolError("SearXNG URL isn't set (Settings → Tools).")
            r = await client.get(f"{base}/search", params={"q": query, "format": "json"})
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                for x in r.json().get("results", [])[:n]
            ]
        if provider == "tavily":
            key = s.get("tavily_key")
            if not key:
                raise ToolError("Tavily API key isn't set (Settings → Tools).")
            r = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": n},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                for x in r.json().get("results", [])
            ]
        if provider == "brave":
            key = s.get("brave_key")
            if not key:
                raise ToolError("Brave API key isn't set (Settings → Tools).")
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": n},
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": re.sub(r"<[^>]+>", "", x.get("description", ""))}
                for x in r.json().get("web", {}).get("results", [])[:n]
            ]

    def ddg() -> list[dict]:
        try:
            from ddgs import DDGS
        except ImportError:  # older package name
            from duckduckgo_search import DDGS  # type: ignore
        last: Optional[Exception] = None
        # Individual engines are flaky; fall through them until one returns results.
        for backend in ("auto", "bing", "google", "yahoo", "duckduckgo"):
            try:
                found = list(DDGS().text(query, max_results=n, backend=backend))
                if found:
                    return found
            except TypeError:  # older packages have no `backend` argument
                return list(DDGS().text(query, max_results=n))
            except Exception as exc:
                last = exc
        if last and "no results" not in str(last).lower():
            raise last
        return []

    try:
        raw = await asyncio.to_thread(ddg)
    except Exception as exc:  # ddgs raises its own exception types
        raise ToolError(f"Search failed: {exc}")
    return [
        {"title": x.get("title", ""), "url": x.get("href") or x.get("url", ""), "snippet": x.get("body", "")}
        for x in raw
    ]


async def web_search(s: dict, chat_id: str, args: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("`query` is required.")
    try:
        items = await _search_items(s, query)
    except httpx.HTTPError as exc:
        raise ToolError(f"Search request failed: {exc.__class__.__name__}")
    items = [i for i in items if i["url"].startswith(("http://", "https://"))]
    if not items:
        return ToolResult(True, f'No results found for "{query}".', "No results", {"results": []})
    return ToolResult(
        True,
        "",  # filled in by the agent loop once citation numbers are assigned
        f"{len(items)} results",
        {"results": items},
        sources=[{"title": i["title"], "url": i["url"]} for i in items],
    )


# -------------------------------------------------------------- fetch page
async def _check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("Only http(s) URLs are supported.")
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, p.hostname, port, 0, socket.SOCK_STREAM)
    except socket.gaierror:
        raise ToolError(f"Could not resolve {p.hostname}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError("Refusing to fetch local or private-network addresses.")


async def _download(url: str) -> tuple[bytes, str, str]:
    """GET with manual redirects so every hop is checked. Returns (body, content_type, final_url)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0), follow_redirects=False, headers={"User-Agent": UA, "Accept-Language": "en"}
    ) as client:
        for _ in range(6):
            await _check_url(url)
            host = (urlparse(url).hostname or "").lower()
            # Wikimedia refuses browser-style agents; its policy asks for a tool name + contact URL instead
            client.headers["User-Agent"] = WIKI_UA if host.endswith(("wikipedia.org", "wikimedia.org", "wikidata.org")) else UA
            async with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise ToolError(f"The site answered HTTP {r.status_code}.")
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_FETCH_BYTES:
                        break
                return bytes(body), r.headers.get("content-type", "").lower(), url
    raise ToolError("Too many redirects.")


_DATE_HINT = re.compile(
    r"(?:last\s+updated|updated(?:\s+on)?|published(?:\s+on)?|posted(?:\s+on)?|as\s+of)\s*[:\-–]?\s*"
    r"((?:[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4})|(?:\d{1,2}\s+[A-Z][a-z]{2,8}\.?\s+\d{4})|(?:\d{4}-\d{2}-\d{2}))"
    r"(?:[ ,T]+(\d{1,2}:\d{2}\s*(?:[AaPp]\.?[Mm]\.?)?(?:\s*[A-Z]{2,4})?))?"
)


def find_page_date(text: str, meta_date: str = "") -> str:
    """The date the PAGE states for itself (never guessed). Empty string when none is found."""
    m = _DATE_HINT.search(text[:6000])  # the page's own wording keeps the time of day
    if m:
        return (m.group(1) + (" " + m.group(2) if m.group(2) else "")).strip()
    return str(meta_date) if meta_date else ""


def _html_to_text(html: str, url: str) -> tuple[str, str, str]:
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, output_format="txt")
    title = ""
    meta_date = ""
    try:
        meta = trafilatura.extract_metadata(html)
        title = (meta.title or "") if meta else ""
        meta_date = (getattr(meta, "date", "") or "") if meta else ""
    except Exception:
        pass
    if not text:  # last-resort tag stripping
        stripped = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)).strip()
    if not title:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return text, title, find_page_date(text or "", meta_date)


async def fetch_page(s: dict, chat_id: str, args: dict) -> ToolResult:
    url = str(args.get("url", "")).strip()
    if not url:
        raise ToolError("`url` is required.")
    from . import crawl
    mode = str(s.get("js_render", "auto"))  # auto: browser only when the plain reader fails or finds (almost) nothing
    body, ctype, final, text, title, page_date = b"", "", url, "", "", ""
    static_err: Optional[ToolError] = None
    try:
        for attempt in (1, 2):  # one retry for transient network trouble
            try:
                body, ctype, final = await _download(url)
                break
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ToolError(f"Could not download the page after 2 attempts: {exc.__class__.__name__}")
                await asyncio.sleep(1.0)
        if "pdf" in ctype or final.lower().endswith(".pdf"):
            text, title = await asyncio.to_thread(_pdf_text, io.BytesIO(body)), ""
        elif "html" in ctype or "xml" in ctype or "text" in ctype or not ctype:
            text, title, page_date = await asyncio.to_thread(_html_to_text, body.decode("utf-8", "replace"), final)
        else:
            raise ToolError(f"Unsupported content type: {ctype or 'unknown'}.")
    except ToolError as exc:
        # a site that blocks plain HTTP clients (403/429/503) can often still be read by a real browser
        if mode == "off" or not re.search(r"HTTP (?:401|403|429|503)", str(exc)):
            raise
        static_err = exc
    text = text.strip()
    is_pdf = "pdf" in ctype or final.lower().endswith(".pdf")
    rendered_by = ""
    # a short page is only suspicious when it was built by scripts (a tiny static page like example.com is simply complete)
    scripted = static_err is not None or body.lower().count(b"<script") >= 2 or b"<noscript" in body.lower()
    if mode != "off" and not is_pdf and (mode == "always" or (len(text) < 250 and scripted)) and crawl.available():
        got = await asyncio.to_thread(crawl.render, final if body else url, str(args.get("query") or ""))
        if got and (not text or mode == "always" or len(got["text"]) > len(text) * 1.5):
            text, title, final, rendered_by = got["text"].strip(), got["title"] or title, got["url"], "browser"
    if not text:
        raise static_err or ToolError("The page had no readable text (it may need JavaScript).")
    limit = int(s.get("page_char_limit") or 8000)
    total = len(text)
    query = str(args.get("query") or "").strip()
    if total > limit and query:  # a long page: keep the passages that talk about the question, not just the first screen
        from . import verify
        clipped = verify.focus(text, query, limit) + f"\n\n[Long page: showing the passages most relevant to \"{query[:80]}\", {limit:,} of {total:,} characters.]"
    else:
        clipped = text[:limit] + (f"\n\n[Truncated: showing {limit:,} of {total:,} characters.]" if total > limit else "")
    host = urlparse(final).hostname or final
    fetched = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    header = (
        f"[Page metadata] Date stated on the page: {page_date or 'none found (do not guess one)'}. "
        f"Retrieved by this app at: {fetched}.\n\n"
    )
    return ToolResult(
        True,
        header + clipped,
        host,
        {"url": final, "title": title, "chars": total, "excerpt": text[:400], "page_date": page_date, "fetched_at": fetched,
         **({"rendered_by": rendered_by} if rendered_by else {})},
        sources=[{"title": title or host, "url": final}],
    )


# --------------------------------------------------------------- read file
def _pdf_text(fh) -> str:
    from pypdf import PdfReader

    reader = PdfReader(fh)
    out = []
    for i, page in enumerate(reader.pages, 1):
        out.append(f"--- page {i} ---\n{(page.extract_text() or '').strip()}")
    return "\n\n".join(out)


def _docx_text(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    out = [p.text for p in doc.paragraphs if p.text.strip()]
    for t, table in enumerate(doc.tables, 1):
        out.append(f"\n[Table {t}]")
        for row in table.rows:
            out.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(out)


def _table_text(path: Path) -> str:
    import pandas as pd

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        frames = pd.read_excel(path, sheet_name=None)
    else:
        frames = {"data": pd.read_csv(path, sep=None, engine="python")}
    parts = []
    for name, df in frames.items():
        parts.append(f"## Sheet: {name}\nShape: {df.shape[0]:,} rows × {df.shape[1]} columns")
        parts.append("Columns:\n" + "\n".join(f"- {c} ({t})" for c, t in df.dtypes.items()))
        with pd.option_context("display.max_columns", 50, "display.width", 200):
            parts.append("First 20 rows:\n" + df.head(20).to_string())
            try:
                parts.append("Summary statistics:\n" + df.describe(include="all").T.to_string())
            except Exception:
                pass
    return "\n\n".join(parts)


def _extract(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        with open(path, "rb") as fh:
            text = _pdf_text(fh)
        if len(text.strip()) < 40:  # a scanned PDF has no text layer: read it with OCR
            from . import ocr
            if ocr.available():
                res = ocr.ocr_pdf(str(path))
                text = "\n\n".join(f"[Page {p['page']}]\n{p['text']}" for p in res["pages"] if p["text"].strip())
                if text:
                    text = "[Scanned document read with OCR; characters may contain mistakes.]\n\n" + text
        return text
    if ext == ".docx":
        return _docx_text(path)
    if ext in (".csv", ".tsv", ".xlsx", ".xlsm", ".xls"):
        return _table_text(path)
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ToolError("This looks like a binary file; it can't be read as text.")
    return data.decode("utf-8", "replace")


async def read_file(s: dict, chat_id: str, args: dict) -> ToolResult:
    meta = db.get_file(str(args.get("file_id", "")).strip())
    if not meta:
        raise ToolError("No attached file has that id. Use the id from the attachment note.")
    path = Path(meta["path"])
    if path.suffix.lower() in IMAGE_EXT:
        from . import ocr
        if ocr.available():  # text inside the image, read locally; the model still sees the picture itself when it can
            try:
                r = await asyncio.to_thread(lambda: ocr.ocr_image_bytes(path.read_bytes()))
                if r["text"].strip():
                    return ToolResult(True, f"[Text found in the image by OCR (confidence {r['confidence']:.2f}); may contain mistakes]\n{r['text']}",
                                      "Read text from image", {"name": meta["name"], "chars": len(r["text"]), "excerpt": r["text"][:400]})
            except Exception:  # noqa: BLE001
                pass
        return ToolResult(
            True,
            "This file is an image. Images are shown to the model directly when it supports vision; "
            "it can't be read as text.",
            "Image",
            {"name": meta["name"]},
        )
    try:
        text = await asyncio.to_thread(_extract, path)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Could not read {meta['name']}: {exc.__class__.__name__}: {str(exc)[:200]}")
    text = text.strip()
    if not text:
        raise ToolError(f"{meta['name']} contains no extractable text.")
    offset = max(0, int(args.get("offset") or 0))
    limit = 12000
    chunk = text[offset : offset + limit]
    end = offset + len(chunk)
    note = ""
    if end < len(text):
        note = f"\n\n[Showing characters {offset:,}–{end:,} of {len(text):,}. Call read_file again with offset={end} to continue.]"
    return ToolResult(
        True,
        chunk + note,
        meta["name"],
        {"name": meta["name"], "chars": len(text), "excerpt": chunk[:400]},
    )


# -------------------------------------------------------------- run python
def _scrubbed_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", k, re.I)
    }
    env.update(
        {
            "MPLBACKEND": "Agg",
            "PYTHONIOENCODING": "utf-8",
            "MPLCONFIGDIR": str(db.DATA_DIR / "mplconfig"),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
    )
    return env


def _snapshot(folder: Path) -> dict[str, tuple[float, int]]:
    snap = {}
    for p in folder.rglob("*"):
        if p.is_file():
            st = p.stat()
            snap[p.relative_to(folder).as_posix()] = (st.st_mtime, st.st_size)
    return snap


def sync_attachments(chat_id: str, files: list[dict]) -> None:
    work = db.work_dir(chat_id)
    for f in files:
        dest = work / Path(f["name"]).name
        if not dest.exists():
            try:
                shutil.copy2(f["path"], dest)
            except OSError:
                pass


def _file_mime(name: str) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".csv": "text/csv", ".json": "application/json", ".txt": "text/plain",
        ".pdf": "application/pdf", ".html": "text/html", ".svg": "image/svg+xml",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".odt": "application/vnd.oasis.opendocument.text", ".ods": "application/vnd.oasis.opendocument.spreadsheet",
        ".odp": "application/vnd.oasis.opendocument.presentation", ".rtf": "application/rtf",
    }.get(Path(name).suffix.lower(), "application/octet-stream")


async def run_python(s: dict, chat_id: str, args: dict) -> ToolResult:
    code = str(args.get("code", ""))
    if not code.strip():
        raise ToolError("`code` is required.")
    timeout = max(1, min(int(s.get("code_timeout") or 30), 300))
    work = db.work_dir(chat_id)
    run_dir = db.DATA_DIR / "chats" / chat_id / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (db.DATA_DIR / "mplconfig").mkdir(exist_ok=True)
    code_file = run_dir / "code.py"
    code_file.write_text(code, encoding="utf-8")

    before = _snapshot(work)
    started = time.monotonic()
    timed_out = False
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-X", "utf8", str(RUNNER), str(code_file),
        cwd=str(work), env=_scrubbed_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        out_b, err_b = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        raise
    elapsed = time.monotonic() - started

    def clip(b: bytes, n: int = 6000) -> str:
        t = b.decode("utf-8", "replace")
        return t if len(t) <= n else t[:n] + f"\n…[{len(t) - n:,} more characters truncated]"

    stdout, stderr = clip(out_b), clip(err_b)
    after = _snapshot(work)
    changed = [n for n, v in after.items() if before.get(n) != v and not n.startswith(".run/")][:20]
    files = [{"name": n, "url": f"/api/chats/{chat_id}/work/{n}", "mime": _file_mime(n)} for n in changed]

    ok = proc.returncode == 0 and not timed_out
    parts = []
    if timed_out:
        parts.append(f"The program was stopped after {timeout}s (time limit).")
    parts.append(f"exit code: {proc.returncode}")
    if stdout.strip():
        parts.append("stdout:\n" + stdout)
    if stderr.strip():
        parts.append("stderr:\n" + stderr)
    if files:
        parts.append("files created: " + ", ".join(f["name"] for f in files) + " (shown to the user)")
    if not stdout.strip() and not stderr.strip() and ok and not files:
        parts.append("(no output — use print() to see values)")
    return ToolResult(
        ok,
        "\n".join(parts),
        f"{elapsed:.1f}s" if ok else ("Timed out" if timed_out else "Error"),
        {"stdout": stdout, "stderr": stderr, "exit_code": proc.returncode, "timed_out": timed_out},
        files=files,
    )


# ------------------------------------------------------ conversation recall
_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)


def extract_urls(text: str) -> list[str]:
    out = []
    for u in _URL_RE.findall(text or ""):
        u = u.rstrip(".,;:!?*_")
        if u not in out:
            out.append(u)
    return out


def conversation_urls(messages: list[dict], limit: int = 40) -> list[str]:
    """Every link that appeared in the conversation (user text, answers, retrieved sources)."""
    urls: list[str] = []
    for m in messages:
        found = extract_urls(m.get("content") or "")
        for src in (m.get("meta") or {}).get("sources", []) if isinstance(m.get("meta"), dict) else []:
            found.append(src.get("url", ""))
        for u in found:
            if u and u not in urls:
                urls.append(u)
    return urls[:limit]


async def search_chat(s: dict, chat_id: str, args: dict) -> ToolResult:
    query = str(args.get("query", "")).strip().lower()
    msgs = db.get_path(chat_id)
    if not msgs:
        return ToolResult(True, "This conversation has no earlier messages.", "Empty")
    tokens = [w for w in re.findall(r"\w+", query) if len(w) > 2]
    wants_urls = (not tokens) or any(w in ("url", "urls", "link", "links", "source", "sources", "website", "websites", "http", "https") for w in tokens)
    urls = conversation_urls(msgs)
    hits = []
    for i, m in enumerate(msgs):
        text = re.sub(r"<think>[\s\S]*?(</think>|$)", "", m.get("content") or "").strip()
        low = text.lower()
        score = sum(low.count(w) for w in tokens)
        if score:
            pos = min((low.find(w) for w in tokens if w in low), default=0)
            snippet = text[max(0, pos - 300): pos + 500]
            hits.append({"score": score, "index": i + 1, "role": m["role"], "snippet": snippet})
    hits.sort(key=lambda h: -h["score"])
    hits = hits[:5]
    parts = []
    if wants_urls:
        parts.append("Links mentioned in this conversation (oldest first):\n" + ("\n".join(f"{n}. {u}" for n, u in enumerate(urls, 1)) if urls else "(none)"))
    files = [a for m in msgs for a in (m.get("attachments") or [])]
    if files and (wants_urls or not hits):
        parts.append("Attached files:\n" + "\n".join(f"- {a['name']} (file id: {a['id']})" for a in files))
    for h in hits:
        parts.append(f"[message {h['index']}, {h['role']}]\n{h['snippet']}")
    if not parts:
        parts.append("Nothing matching was found in earlier messages.")
    return ToolResult(
        True,
        "\n\n".join(parts),
        f"{len(hits)} matches, {len(urls)} links",
        {"hits": [{k: h[k] for k in ("index", "role", "snippet")} for h in hits], "urls": urls},
    )


# --------------------------------------------------------------- datetime
async def create_chart(s: dict, chat_id: str, args: dict) -> ToolResult:
    """Validate the data; the browser draws it. Errors are explicit so the model can correct and retry."""
    try:
        spec, summary = charts.build(args)
    except charts.ChartError as exc:
        raise ToolError(f"Chart not drawn: {exc} Fix the arguments and call create_chart again.") from None
    title = spec.get("title") or spec["visualization_type"].replace("_", " ")
    return ToolResult(
        True,
        f'The chart "{title}" ({spec["visualization_type"]}, {summary}) is now displayed to the user above your reply. '
        "Do not repeat the data or describe how it was drawn; write 1-3 sentences on what it shows.",
        summary,
        {"spec": spec},
    )


async def create_dashboard(s: dict, chat_id: str, args: dict) -> ToolResult:
    """Validate the data; the browser lays the dashboard out in the fixed pattern."""
    try:
        spec, summary, warnings = dashboards.build(args)
    except dashboards.DashboardError as exc:
        raise ToolError(str(exc)) from None
    note = (" Skipped: " + "; ".join(warnings) + ".") if warnings else ""
    return ToolResult(
        True,
        f'The dashboard "{spec["title"]}" ({summary}) is now displayed to the user above your reply.{note} '
        "Do not repeat its numbers or describe the layout; write 2-4 sentences of insight (what stands out, what to watch).",
        summary + (f" · {len(warnings)} skipped" if warnings else ""),
        {"spec": spec},
    )


async def create_map(s: dict, chat_id: str, args: dict) -> ToolResult:
    from . import geo
    try:
        spec = await geo.build_map(args)
    except ValueError as exc:
        raise ToolError(f"Map not drawn: {exc}") from None
    n = len(spec["points"])
    note = (" Not located (omitted): " + ", ".join(spec["skipped"]) + ".") if spec["skipped"] else ""
    dist = spec.get("distance")
    extra = f" Route length about {dist['km']} km, {dist['minutes']} min by road ({dist['method']})." if dist else ""
    return ToolResult(True, f'The map "{spec["title"]}" with {n} place{"s" if n != 1 else ""} is now displayed above your reply.{extra}{note} '
                      "Do not list the coordinates; write 1-3 sentences about what the map shows.",
                      f"{n} places" + (f" · {dist['km']} km" if dist else ""), {"map": spec})


async def save_memory(s: dict, chat_id: str, args: dict) -> ToolResult:
    if not s.get("memory_enabled", True):
        raise ToolError("Memory is switched off in Settings.")
    row, status = library.add_memory(str(args.get("fact") or ""), "model")
    if status == "secret":
        raise ToolError("Not saved: memory never stores passwords, keys or similar secrets.")
    if status in ("empty", "full"):
        raise ToolError("Not saved: " + ("the fact was empty." if status == "empty" else "memory is full; ask the user to delete some."))
    text = row["text"] if row else ""
    msg = f'Already remembered: "{text}".' if status == "duplicate" else f'Saved to memory: "{text}".'
    return ToolResult(True, msg + " Confirm briefly to the user.", msg, {"text": text, "status": status})


async def search_knowledge(s: dict, chat_id: str, args: dict) -> ToolResult:
    from . import embeddings
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("Give a search query.")
    chat = db.get_chat(chat_id) or {}
    scopes = ["memory"] + ([f"project:{chat['project_id']}"] if chat.get("project_id") else [])
    try:
        r = await asyncio.to_thread(embeddings.search, query, scopes, 5, 0.25)
    except embeddings.EmbeddingsUnavailable as exc:
        raise ToolError(f"Semantic search isn't available: {exc}")
    if not r["results"]:
        return ToolResult(True, "No relevant passages found in the project documents or memory.", "No matches")
    lines = [f"[{i + 1}] ({x['label'] or x['scope']}, relevance {x['score']:.2f}) {x['text']}" for i, x in enumerate(r["results"])]
    summary = f"Found {len(r['results'])} passage(s) · {r['device']} · {r['ms']:.0f} ms"
    return ToolResult(True, "\n\n".join(lines), summary, {"results": r["results"], "device": r["device"], "ms": r["ms"]})


def find_soffice() -> Optional[str]:
    for cand in (shutil.which("soffice"), shutil.which("libreoffice"),
                 r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if cand and Path(cand).exists():
            return cand
    return None


CONVERT_TARGETS = {"pdf", "docx", "doc", "odt", "xlsx", "xls", "ods", "csv", "pptx", "odp", "txt", "html", "rtf", "png"}


async def convert_document(s: dict, chat_id: str, args: dict) -> ToolResult:
    exe = find_soffice()
    if not exe:
        raise ToolError("LibreOffice is not installed on this computer.")
    to = str(args.get("to", "")).strip().lower().lstrip(".")
    if to not in CONVERT_TARGETS:
        raise ToolError(f"Can't convert to '{to}'. Choose one of: {', '.join(sorted(CONVERT_TARGETS))}.")
    ref = str(args.get("file", "")).strip()
    work = db.work_dir(chat_id)
    meta = db.get_file(ref)
    src = Path(meta["path"]) if meta else work / Path(ref).name
    if not ref or not src.is_file():
        raise ToolError("No such file. Use an attached file's id, or the name of a file in this chat's folder.")
    profile = (db.DATA_DIR / "lo-profile").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", to, "--outdir", str(work), str(src)]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 120)
    except asyncio.TimeoutError:
        proc.kill()
        raise ToolError("LibreOffice took longer than 2 minutes and was stopped.") from None
    result = work / (src.stem + "." + to)
    if proc.returncode != 0 or not result.exists():
        detail = (err or out).decode("utf-8", "replace").strip()[:300]
        raise ToolError(f"Conversion failed. {detail}")
    f = {"name": result.name, "url": f"/api/chats/{chat_id}/work/{result.name}", "mime": _file_mime(result.name)}
    return ToolResult(True, f"Converted {src.name} to {result.name}; the file is shown to the user.", f"{src.name} → {result.name}",
                      {"from": src.name, "to": result.name}, files=[f])


async def build_n8n_workflow(s: dict, chat_id: str, args: dict) -> ToolResult:
    try:
        wf, path = n8n.build(str(args.get("template", "")), str(args.get("name", "")), {"allow_send": args.get("allow_send")})
        made = await n8n.create_workflow(s, wf)
    except n8n.N8nError as exc:
        raise ToolError(str(exc)) from None
    needs = n8n.TEMPLATES[str(args.get("template"))]["needs"]
    url = f"{n8n.base_url(s)}/workflow/{made.get('id', '')}"
    msg = (f'Created the draft workflow "{wf["name"]}" in n8n (inactive). Tell the user to open it, attach {needs}, check each '
           f"node, then switch it on. Then add the MCP server in Settings → Connections (URL starts with {n8n.mcp_url_guess(s, path)}).")
    return ToolResult(True, msg, f"Created “{wf['name']}” in n8n", {"workflow_id": made.get("id"), "url": url, "mcp_url": n8n.mcp_url_guess(s, path)})


async def current_datetime(s: dict, chat_id: str, args: dict) -> ToolResult:
    now = datetime.now().astimezone()
    text = now.strftime("%A, %B %d, %Y, %I:%M:%S %p ") + (now.tzname() or "")
    return ToolResult(True, text, text, {"text": text})


IMPLS = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "read_file": read_file,
    "create_chart": create_chart,
    "create_dashboard": create_dashboard,
    "create_map": create_map,
    "save_memory": save_memory,
    "search_knowledge": search_knowledge,
    "convert_document": convert_document,
    "build_n8n_workflow": build_n8n_workflow,
    "run_python": run_python,
    "current_datetime": current_datetime,
    "search_chat": search_chat,
}
