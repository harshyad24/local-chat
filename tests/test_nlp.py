import unittest

from backend import nlp


class NlpTests(unittest.TestCase):
    def test_validation(self):
        self.assertFalse(nlp.detect_language("")["ok"])
        self.assertFalse(nlp.detect_language(None)["ok"])
        self.assertFalse(nlp.normalize("x" * (nlp.MAX_CHARS + 1))["ok"])
        r = nlp.classify_text("hello", [])
        self.assertFalse(r["ok"])
        self.assertIn("labels", r["error"])

    def test_result_shape(self):
        r = nlp.detect_language("Hello there, how are you today?")
        self.assertTrue(r["ok"])
        self.assertEqual(r["device"], "cpu")
        self.assertIsInstance(r["ms"], float)

    def test_normalize(self):
        r = nlp.normalize("  “Hi”​   there  \n\n\n\nbye ")
        self.assertEqual(r["text"], '"Hi" there\n\nbye')

    def test_three_languages(self):
        self.assertEqual(nlp.detect_language("The quarterly report shows revenue growth across all regions.")["language"], "English")
        self.assertEqual(nlp.detect_language("मुझे यह फ़ाइल बहुत अच्छी लगी और दिल्ली में बैठक कल है")["language"], "Hindi")
        self.assertEqual(nlp.detect_language("bhai ye report ka summary bata do, mujhe samajh nahi aaya")["language"], "Hinglish")

    def test_intent(self):
        self.assertEqual(nlp.classify_intent("Build me a sales dashboard with KPIs")["intent"], "dashboard")
        self.assertEqual(nlp.classify_intent("please translate this to Hindi")["intent"], "translation")
        self.assertEqual(nlp.classify_intent("Hello!")["intent"], "greeting")
        self.assertEqual(nlp.classify_intent("zzz qqq")["intent"], "general_chat")

    def test_entities_patterns(self):
        e = nlp.extract_entities("Mail raj@example.com about the Rs. 5,000 invoice due 12/03/2025, see https://x.org/a, 15% off")["entities"]
        labels = {x["label"] for x in e}
        self.assertTrue({"EMAIL", "MONEY", "DATE", "URL", "PERCENT"} <= labels, labels)

    def test_sentiment_negation_and_hinglish(self):
        self.assertEqual(nlp.analyze_sentiment("The service was not bad, really helpful and fast!")["sentiment"], "positive")
        self.assertEqual(nlp.analyze_sentiment("yeh movie accha nahi tha, bekar hai")["sentiment"], "negative")
        self.assertEqual(nlp.analyze_sentiment("यह फ़ाइल बहुत अच्छी है")["sentiment"], "positive")
        self.assertEqual(nlp.analyze_sentiment("The table is brown.")["sentiment"], "neutral")

    def test_keywords_summary_relationships(self):
        t = "Machine learning models need training data. Training data quality decides model accuracy. Weather was fine."
        kws = [k["keyword"] for k in nlp.extract_keywords(t, 5)["keywords"]]
        self.assertTrue(any("training data" in k for k in kws), kws)
        s = nlp.summarize("One. Two things here are good. Three more words appear. Four is great fun. Five ends it.", 2)
        self.assertEqual(s["sentences"], 2)
        rels = nlp.extract_relationships("Apple acquired Beats. Satya Nadella leads Microsoft.")["relationships"]
        self.assertIn(("Apple", "Beats"), {(r["subject"], r["object"]) for r in rels})

    def test_classify_text_and_analyze(self):
        r = nlp.classify_text("The invoice total is $500 due Friday", ["finance", "sports", "cooking"])
        self.assertEqual(r["label"], "finance")
        a = nlp.analyze("Please analyze this PDF report from Acme Corp, it was great!")
        for k in ("language", "intent", "entities", "keywords", "sentiment", "confidence"):
            self.assertIn(k, a)
        self.assertEqual(a["intent"], "document_analysis")


if __name__ == "__main__":
    unittest.main()
