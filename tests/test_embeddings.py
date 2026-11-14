"""Embeddings, semantic search and the GPU manager. Uses the real model (cached after first download)."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import embeddings, gpu, library  # noqa: E402

from test_agent import TempDB  # noqa: E402


class Chunking(unittest.TestCase):
    def test_chunks_respect_size_and_cover_text(self):
        text = "\n\n".join(f"Paragraph {i}. " + "word " * 80 for i in range(10))
        chunks = embeddings.chunk_text(text, size=500, overlap=50)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(c) <= 500 * 1.6 for c in chunks))
        joined = " ".join(chunks)
        for i in range(10):
            self.assertIn(f"Paragraph {i}.", joined)

    def test_empty_and_giant_paragraph(self):
        self.assertEqual(embeddings.chunk_text("   "), [])
        big = embeddings.chunk_text("x" * 5000, size=900)
        self.assertGreater(len(big), 3)


@unittest.skipUnless(embeddings.available(), "sentence-transformers not installed")
class Search(TempDB):
    def test_index_search_sources_filters(self):
        embeddings.index_text("project:p1", "f1", "The invoice is due on Friday and totals five hundred dollars.", "invoice.pdf", {"kind": "pdf"})
        embeddings.index_text("project:p1", "f2", "Our dog loves to play fetch in the park every morning.", "pets.txt", {"kind": "txt"})
        embeddings.index_text("project:p2", "f3", "Quarterly revenue grew twelve percent.", "other.txt")
        r = embeddings.search("when do I have to pay the bill?", ["project:p1"], limit=2, threshold=0.1)
        self.assertEqual(r["results"][0]["label"], "invoice.pdf")
        self.assertEqual(r["results"][0]["source"], "f1")
        self.assertTrue(all(x["scope"] == "project:p1" for x in r["results"]))  # never leaks another project
        self.assertEqual(embeddings.search("pay the bill", ["project:p1"], meta_filter={"kind": "txt"}, threshold=-1)["results"][0]["label"], "pets.txt")
        self.assertEqual(embeddings.search("zzzz", ["project:none"])["results"], [])
        self.assertEqual(embeddings.remove("project:p1", "f1"), 1)
        self.assertEqual(embeddings.count("project:p1"), 1)

    def test_hindi_query_finds_english_text(self):
        embeddings.index_text("memory", "1", "The user prefers vegetarian food.", "memory")
        embeddings.index_text("memory", "2", "The user works as a data analyst in Pune.", "memory")
        r = embeddings.search("मैं शाकाहारी खाना पसंद करता हूँ", ["memory"], limit=1, threshold=0.1)
        self.assertEqual(r["results"][0]["source"], "1")

    def test_threshold_and_similarity(self):
        s = embeddings.similarity("reset my password", "I forgot my login")
        self.assertGreater(s["score"], 0.4)
        self.assertLess(embeddings.similarity("reset my password", "pizza with extra cheese")["score"], 0.2)

    def test_reindex_replaces(self):
        embeddings.index_text("memory", "9", "first version text", "m")
        embeddings.index_text("memory", "9", "second version text", "m")
        self.assertEqual(embeddings.count("memory"), 1)


class GpuManager(unittest.TestCase):
    def test_status_shape(self):
        st = gpu.status()
        self.assertIn("torch", st)
        self.assertIn("app_models", st)
        self.assertIn("external", st)

    def test_cpu_preference_and_vram_guard(self):
        self.assertEqual(gpu.choose_device(100, "cpu")[0], "cpu")
        dev, reason = gpu.choose_device(10_000_000)  # more than any GPU has
        self.assertEqual(dev, "cpu")
        self.assertTrue(reason)

    def test_tasks_are_serialised(self):
        order = []

        def work(n):
            with gpu.task(f"t{n}", prefer="cpu"):
                order.append(("in", n)); time.sleep(0.15); order.append(("out", n))

        ts = [threading.Thread(target=work, args=(i,)) for i in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        for i in range(0, 6, 2):  # each task fully finishes before the next starts
            self.assertEqual(order[i][0], "in")
            self.assertEqual(order[i + 1], ("out", order[i][1]))

    def test_registry_unload_and_idle(self):
        gpu.register("dummy", object(), "cpu", 0)
        self.assertEqual(gpu.get("dummy")[1], "cpu")
        self.assertEqual(gpu.unload_idle(max_idle_s=-1), ["dummy"])
        self.assertIsNone(gpu.get("dummy")[0])

    def test_queue_timeout(self):
        with gpu.task("holder", prefer="cpu"):
            err = []
            def other():
                try:
                    with gpu.task("late", prefer="cpu", timeout=0.2):
                        pass
                except TimeoutError as e:
                    err.append(e)
            t = threading.Thread(target=other); t.start(); t.join()
            self.assertEqual(len(err), 1)


if __name__ == "__main__":
    unittest.main()
