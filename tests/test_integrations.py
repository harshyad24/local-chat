"""Vector store backends, the LangGraph chart/dashboard flow and the LangChain retriever.

No embedding model and no Chroma install are needed: vectors are hand-made, Chroma is replaced by a small in-memory stand-in with the
same calls, and the model by scripted replies. Real Chroma / real embeddings are exercised by scripts/eval_retrieval.py."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from backend import agent, db, embeddings, vectorstore  # noqa: E402
from backend.integrations import langchain_rag, langgraph_flows  # noqa: E402
from backend.skills import book  # noqa: E402

from test_agent import TempDB, make_ctx  # noqa: E402
from test_phase4 import GOOD_DASH  # noqa: E402


def unit(*xs):
    v = np.array(xs, dtype="float32")
    return v / np.linalg.norm(v)


PASSAGES = [  # (text, page, vector): three topics laid out on three axes
    ("Tensors are arrays. " * 12, 90, unit(1, 0.1, 0)),
    ("Reshaping changes the view. " * 10, 95, unit(0.9, 0.3, 0)),
    ("Gradients come from autograd. " * 10, 150, unit(0, 1, 0.1)),
    ("Probability and sampling. " * 12, 160, unit(0, 0.1, 1)),
    ("A chapter far away about CNNs. " * 10, 520, unit(1, 0, 0.4)),
]


class FakeCollection:
    def __init__(self):
        self.rows = []

    def add(self, ids, documents, embeddings, metadatas):
        self.rows += list(zip(ids, documents, embeddings, metadatas))

    def count(self):
        return len(self.rows)

    def query(self, query_embeddings, n_results, where=None):
        q = np.array(query_embeddings[0], dtype="float32")

        def ok(m):
            if not where:
                return True
            conds = where["$and"]
            return m["page_end"] >= conds[0]["page_end"]["$gte"] and m["page"] <= conds[1]["page"]["$lte"]

        hits = sorted(((1 - float(np.array(e, dtype="float32") @ q), d, m) for _, d, e, m in self.rows if ok(m)), key=lambda x: x[0])[:n_results]
        return {"documents": [[h[1] for h in hits]], "metadatas": [[h[2] for h in hits]], "distances": [[h[0] for h in hits]]}


class FakeChroma:
    def __init__(self):
        self.cols = {}

    def get_or_create_collection(self, name, metadata=None):
        return self.cols.setdefault(name, FakeCollection())

    def delete_collection(self, name):
        self.cols.pop(name, None)


class Stores(TempDB):
    def setUp(self):
        super().setUp()
        self.scope = "book2:t1"
        embeddings._init()
        with db.conn() as c:
            c.executemany(
                "INSERT INTO chunks(scope, source, label, ord, text, meta, model, dim, vec, created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(self.scope, "t1", "t", i, t, json.dumps({"page": p, "page_end": p}), embeddings.model_name(), 3, v.tobytes(), 0.0)
                 for i, (t, p, v) in enumerate(PASSAGES)])

    def test_sqlite_search_ranks_by_cosine_and_filters_pages(self):
        s = vectorstore.SqliteStore()
        top = s.search(self.scope, unit(1, 0, 0), 2)
        self.assertEqual([h["page"] for h in top], [90, 95])
        scoped = s.search(self.scope, unit(1, 0, 0), 5, (80, 170))
        self.assertTrue(all(80 <= h["page"] <= 170 for h in scoped))
        self.assertEqual(scoped[0]["page"], 90)

    def test_mirror_copies_vectors_once_and_both_stores_agree(self):
        chroma = vectorstore.ChromaStore(client=FakeChroma())
        self.assertEqual(vectorstore.mirror(self.scope, chroma), 5)
        self.assertEqual(vectorstore.mirror(self.scope, chroma), 0)  # already there: nothing copied again
        q = unit(0.1, 1, 0)
        a = [h["page"] for h in vectorstore.SqliteStore().search(self.scope, q, 3, (80, 170))]
        b = [h["page"] for h in chroma.search(self.scope, q, 3, (80, 170))]
        self.assertEqual(a, b)  # same vectors, same cosine: the two stores return the same pages

    def test_chroma_unavailable_falls_back_to_sqlite(self):
        broken = vectorstore.ChromaStore()
        with mock.patch.object(vectorstore.ChromaStore, "_chroma", side_effect=ImportError("blocked")), mock.patch.object(vectorstore, "_CHROMA", broken):
            self.assertEqual(vectorstore.get_store("chroma").name, "sqlite")

    def test_book_retrieval_through_the_vector_database_covers_each_section_inside_the_chapter(self):
        chroma = vectorstore.ChromaStore(client=FakeChroma())
        sections = [("Tensors", 90), ("Autograd", 140), ("Probability", 155)]
        fake_embed = lambda texts: {"vectors": np.vstack([unit(1, 0.2, 0.1), unit(1, 0, 0), unit(0, 1, 0), unit(0, 0, 1)])[:len(texts)]}
        with mock.patch.object(book.embeddings, "embed", fake_embed), mock.patch.object(book.vectorstore, "get_store", lambda *_: chroma):
            hits = asyncio.run(book.retrieve("t1", "tensors and gradients", (89, 178), sections, 4))
        pages = [h["page"] for h in hits]
        self.assertEqual(pages, sorted(pages))
        self.assertNotIn(520, pages)  # the page range keeps the other chapter out
        self.assertTrue({90, 150, 160} <= set(pages))  # one passage from each of the three sections


class LangGraphFlow(unittest.TestCase):
    def ctx(self, engine):
        c = make_ctx(["create_dashboard"])
        c.s["agent_engine"] = engine
        c.dash_template = "sales"
        c.prompt.append({"role": "user", "content": "make a dashboard: revenue Jan 10 Feb 12 Mar 15"})
        return c

    def run_flow(self, engine, replies):
        c, seen = self.ctx(engine), []
        it = iter(replies)

        async def fake_complete(s, model, msgs, **kw):
            seen.append(msgs[-1]["content"])
            return next(it)

        with mock.patch.object(agent.llm, "complete", fake_complete):
            out = asyncio.run(agent.extract_chart_call(c, "", "create_dashboard"))
        return out, c, seen

    def test_good_first_reply(self):
        out, _, seen = self.run_flow("langgraph", [json.dumps(GOOD_DASH)])
        self.assertEqual(out["name"], "create_dashboard")
        self.assertEqual(len(seen), 1)

    def test_bad_reply_is_repaired_once_with_the_reason_shown_to_the_model(self):
        out, _, seen = self.run_flow("langgraph", ['{"kpis": [], "charts": []}', json.dumps(GOOD_DASH)])
        self.assertIsNotNone(out)
        self.assertIn("rejected", seen[1])

    def test_gives_up_after_two_attempts_and_says_why(self):
        out, c, seen = self.run_flow("langgraph", ["not json", "still not json"])
        self.assertIsNone(out)
        self.assertEqual(len(seen), 2)
        self.assertTrue(c.extract_error)

    def test_no_data_stops_immediately(self):
        out, c, seen = self.run_flow("langgraph", ['{"error": "no data"}'])
        self.assertIsNone(out)
        self.assertEqual(len(seen), 1)
        self.assertIn("no numbers", c.extract_error)

    def test_graph_and_builtin_loop_give_identical_results_on_the_same_replies(self):
        for replies in ([json.dumps(GOOD_DASH)], ["x", json.dumps(GOOD_DASH)], ["x", "y"], ['{"error": "no data"}']):
            a, ca, sa = self.run_flow("builtin", replies)
            b, cb, sb = self.run_flow("langgraph", replies)
            self.assertEqual(a, b)
            self.assertEqual(len(sa), len(sb))
            self.assertEqual(ca.extract_error, cb.extract_error)

    def test_graph_has_the_expected_shape(self):
        text = langgraph_flows.diagram()
        for node in ("ask", "check"):
            self.assertIn(node, text)


class LangChainRetriever(unittest.TestCase):
    def test_book_retriever_returns_documents_with_pages(self):
        async def fake(file_id, query, span, sections, limit):
            return [{"page": 91, "page_end": 92, "text": "Tensor creation…", "score": 0.7}]

        with mock.patch.object(book, "retrieve", fake):
            docs = asyncio.run(langchain_rag.BookRetriever(file_id="t1", span=(89, 178), k=3).ainvoke("what is a tensor"))
        self.assertEqual(docs[0].metadata["page"], 91)
        self.assertEqual(docs[0].page_content, "Tensor creation…")

    def test_store_retriever_uses_the_chosen_backend(self):
        store = mock.Mock()
        store.search.return_value = [{"page": 5, "page_end": 5, "text": "hello", "score": 0.9}]
        with mock.patch.object(embeddings, "embed", lambda t: {"vectors": np.array([[1.0, 0.0]], dtype="float32")}):
            docs = langchain_rag.StoreRetriever(scope="s", backend=store, k=2).invoke("q")
        self.assertEqual(docs[0].metadata["page"], 5)
        store.search.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class EvalScript(unittest.TestCase):
    def test_sections_become_questions_with_page_ranges_and_hits_are_counted(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ev", Path(__file__).resolve().parent.parent / "scripts" / "eval_retrieval.py")
        ev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ev)
        toc = [(1, "Data Manipulation", 90), (2, "Tensor Creation", 91), (2, "Summary", 93), (2, "Reshaping and Indexing", 94), (1, "Calculus", 104)]
        secs = ev.sample_sections(toc, 10)
        self.assertEqual(sorted(secs), [("Reshaping and Indexing", 94, 103), ("Tensor Creation", 91, 92)])  # Summary is never a question, but it still ends the previous section
        self.assertTrue(ev.hit([(10, 10), (92, 92)], 91, 93, 3))
        self.assertFalse(ev.hit([(10, 10), (92, 92)], 91, 93, 1))  # the right page is only second
        self.assertTrue(ev.hit([(90, 94)], 94, 103, 1))  # a passage that spans pages counts when it overlaps
