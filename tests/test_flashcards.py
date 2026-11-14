"""Flashcard quality, scheduling previews, undo, leeches, the daily new-card limit and statistics."""
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, flashcards, learning as L  # noqa: E402

from test_agent import TempDB  # noqa: E402

LESSON = ("A tensor is an array of numbers arranged along any number of axes (p. 91). Broadcasting stretches axes of length 1 so that tensors of "
          "different shapes can be combined without copying data (p. 102). Gradients are computed by automatic differentiation (p. 147).")


class Quality(unittest.TestCase):
    def test_cloze_markup_and_blanks_become_one_shape(self):
        a = flashcards.normalize({"front": "A {{c1::tensor}} is an array on many axes.", "back": ""})
        self.assertEqual((a["kind"], a["front"], a["back"]), ("cloze", "A […] is an array on many axes.", "tensor"))
        b = flashcards.normalize({"type": "cloze", "front": "Broadcasting stretches axes of length ____.", "back": "1"})
        self.assertEqual((b["kind"], b["front"]), ("cloze", "Broadcasting stretches axes of length […]."))
        self.assertIsNone(flashcards.normalize({"type": "cloze", "front": "No blank here at all.", "back": "x"}))  # a cloze needs a blank

    def test_cards_that_depend_on_the_text_or_invent_facts_are_dropped_with_a_reason(self):
        cards = [
            {"front": "What is a tensor?", "back": "An array of numbers arranged along any number of axes"},
            {"front": "What does the text say about broadcasting?", "back": "It stretches axes"},
            {"front": "What is the capital of France and why is it famous?", "back": "Paris, because of the Eiffel Tower and the Louvre museum"},
            {"front": "How does automatic differentiation help?", "back": " ".join(["word"] * 40)},
            {"front": "Why", "back": "x"},
            "not a card",
        ]
        keep, dropped = flashcards.quality(cards, LESSON)
        self.assertEqual([c["front"] for c in keep], ["What is a tensor?"])
        self.assertEqual(len(dropped), 5)
        self.assertIn("depends on 'the text'", dropped)
        self.assertIn("answer not found in the lesson", dropped)
        self.assertIn("answer too long", dropped)

    def test_the_page_comes_from_the_lesson_sentence_the_card_matches(self):
        card = {"front": "What does broadcasting do to axes of length 1?", "back": "It stretches them so tensors of different shapes combine"}
        self.assertEqual(flashcards.find_page(LESSON, card), 102)
        self.assertIsNone(flashcards.find_page(LESSON, {"front": "Unrelated question about cooking", "back": "pasta"}))

    def test_interval_labels_read_naturally(self):
        self.assertEqual(flashcards.fmt_interval(600), "10 min")
        self.assertEqual(flashcards.fmt_interval(86400), "1 day")
        self.assertEqual(flashcards.fmt_interval(6 * 86400), "6 days")
        self.assertEqual(flashcards.fmt_interval(21 * 86400), "3 weeks")
        self.assertEqual(flashcards.fmt_interval(120 * 86400), "4 months")


def add(course, n, module="m", **kw):
    return L.add_cards(course, module, [{"front": f"Question number {i} about {module}?", "back": f"Answer {i} for {module}", **kw} for i in range(n)])


