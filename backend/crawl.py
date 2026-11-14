"""Crawl4AI as the page reader of last resort: renders JavaScript pages in a real browser and returns clean, LLM-ready markdown.

The plain HTTP reader in tools.py is fast and is tried first. This is used only when that fails (HTTP 403/429/503 from a site that
blocks scripts) or returns almost no text (a page that builds its content with JavaScript: product pages, SPAs, many official sites).

* Uses the Edge / Chrome that is already installed (no extra browser download); headless, images off, one page at a time.
* Runs in its own thread with its own event loop, so it works with any server event-loop policy (Windows included).
* The URL passes the same public-address check as the plain reader, and the address the browser ends up on is checked again.
* Page text is cleaned with Crawl4AI's pruning filter (or BM25 against the question when there is one) to drop menus, cookie banners, footers.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from typing import Optional

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")  # the library's promo banner; nothing is sent anywhere either way

_LOCK = threading.Lock()
_WIN_PATHS = {
    "msedge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
    "chrome": [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
}


def installed() -> bool:
    try:
        import crawl4ai  # noqa: F401
        return True
    except Exception:
        return False


def channel() -> Optional[str]:
    """Which installed browser Playwright can drive, or None."""
    if os.name == "nt":
        for ch, paths in _WIN_PATHS.items():
            if any(os.path.exists(p) for p in paths):
                return ch
        return None
    for ch, exe in (("chrome", "google-chrome"), ("chrome", "google-chrome-stable"), ("msedge", "microsoft-edge"), ("chrome", "chromium")):
        if shutil.which(exe):
            return ch
    return None


def available() -> bool:
    return installed() and channel() is not None


async def _crawl(url: str, query: str, timeout: float) -> dict:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import BM25ContentFilter
    try:
        from crawl4ai.content_filter_strategy import PruningContentFilterLXML as PruningContentFilter  # same output, ~10x faster
    except ImportError:
        from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    from . import tools
    flt = BM25ContentFilter(user_query=query) if query.strip() else PruningContentFilter(threshold=0.45, threshold_type="dynamic")
    browser = BrowserConfig(headless=True, browser_type="chromium", channel=channel() or "chrome", chrome_channel=channel() or "chrome", verbose=False, text_mode=True)
    run = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS, markdown_generator=DefaultMarkdownGenerator(content_filter=flt, options={"ignore_links": True, "ignore_images": True}),
        word_count_threshold=8, page_timeout=int(timeout * 1000), remove_overlay_elements=True, remove_consent_popups=True,
        delay_before_return_html=1.2, verbose=False)
    async with AsyncWebCrawler(config=browser) as crawler:
        r = await crawler.arun(url=url, config=run)
    if not r.success:
        raise RuntimeError((r.error_message or "the browser could not load the page")[:160])
    final = getattr(r, "redirected_url", None) or url
    await tools._check_url(final)  # a redirect must not lead into the local network
    md = r.markdown
    raw = (getattr(md, "raw_markdown", None) or str(md or "")).strip()
    fit = (getattr(md, "fit_markdown", None) or "").strip()
    text = fit if len(fit) >= 500 else raw  # a filter can over-trim a short page: then keep everything
    meta = r.metadata or {}
    return {"text": text, "title": str(meta.get("title") or "").strip(), "url": final, "status": r.status_code, "filtered": text is fit and bool(fit)}


def render(url: str, query: str = "", timeout: float = 40.0) -> Optional[dict]:
    """Blocking (call it with asyncio.to_thread). Returns {text, title, url, status, ms} or None when the page can't be rendered."""
    if not available():
        return None
    t0 = time.perf_counter()
    with _LOCK:  # one browser at a time: it is heavy and the machine is shared with the chat model
        try:
            out = asyncio.run(asyncio.wait_for(_crawl(url, query, timeout), timeout + 20))
        except Exception:  # noqa: BLE001 - the plain reader's error stays the one the user sees
            return None
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out if out["text"].strip() else None
