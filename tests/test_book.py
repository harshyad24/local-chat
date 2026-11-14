"""/learn with an attached book: page-aware passages, chapter outline, file picking, prefill (no model, no embeddings needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.skills import book  # noqa: E402
from backend.skills.workflows import learn  # noqa: E402

BOOK = "\n\n".join(
    f"--- page {n} ---\n" + ("Chapter 2 Soil Grading\n" if n == 6 else "") + ("Chapter 1 Irrigation Basics\n" if n == 1 else "")
    + f"Fact on page {n}. " + "Farmers record their work in ledgers. " * 30
    for n in range(1, 13))


class BookTests(unittest.TestCase):
    def test_pages_are_split_by_marker(self):
        pg = book.pages_of(BOOK)
        self.assertEqual([p for p, _ in pg], list(range(1, 13)))
        self.assertIn("Fact on page 7.", dict(pg)[7])

    def test_ocr_page_markers_are_understood(self):
        txt = "[Page 3]\nhello\n\n[Page 4]\nworld"
        self.assertEqual([p for p, _ in book.pages_of(txt)], [3, 4])

    def test_text_without_markers_gets_pseudo_pages(self):
        pg = book.pages_of("word " * 3000)
        self.assertGreater(len(pg), 3)
        self.assertEqual(pg[0][0], 1)

    def test_passages_carry_page_numbers(self):
        ps = book.passages(BOOK)
        self.assertTrue(ps)
        self.assertTrue(all(m["page"] is not None and m["page_end"] >= m["page"] for _, m in ps))
        hit = [m for t, m in ps if "Fact on page 9." in t][0]
        self.assertLessEqual(hit["page"], 9)
        self.assertGreaterEqual(hit["page_end"], 9)

    def test_outline_finds_chapters_in_order(self):
        o = book.outline(BOOK)
        self.assertEqual(o[0], "Chapter 1 Irrigation Basics")
        self.assertIn("Chapter 2 Soil Grading", o)

    def test_outline_ignores_table_of_contents_dot_leaders(self):
        o = book.outline("Chapter 3 Pests and their long running costs over time ........ 41\n")
        self.assertEqual(o, [])

    def test_sample_is_evenly_spread(self):
        ps = book.passages(BOOK)
        s = book.sample(ps, 4)
        self.assertLessEqual(len(s), 4)
        self.assertTrue(s[0].startswith("p."))

    def test_pick_takes_newest_document_and_ignores_images_and_tables(self):
        files = [{"id": "a", "name": "a.pdf", "path": "a", "ext": ".pdf"}, {"id": "b", "name": "b.png", "path": "b", "ext": ".png"},
                 {"id": "c", "name": "c.csv", "path": "c", "ext": ".csv"}]
        self.assertEqual(book.pick(files)["id"], "a")
        self.assertIsNone(book.pick(files[1:]))

    def test_prompt_and_page_label(self):
        hits = [{"page": 9, "page_end": 9, "text": "x" * 2000, "score": .5}, {"page": 3, "page_end": 3, "text": "y", "score": .4}]
        self.assertEqual(book.pages_label(hits), "3, 9")
        self.assertLess(len(book.as_prompt(hits, 100)), 260)

    def test_prefill_uses_attached_book_and_names_the_subject_after_it(self):
        f = [{"id": "x1", "name": "plateau_farming.pdf", "path": "p", "ext": ".pdf"}]
        out = learn.prefill("teach me this book", {"files": f})
        self.assertEqual(out["mode"], "book")
        self.assertEqual(out["subject"], "plateau farming")
        self.assertEqual(out["book"]["id"], "x1")

    def test_prefill_keeps_a_specific_subject_and_interview_mode(self):
        f = [{"id": "x1", "name": "notes.pdf", "path": "p", "ext": ".pdf"}]
        out = learn.prefill("prepare me for a Python interview using my notes", {"files": f})
        self.assertEqual(out["mode"], "interview")
        self.assertIn("python", out["subject"].lower())

    def test_prefill_without_file_is_unchanged(self):
        out = learn.prefill("teach me Python from beginner to advanced", {"files": []})
        self.assertNotIn("book", out)
        self.assertEqual(out["mode"], "course")


if __name__ == "__main__":
    unittest.main()


class TocScopingTests(unittest.TestCase):
    TOC = [(1, "Preliminaries", 89), (2, "Resources and Further Reading", 90), (1, "Data Manipulation", 90), (2, "Tensor Creation", 91),
           (1, "Data Preprocessing", 104), (1, "Linear Algebra", 112), (1, "Calculus", 131), (2, "Summary", 144), (1, "Linear Regression", 179),
           (1, "Linear Regression", 181), (1, "Multilayer Perceptrons", 330)]

    def test_module_titles_match_bookmarks_even_with_numbers_and_plurals(self):
        from backend.skills import book
        mods = [{"title": "1 Preliminaries"}, {"title": "2 Linear Regression"}, {"title": "4 Multilayer Perceptron"}, {"title": "Quantum Gravity"}]
        r = book.module_ranges(mods, self.TOC)
        self.assertEqual(r[0], (89, 178))  # runs to the next module's start
        self.assertEqual(r[1][0], 179)  # the earliest of two same-named bookmarks
        self.assertEqual(r[2][0], 330)
        self.assertNotIn(3, r)  # nothing in the book is close enough

    def test_sections_come_from_the_book_and_skip_boilerplate(self):
        from backend.skills import book
        secs = book.sections_in(self.TOC, 89, 178)
        self.assertEqual([t for t, _ in secs], ["Data Manipulation", "Data Preprocessing", "Linear Algebra", "Calculus"])

    def test_citations_not_in_the_retrieved_passages_are_removed(self):
        from backend.skills import book
        hits = [{"page": 90, "page_end": 91}, {"page": 104, "page_end": 104}]
        text, bad = book.check_citations("Tensors (p. 91) are arrays. Autograd (p. 1202) is later. See (pages 104).", hits)
        self.assertEqual(bad, [1202])
        self.assertIn("(p. 91)", text)
        self.assertNotIn("1202", text)

    def test_quiz_filter_drops_page_trivia_and_the_text_does_not_say(self):
        from backend.skills.workflows import learn
        good = {"q": "Why does broadcasting avoid a Python loop?", "options": ["a", "b", "c", "d"], "answer": 1}
        trivia = {"q": "What does page 200 say about the threshold?", "options": ["a", "b", "c", "d"], "answer": 0}
        nosay = {"q": "Why odd kernels?", "options": ["x", "The text does not provide a reason", "y", "z"], "answer": 1}
        self.assertEqual(len(learn._valid_quiz({"questions": [good, trivia, nosay]})), 1)
