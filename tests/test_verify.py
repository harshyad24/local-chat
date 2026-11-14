"""Accuracy layer: when to ground, and the claim checker."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from backend import verify

EVIDENCE = ("The Eiffel Tower is 330 metres tall and was completed in 1889 for the World's Fair in Paris. "
            "It was designed by the engineering company of Gustave Eiffel. Mayor Dana Whitfield said the council voted 7 to 2.")


class ShouldGround(unittest.TestCase):
    def test_lookup_questions(self):
        for q in ["Who won Euro 2024?", "What is the latest iPhone model?", "How many people live in Reykjavik?",
                  "When was the Eiffel Tower completed?", "Is the new Pixel available in India?"]:
            self.assertTrue(verify.should_ground(q), q)

    def test_not_lookups(self):
        for q in ["hi", "thanks!", "Write a poem about rain", "Fix this python function for me", "What is 17 * 24",
                  "Draw a flowchart of login", "make a bar chart of sales", "calculate 15% of 840", "What does https://example.com/page say about pricing?",
                  "A train leaves at 3:45 pm and travels for 2 hours 50 minutes. When does it arrive?"]:
            self.assertFalse(verify.should_ground(q), q)


class LexicalCheck(unittest.TestCase):
    def flagged(self, answer):
        return verify.lexical_check(answer, EVIDENCE, "Tell me about it")["flagged"]

    def test_faithful_answer_passes(self):
        self.assertEqual(self.flagged("The Eiffel Tower is 330 metres tall and was completed in 1889. Gustave Eiffel's company designed it."), [])

    def test_wrong_number(self):
        self.assertTrue(self.flagged("The Eiffel Tower is 300 metres tall."))
        self.assertTrue(self.flagged("The council voted 9 to 2."))  # a single digit must not hide inside another number

    def test_substring_number_is_not_support(self):
        self.assertTrue(self.flagged("The tower was completed in 188."))

    def test_invented_name(self):
        f = self.flagged("It was designed by Henri Lefebvre.")
        self.assertTrue(f)
        self.assertIn("Henri Lefebvre", f[0]["missing"])

    def test_filler_and_first_word_are_not_names(self):
        self.assertEqual(self.flagged("Overall, this is a well known landmark in Paris. Let me know if you want more detail."), [])

    def test_number_from_the_question_is_not_flagged(self):
        r = verify.lexical_check("In 2031 the tower will be 330 metres tall.", EVIDENCE, "What will the tower look like in 2031?")
        self.assertEqual(r["flagged"], [])

    def test_code_and_think_blocks_ignored(self):
        r = verify.lexical_check("<think>Maybe 999 metres</think>\n```\nx = 12345\n```\nThe tower is 330 metres tall.", EVIDENCE, "")
        self.assertEqual(r["flagged"], [])


class PostCheck(unittest.TestCase):
    def ctx(self, answer):
        st = {"acc": answer, "tools": [{"name": "web_search", "ok": True, "model_text": EVIDENCE * 3}], "sources": []}
        return SimpleNamespace(s={"num_ctx": 8192}, model="m", state=st)

    def run_post(self, answer, mode, judge_says_bad=True, rewrite="The tower is 330 metres tall."):
        ctx = self.ctx(answer)

        async def fake_complete(s, model, messages, **kw):
            text = messages[0]["content"]
            if "strict fact checker" in text:
                return '{"unsupported": [1]}' if judge_says_bad else '{"unsupported": []}'
            return rewrite

        async def go():
            return [e async for e in verify.postcheck(ctx, "Tell me", mode)]

        with mock.patch.object(verify.llm, "complete", fake_complete):
            return ctx, asyncio.run(go())

    ANSWER = "The Eiffel Tower is 300 metres tall, according to the sources provided to the app for this question."

    def test_flag_mode_keeps_text(self):
        ctx, events = self.run_post(self.ANSWER, "flag")
        self.assertEqual(ctx.state["acc"], self.ANSWER)
        v = events[-1]
        self.assertEqual(v["type"], "verification")
        self.assertEqual(len(v["unsupported"]), 1)
        self.assertFalse(v["fixed"])

    def test_fix_mode_replaces_text(self):
        ctx, events = self.run_post(self.ANSWER, "fix", rewrite="The Eiffel Tower is 330 metres tall, according to the sources provided.")
        self.assertIn("330", ctx.state["acc"])
        self.assertTrue(events[-1]["fixed"])
        self.assertEqual(events[0]["type"], "truncate")

    def test_gutted_rewrite_is_rejected(self):
        ctx, events = self.run_post(self.ANSWER, "fix", rewrite="No.")
        self.assertEqual(ctx.state["acc"], self.ANSWER)
        self.assertFalse(events[-1]["fixed"])

    def test_judge_overrules_false_alarm(self):
        ctx, events = self.run_post(self.ANSWER, "fix", judge_says_bad=False)
        self.assertEqual(events[-1]["unsupported"], [])
        self.assertEqual(ctx.state["acc"], self.ANSWER)

    def test_irrelevant_sources_are_not_used_to_contradict(self):
        ctx = self.ctx("The train arrives at 6:35 pm, after travelling for a total of two hours and fifty minutes.")
        async def go():
            return [e async for e in verify.postcheck(ctx, "When does the train from Pune arrive?", "fix")]
        self.assertEqual(asyncio.run(go()), [])

    def test_no_evidence_no_check(self):
        ctx = self.ctx(self.ANSWER)
        ctx.state["tools"] = []

        async def go():
            return [e async for e in verify.postcheck(ctx, "q", "fix")]
        self.assertEqual(asyncio.run(go()), [])


if __name__ == "__main__":
    unittest.main()