class Scheduling(TempDB):
    def test_review_cards_show_what_each_button_will_do(self):
        add("c1", 1)
        card = L.due_cards("c1")[0]
        self.assertEqual(card["preview"]["again"], "10 min")
        self.assertEqual(card["preview"]["good"], "1 day")
        self.assertEqual(card["preview"]["easy"], "3 days")
        self.assertEqual(card["maturity"], "new")

    def test_undo_restores_the_card_and_removes_the_review_from_the_statistics(self):
        add("c1", 1)
        cid = L.due_cards("c1")[0]["id"]
        r = L.grade_card(cid, "good")
        self.assertEqual(L.card_stats("c1")["reviews_30d"], 1)
        self.assertTrue(L.undo_grade(cid, r["prev"], r["event"]))
        row = [c for c in L.list_cards("c1") if c["id"] == cid][0]
        self.assertEqual((row["reps"], row["interval"], row["first_seen"]), (0, 0, None))
        self.assertEqual(L.card_stats("c1")["reviews_30d"], 0)
        self.assertFalse(L.undo_grade(cid, {"ease": 99, "interval": 1, "reps": 1, "lapses": 0, "due": 0}))  # nonsense is refused

    def test_a_card_missed_four_times_is_flagged_as_a_leech_and_can_be_suspended(self):
        add("c1", 2)
        cid = L.due_cards("c1")[0]["id"]
        last = None
        for _ in range(4):
            last = L.grade_card(cid, "again")
        self.assertTrue(last["leech"])
        self.assertEqual(L.card_stats("c1")["leeches"], 1)
        self.assertTrue(L.set_suspended(cid, True))
        self.assertNotIn(cid, [c["id"] for c in L.due_cards("c1")])  # never shown again
        self.assertEqual(L.card_stats("c1")["leeches"], 0)
        self.assertEqual(L.card_counts("c1")["total"], 1)
        L.set_suspended(cid, False)
        self.assertIn(cid, [c["id"] for c in L.due_cards("c1")])

    def test_only_a_daily_number_of_new_cards_is_introduced(self):
        db.save_settings({"cards_new_per_day": 3})
        add("c1", 8)
        first = L.due_cards("c1", 30)
        self.assertEqual(len(first), 3)
        for c in first:
            L.grade_card(c["id"], "good")
        self.assertEqual(L.due_cards("c1", 30), [])  # the 3 are scheduled for tomorrow and today's allowance is used up
        db.save_settings({"cards_new_per_day": 5})
        self.assertEqual(len(L.due_cards("c1", 30)), 2)

    def test_reviews_that_are_due_come_before_new_cards(self):
        add("c1", 3, module="old")
        old = L.due_cards("c1")[0]
        L.grade_card(old["id"], "again")  # due again in 10 minutes
        with db.conn() as c:
            c.execute("UPDATE learn_cards SET due=? WHERE id=?", (time.time() - 100, old["id"]))
        add("c1", 2, module="fresh")
        order = [c["maturity"] for c in L.due_cards("c1")]
        self.assertNotEqual(order[0], "new")

    def test_cards_reviewed_before_the_upgrade_stay_in_the_review_queue(self):
        add("c1", 1)
        cid = L.list_cards("c1")[0]["id"]
        with db.conn() as c:  # what a database from before this feature looks like
            c.execute("UPDATE learn_cards SET last_reviewed=?, first_seen=NULL, reps=2, interval=6, due=? WHERE id=?", (time.time() - 86400 * 7, time.time() - 10, cid))
        db.init_db()
        self.assertEqual([c["id"] for c in L.due_cards("c1")], [cid])


class Editing(TempDB):
    def test_edit_dedupe_and_page_tags(self):
        n = L.add_cards("c1", "m", [{"front": "What is a tensor in deep learning?", "back": "An array of numbers on many axes", "page": 91, "kind": "basic"},
                                    {"front": "Define tensor in deep learning please", "back": "An array of numbers on many axes"},  # same answer
                                    {"front": "A […] is an array.", "back": "tensor", "kind": "cloze"}])
        self.assertEqual(n, 2)
        cards = L.list_cards("c1")
        self.assertEqual([(c["kind"], c["page"]) for c in cards], [("basic", 91), ("cloze", None)])
        self.assertTrue(L.update_card(cards[0]["id"], "What is a tensor?", "A multi-axis array"))
        self.assertEqual(L.list_cards("c1")[0]["back"], "A multi-axis array")
        self.assertFalse(L.update_card(cards[0]["id"], "x", "y"))  # too short to be a card


class Statistics(TempDB):
    def test_maturity_forecast_and_retention(self):
        add("c1", 4)
        ids = [c["id"] for c in L.due_cards("c1")]
        L.grade_card(ids[0], "good")
        L.grade_card(ids[1], "again")
        with db.conn() as c:
            c.execute("UPDATE learn_cards SET interval=30, reps=5, last_reviewed=?, due=? WHERE id=?", (time.time(), time.time() + 3 * 86400, ids[0]))
        st = L.card_stats("c1")
        self.assertEqual(st["total"], 4)
        self.assertEqual((st["maturity"]["mature"], st["maturity"]["new"]), (1, 2))
        self.assertEqual(st["retention"], 50)  # one good, one again
        self.assertEqual(st["reviewed_today"], 2)
        self.assertEqual(len(st["forecast"]), 7)
        self.assertEqual(st["forecast"][3]["due"], 1)  # the mature card comes back in 3 days
        self.assertGreaterEqual(st["forecast"][0]["due"], 1)  # the 'again' card is due today


if __name__ == "__main__":
    unittest.main()
