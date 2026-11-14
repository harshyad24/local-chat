"""Memory de-duplication by meaning, relevance-based recall, and chat-wide semantic search (real embedding model)."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, embeddings, library, main  # noqa: E402

from test_agent import TempDB  # noqa: E402


@unittest.skipUnless(embeddings.available(), "sentence-transformers not installed")
class MemoryByMeaning(TempDB):
    def test_rewording_replaces_instead_of_piling_up(self):
        a, st = library.add_memory("My dog is named Bruno")
        self.assertEqual(st, "saved")
        b, st = library.add_memory("My dog's name is Bruno")  # reworded: same fact
        self.assertEqual(b["id"], a["id"])
        self.assertEqual(len(library.list_memories()), 1)

    def test_refinement_updates_the_old_text(self):
        a, _ = library.add_memory("I work at Acme as an engineer")
        b, st = library.add_memory("I work at Acme as a senior engineer")
        self.assertEqual((b["id"], st), (a["id"], "saved"))
        self.assertEqual(library.list_memories()[0]["text"], "I work at Acme as a senior engineer")

    def test_different_facts_are_both_kept(self):
        library.add_memory("I like tea")
        library.add_memory("I like coffee")  # similar sentence, different fact: never merged
        library.add_memory("I live in Pune")
        library.add_memory("I moved to Mumbai")
        self.assertEqual(len(library.list_memories()), 4)

    def test_edit_refreshes_the_index(self):
        a, _ = library.add_memory("I enjoy cycling on weekends")
        library.update_memory(a["id"], "I enjoy baking sourdough bread")
        hit = embeddings.search("homemade bread", "memory", limit=1, threshold=0.2)["results"]
        self.assertTrue(hit and "bread" in hit[0]["text"])


@unittest.skipUnless(embeddings.available(), "sentence-transformers not installed")
class Recall(TempDB):
    def test_relevant_memory_beats_newest(self):
        library.add_memory("I am allergic to peanuts")
        for i in range(12):
            library.add_memory(f"Unrelated note number {i} about {['gardening', 'chess', 'trains', 'jazz', 'rain', 'maps', 'tea', 'paint', 'kites', 'owls', 'clocks', 'sand'][i]}")
        fallback = library.memory_lines(budget=120)  # only the newest few fit: the allergy is not among them
        self.assertNotIn("I am allergic to peanuts", fallback)
        got = asyncio.run(main.recall_memories("Can you suggest a snack? I react badly to nuts.", fallback))
        self.assertIn("I am allergic to peanuts", got)

    def test_few_memories_use_the_plain_path(self):
        library.add_memory("I like green")
        self.assertEqual(asyncio.run(main.recall_memories("anything", ["x"])), ["x"])


@unittest.skipUnless(embeddings.available(), "sentence-transformers not installed")
class ChatSearch(TempDB):
    def test_finds_chat_by_meaning_and_skips_archived(self):
        c1, c2 = db.create_chat(), db.create_chat()
        m1 = db.add_message(c1["id"], "user", "How do I fix a leaking kitchen tap at home without calling a plumber?", parent_id=None)
        db.add_message(c1["id"], "assistant", "Turn off the water supply, then replace the worn washer or cartridge inside the faucet handle.", parent_id=m1)
        db.add_message(c2["id"], "user", "Write a python function that parses a CSV file and sums the third column.", parent_id=None)
        added = main.sync_chat_index()
        self.assertEqual(added, 3)
        self.assertEqual(main.sync_chat_index(), 0)  # incremental
        r = asyncio.run(main.search_chats("dripping faucet repair"))
        self.assertEqual(r["results"][0]["chat_id"], c1["id"])
        self.assertNotIn(c2["id"], [x["chat_id"] for x in r["results"]][:1])


if __name__ == "__main__":
    unittest.main()
