"""Page reader: plain HTTP first, Crawl4AI browser rendering only for blocked / script-built pages."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import crawl, tools  # noqa: E402

SETTINGS = {"page_char_limit": 3000, "js_render": "auto"}
SHELL = b"<html><head><title>Shop</title><script src='a.js'></script><script src='b.js'></script></head><body><div id='root'></div><noscript>Enable JS</noscript></body></html>"
LONG = ("<html><head><title>Long</title></head><body>" + "".join(f"<p>Paragraph {i} about gardening and soil.</p>" for i in range(400))
        + "<p>The warranty period for the Zeta 9 is 36 months from purchase.</p>"
        + "".join(f"<p>Another filler paragraph {i} about trains.</p>" for i in range(400)) + "</body></html>").encode()
TINY = b"<html><head><title>Example</title></head><body><h1>Example Domain</h1><p>This domain is for use in examples.</p></body></html>"


def run(settings, html=None, error=None, rendered=None, query=""):
    calls = []

    async def fake_download(url):
        if error:
            raise tools.ToolError(error)
        return html, "text/html", url

    def fake_render(url, q="", timeout=40.0):
        calls.append((url, q))
        return rendered

    with mock.patch.object(tools, "_download", fake_download), mock.patch.object(crawl, "render", fake_render), \
            mock.patch.object(crawl, "available", lambda: True):
        try:
            res = asyncio.run(tools.fetch_page({**SETTINGS, **settings}, "c", {"url": "https://shop.example/p", "query": query}))
        except tools.ToolError as exc:
            return exc, calls
    return res, calls


BROWSER = {"text": "Zeta 9 laptop. Price 79,990 rupees. 16 GB RAM. " * 8, "title": "Zeta 9", "url": "https://shop.example/p", "status": 200}


class Fallback(unittest.TestCase):
    def test_script_built_page_uses_the_browser(self):
        res, calls = run({}, html=SHELL, rendered=BROWSER)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.detail["rendered_by"], "browser")
        self.assertIn("79,990", res.content)

    def test_normal_page_never_starts_a_browser(self):
        res, calls = run({}, html=LONG)
        self.assertEqual(calls, [])
        self.assertNotIn("rendered_by", res.detail)

    def test_tiny_but_complete_page_is_left_alone(self):
        res, calls = run({}, html=TINY)
        self.assertEqual(calls, [])
        self.assertIn("Example Domain", res.content)

    def test_blocked_site_falls_back(self):
        res, calls = run({}, error="The site answered HTTP 403.", rendered=BROWSER)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.detail["rendered_by"], "browser")

    def test_blocked_and_unrenderable_keeps_the_original_error(self):
        res, calls = run({}, error="The site answered HTTP 403.", rendered=None)
        self.assertIsInstance(res, tools.ToolError)
        self.assertIn("403", str(res))

    def test_other_errors_do_not_start_a_browser(self):
        res, calls = run({}, error="The site answered HTTP 404.", rendered=BROWSER)
        self.assertEqual(calls, [])
        self.assertIsInstance(res, tools.ToolError)

    def test_off_means_off(self):
        res, calls = run({"js_render": "off"}, html=SHELL, rendered=BROWSER)
        self.assertEqual(calls, [])
        self.assertNotIn("rendered_by", res.detail)  # just the plain reader's 9 characters

    def test_always_uses_the_browser_even_for_normal_pages(self):
        res, calls = run({"js_render": "always"}, html=LONG, rendered=BROWSER)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.detail["rendered_by"], "browser")

    def test_browser_result_must_be_better(self):
        weak = {**BROWSER, "text": "tiny"}
        res, calls = run({}, html=SHELL, rendered=weak)
        self.assertIsInstance(res, tools.ToolError) if not hasattr(res, "content") else self.assertNotIn("rendered_by", res.detail)


class LongPages(unittest.TestCase):
    def test_query_keeps_the_relevant_passage_from_deep_in_the_page(self):
        plain, _ = run({}, html=LONG)
        self.assertNotIn("warranty period", plain.content)  # the old head-only clipping loses it
        focused, _ = run({}, html=LONG, query="Zeta 9 warranty period")
        self.assertIn("36 months", focused.content)


class RealBrowser(unittest.TestCase):
    @unittest.skipUnless(crawl.available(), "Crawl4AI or an installed Edge/Chrome is missing")
    def test_refuses_local_network_addresses(self):
        import http.server
        import threading

        page = b"<html><head><title>JS</title></head><body><div id='x'></div><script>document.getElementById('x').innerHTML='<p>' + 'The secret code is PLUM-4417 and it was built by JavaScript. '.repeat(6) + '</p>'</script></body></html>"

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            # the public-address guard must refuse a server on this machine, even though the browser could load it
            self.assertIsNone(crawl.render(f"http://127.0.0.1:{srv.server_port}/"))
        finally:
            srv.shutdown()

    @unittest.skipUnless(crawl.available(), "Crawl4AI or an installed Edge/Chrome is missing")
    def test_renders_with_a_real_browser(self):
        out = asyncio.run(asyncio.wait_for(asyncio.to_thread(crawl.render, "https://quotes.toscrape.com/js/"), 90))
        self.assertTrue(out and "world as we have created it" in out["text"].lower())


if __name__ == "__main__":
    unittest.main()
