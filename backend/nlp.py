"""Application-level NLP toolkit: small, CPU-only, predictable operations the main LLM shouldn't have to do.

Every public function takes plain text and returns {"ok": bool, ...result fields, "ms": float, "device": "cpu"}.
Languages: English, Hindi (Devanagari) and Hinglish (Hindi written in Latin letters).
Heavy pieces (spaCy model, scikit-learn) are loaded lazily and are optional: each operation falls back or reports
a clear error instead of crashing the app.
"""
from __future__ import annotations

import functools
import math
import re
import time
import unicodedata
from collections import Counter

MAX_CHARS = 200_000

# ------------------------------------------------------------------ plumbing
class NlpError(ValueError):
    pass


def _check(text, *, min_len=1) -> str:
    if not isinstance(text, str):
        raise NlpError("text must be a string")
    if len(text.strip()) < min_len:
        raise NlpError("text is empty")
    if len(text) > MAX_CHARS:
        raise NlpError(f"text is too long ({len(text)} characters; limit {MAX_CHARS})")
    return text


def op(fn):
    """Validate, time and wrap every operation in the same structured result."""
    @functools.wraps(fn)
    def run(text, *a, **kw):
        t0 = time.perf_counter()
        try:
            _check(text)
            out = fn(text, *a, **kw)
            res = {"ok": True, **out}
        except NlpError as exc:
            res = {"ok": False, "error": str(exc)}
        except Exception as exc:  # an optional dependency or model problem must not take the app down
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        res.setdefault("device", "cpu")
        res["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return res
    return run


# ------------------------------------------------------------------ word lists
EN_STOP = set("""a an the and or but if then else of to in on at by for with about as into from up down out over under again
further once here there when where why how all any both each few more most other some such no nor not only own same so than
too very can will just don should now is are was were be been being have has had having do does did doing i me my we our you
your he him his she her it its they them their what which who whom this that these those am would could may might must shall
also get got""".split())
HI_STOP = set("""है हैं था थे थी थीं हो होता होती होते का की के को में से पर और या एक यह वह ये वो इस उस कि जो तो भी ही नहीं न
मैं हम तुम आप वे उन इन कर करता करती करते किया गया गई गए रहा रही रहे सकता सकती सकते लिए साथ बहुत अपने अपना अपनी""".split())
HG_STOP = set("""hai hain tha thi the ho hoga hogi ka ki ke ko me mein se par pe aur ya ek yeh ye woh wo is us jo toh to bhi hi nahi
nahin na main mai hum tum aap kya kaise kyun kyon kab kahan kaun mujhe mera meri mere tumhara apna apni apne kar karo kare
karna kiya gaya gayi raha rahi rahe sakta sakti sakte liye sath bahut abhi phir lekin agar kuch koi""".split())

# common romanized-Hindi words (stop words + frequent content words) for Hinglish detection
HG_WORDS = HG_STOP | set("""accha achha theek thik bhai yaar kyunki kyuki matlab samjha samjhao batao bata bolo dekho dekh chalo
paisa paise kaam ghar log din raat kal aaj sab sabhi zyada jyada kam bohot bahot mast badiya bekar bura acha pyaar
dost dil naam kitna kitni kitne wala wali wale hona hota hoti hote chahiye chahta chahte milega milta dena dedo lelo
banao banana likho likh padhna padh samajh shuru khatam""".split())


# ------------------------------------------------------------------ 1. normalization
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_QUOTES = {ord(k): v for k, v in {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'", "–": "-", "—": "-", "…": "..."}.items()}


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text).translate(_ZW).translate(_QUOTES)
    t = "".join(ch for ch in t if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return "\n".join(line.strip() for line in t.split("\n")).strip()


@op
def normalize(text: str) -> dict:
    out = _normalize(text)
    return {"text": out, "changed": out != text}


# ------------------------------------------------------------------ 2. language
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[A-Za-zऀ-ॿ']+")


def _lang(text: str) -> dict:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return {"language": "Unknown", "code": "und", "script": "none", "confidence": 0.0}
    dev = len(_DEVANAGARI.findall(text)) / len(letters)
    lat = len(_LATIN.findall(text)) / len(letters)
    if dev >= 0.6:
        return {"language": "Hindi", "code": "hi", "script": "Devanagari", "confidence": round(min(1.0, 0.7 + dev * 0.3), 2)}
    words = [w.lower() for w in _WORD.findall(text) if _LATIN.match(w)]
    hg = sum(1 for w in words if w in HG_WORDS)
    ratio = hg / max(1, len(words))
    if dev > 0.15 and lat > 0.15:  # mixed scripts
        return {"language": "Hinglish", "code": "hi-Latn", "script": "mixed", "confidence": 0.8}
    if lat >= 0.6 and len(words) >= 2 and ratio >= 0.22 and hg >= 2:
        return {"language": "Hinglish", "code": "hi-Latn", "script": "Latin", "confidence": round(min(0.95, 0.55 + ratio), 2)}
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        best = detect_langs(text[:3000])[0]
        names = {"en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
                 "ar": "Arabic", "ru": "Russian", "zh-cn": "Chinese", "ja": "Japanese", "ko": "Korean", "bn": "Bengali", "ta": "Tamil", "ur": "Urdu"}
        return {"language": names.get(best.lang, best.lang), "code": best.lang, "script": "Latin" if lat > 0.5 else "other", "confidence": round(best.prob, 2)}
    except Exception:
        return {"language": "English", "code": "en", "script": "Latin", "confidence": 0.5, "note": "langdetect unavailable; assumed English"}


@op
def detect_language(text: str) -> dict:
    return _lang(text)


# ------------------------------------------------------------------ 3. intent
# (intent, pattern list, weight). Rules + weights keep results explainable; confidence is the score share.
INTENTS: dict[str, list[str]] = {
    "dashboard": [r"\bdashboards?\b", r"\bkpis?\b", r"डैशबोर्ड"],
    "chart": [r"\b(chart|graph|plot|histogram|heatmap|pie|bar chart|line chart|visuali[sz]e)\b", r"चार्ट|ग्राफ"],
    "flowchart": [r"\b(flow ?chart|flow diagram|sequence diagram|mind ?map|diagram|process flow)\b"],
    "document_analysis": [r"\b(pdf|docx?|document|file|attachment|report|paper|contract|invoice)\b", r"\b(analy[sz]e|review|extract|read) (this|the|my)\b", r"दस्तावेज़|फ़ाइल"],
    "summarization": [r"\b(summari[sz]e|summary|tl;?dr|shorten|key points|brief)\b", r"सारांश|संक्षेप"],
    "translation": [r"\btranslate\b|\btranslation\b|\bin (hindi|english|hinglish|spanish|french)\b", r"अनुवाद"],
    "coding": [r"\b(code|function|bug|error|python|javascript|sql|regex|script|debug|compile|api|class)\b|```"],
    "research": [r"\b(research|investigate|compare|market|competitors?|sources?|literature)\b", r"\b(latest|news|today|current|price of)\b"],
    "calculation": [r"\b(calculate|compute|solve|equation|integral|derivative|percent(age)?)\b|\d+\s*[-+*/^]\s*\d+", r"हिसाब|गणना"],
    "memory": [r"\b(remember|forget|don'?t forget|my name is|i (like|prefer|am|live))\b", r"याद रखो|याद रखना"],
    "automation": [r"\b(n8n|workflow|automate|automation|schedule|email me|calendar)\b"],
    "learning": [r"\b(quiz|flashcards?|explain|teach|learn|study|lesson|exam)\b", r"समझाओ|समझा|सिखाओ|पढ़ाई"],
    "writing": [r"\b(write|draft|rewrite|essay|email|letter|story|poem|blog|caption)\b", r"लिखो|लिखें"],
    "greeting": [r"^\s*(hi|hello|hey|namaste|namaskar|good (morning|evening|afternoon)|kaise ho|kya haal)\b", r"^\s*(नमस्ते|हेलो)"],
}


@op
def classify_intent(text: str) -> dict:
    low = text.lower()
    scores: dict[str, float] = {}
    for name, pats in INTENTS.items():
        s = sum(len(re.findall(p, low if p.isascii() or True else text, flags=re.I | re.M)) for p in pats)
        if s:
            scores[name] = float(s)
    if not scores:
        return {"intent": "general_chat", "confidence": 0.4, "scores": {}}
    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, val = ranked[0]
    margin = val / total
    conf = round(min(0.95, 0.5 + 0.45 * margin * min(1.0, val / 2)), 2)
    return {"intent": top, "confidence": conf, "scores": {k: round(v / total, 2) for k, v in ranked[:4]}}


# ------------------------------------------------------------------ 4. NER
_PATTERNS = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "URL": re.compile(r"https?://[^\s<>\"')]+|www\.[^\s<>\"')]+"),
    "MONEY": re.compile(r"(?:₹|Rs\.?|INR|\$|€|£|USD)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakh|lakhs|crore|crores|k|m|bn|million|billion))?|\d[\d,]*(?:\.\d+)?\s?(?:rupees|dollars|euros|रुपये|रुपए)", re.I),
    "DATE": re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}(?:,? \d{4})?\b|\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,? ?\d{0,4}\b", re.I),
    "PHONE": re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "PERCENT": re.compile(r"\d+(?:\.\d+)?\s?%"),
}
_nlp_model = None
_nlp_failed = False


def _spacy():
    global _nlp_model, _nlp_failed
    if _nlp_model is None and not _nlp_failed:
        try:
            import spacy
            _nlp_model = spacy.load("en_core_web_sm", disable=["lemmatizer"])
        except Exception:
            _nlp_failed = True
    return _nlp_model


def _entities(text: str, lang: str) -> tuple[list[dict], str]:
    ents: list[dict] = []
    taken: list[tuple[int, int]] = []
    for label, rx in _PATTERNS.items():
        for m in rx.finditer(text):
            ents.append({"text": m.group(0).strip(), "label": label, "start": m.start(), "end": m.end()})
            taken.append((m.start(), m.end()))
    method = "regex"
    nlp = _spacy() if lang == "English" else None  # the English model mislabels Hinglish words, so Hinglish uses patterns only
    if nlp is not None:
        method = "spacy+regex"
        for e in nlp(text[:50_000]).ents:
            if e.label_ in ("CARDINAL", "ORDINAL", "QUANTITY", "MONEY", "DATE", "PERCENT", "TIME"):
                continue
            if any(s < e.end_char and e.start_char < t for s, t in taken):
                continue
            ents.append({"text": e.text, "label": {"GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION", "NORP": "GROUP"}.get(e.label_, e.label_),
                         "start": e.start_char, "end": e.end_char})
    else:  # no model (Hindi, or spaCy missing): runs of capitalised words that aren't sentence starters
        for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text):
            if not any(s < m.end() and m.start() < t for s, t in taken) and m.group(0).lower() not in EN_STOP:
                ents.append({"text": m.group(0), "label": "NAME", "start": m.start(), "end": m.end()})
    ents.sort(key=lambda e: e["start"])
    return ents, method


@op
def extract_entities(text: str) -> dict:
    lang = _lang(text)["language"]
    ents, method = _entities(text, lang)
    return {"entities": ents, "method": method, "language": lang}


# ------------------------------------------------------------------ 5. keywords
def _stop_for(lang: str) -> set[str]:
    return EN_STOP | HI_STOP | HG_STOP if lang in ("Hinglish", "Hindi") else EN_STOP


@op
def extract_keywords(text: str, limit: int = 10) -> dict:
    """RAKE-style phrase scoring: phrases between stop words, scored by word degree/frequency."""
    lang = _lang(text)["language"]
    nlp = _spacy() if lang == "English" else None
    if nlp is not None:  # noun phrases are what people mean by "keywords" in English
        counts: Counter = Counter()
        for chunk in nlp(text[:50_000]).noun_chunks:
            toks = [t for t in chunk if t.pos_ in ("NOUN", "PROPN", "ADJ") and not t.is_stop and len(t.text) > 1]
            if toks and any(t.pos_ in ("NOUN", "PROPN") for t in toks):
                counts[" ".join(t.text.lower() for t in toks[-3:])] += 1
        if counts:
            ranked = sorted(counts.items(), key=lambda kv: (-(kv[1] * (1 + 0.25 * len(kv[0].split()))), kv[0]))
            picked: list[tuple[str, float]] = []
            for k, v in ranked:
                if any(k in r or r in k for r, _ in picked):
                    continue
                picked.append((k, v * (1 + 0.25 * len(k.split()))))
                if len(picked) >= max(1, min(int(limit), 50)):
                    break
            top = picked[0][1]
            return {"keywords": [{"keyword": k, "score": round(v / top, 3)} for k, v in picked], "language": lang, "method": "noun-phrases"}
    stop = _stop_for(lang)
    phrases: list[list[str]] = []
    for sent in re.split(r"[.!?।\n;:,()\[\]\"]+", text.lower()):
        cur: list[str] = []
        for w in _WORD.findall(sent):
            if w in stop or len(w) < 2:
                if cur:
                    phrases.append(cur); cur = []
            else:
                cur.append(w)
        if cur:
            phrases.append(cur)
    freq, deg = Counter(), Counter()
    for p in phrases:
        for w in p:
            freq[w] += 1
            deg[w] += len(p) - 1
    word_score = {w: (deg[w] + freq[w]) / freq[w] for w in freq}
    cand: dict[str, float] = {}
    for p in phrases:
        # long runs are cut into 3-word windows so no content is dropped
        parts = [p] if len(p) <= 4 else [p[i:i + 3] for i in range(len(p) - 2)]
        for q in parts:
            key = " ".join(q)
            cand[key] = max(cand.get(key, 0), sum(word_score[w] for w in q) + 0.01 * freq[q[0]])
    ranked = []
    for k, v in sorted(cand.items(), key=lambda kv: (-kv[1], kv[0])):
        if any(len(set(k.split()) & set(r.split())) >= max(1, min(len(k.split()), len(r.split())) - 1) for r, _ in ranked):
            continue  # overlaps a better phrase
        ranked.append((k, v))
        if len(ranked) >= max(1, min(int(limit), 50)):
            break
    top = ranked[0][1] if ranked else 1.0
    return {"keywords": [{"keyword": k, "score": round(v / top, 3)} for k, v in ranked], "language": lang, "method": "rake"}


# ------------------------------------------------------------------ 6. sentiment
POS = set("""good great excellent amazing awesome wonderful love loved like liked happy best fantastic perfect nice brilliant
superb enjoy helpful easy fast improved growth profit win success pleased delighted recommend beautiful""".split()) | set(
    """accha achha acha badiya mast zabardast shandar behtareen khush pyaar pasand maza kamaal bahut-accha theek""".split()) | set(
    """अच्छा अच्छी बढ़िया शानदार बेहतरीन खुश प्यार पसंद मज़ा कमाल बेहतर सुंदर""".split())
NEG = set("""bad terrible awful horrible hate hated worst poor sad angry slow broken bug fail failed failure loss problem issue
disappointed disappointing useless difficult hard annoying waste decline drop crash error wrong ugly boring""".split()) | set(
    """bura bekar ganda kharab ghatiya bakwas dukhi gussa nafrat pareshan problem dikkat mushkil bekaar""".split()) | set(
    """बुरा बेकार खराब गंदा घटिया बकवास दुखी गुस्सा नफरत परेशान दिक्कत मुश्किल""".split())
NEGATORS = {"not", "no", "never", "n't", "cannot", "nahi", "nahin", "nhi", "na", "mat", "नहीं", "न", "मत", "without"}
BOOST = {"very", "really", "extremely", "so", "bahut", "bohot", "bahot", "ekdum", "बहुत", "बेहद"}


@op
def analyze_sentiment(text: str) -> dict:
    lang = _lang(text)["language"]
    splitter = re.compile(r"[,.;!?\u0964\n]+|\b(?:but|lekin|par)\b", re.I)
    clauses = [re.findall(r"[A-Za-z\u0900-\u097F']+", c.replace("n't", " n't").lower()) for c in splitter.split(text)]
    toks = [w for c in clauses for w in c]
    score = 0.0
    hits = 0
    for toks_c in clauses:  # negation never crosses a clause boundary
        for i, w in enumerate(toks_c):
            v = 1.0 if w in POS else -1.0 if w in NEG else 0.0
            if not v:
                continue
            # English negates from the left ("not good"); Hindi/Hinglish negates from the right ("accha nahi")
            if any(n in toks_c[max(0, i - 2): i] for n in NEGATORS) or any(n in toks_c[i + 1: i + 3] for n in NEGATORS):
                v = -v * 0.8
            if i and toks_c[i - 1] in BOOST:
                v *= 1.5
            score += v; hits += 1
    excl = text.count("!") * 0.1 * (1 if score >= 0 else -1) if hits else 0
    norm = max(-1.0, min(1.0, (score + excl) / math.sqrt(max(1, len(toks) / 3))))
    label = "positive" if norm > 0.15 else "negative" if norm < -0.15 else "neutral"
    return {"sentiment": label, "score": round(norm, 3), "confidence": round(min(0.95, 0.4 + abs(norm) * 0.55) if hits else 0.35, 2),
            "matched_terms": hits, "language": lang, "method": "lexicon"}


# ------------------------------------------------------------------ 7. similarity (TF-IDF fallback; embeddings arrive with the embedding service)
def _tfidf_sim(a: str, b: str) -> float:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    v = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), lowercase=True)
    m = v.fit_transform([a, b])
    return float(cosine_similarity(m[0], m[1])[0, 0])


@op
def semantic_similarity(text: str, other: str = "") -> dict:
    _check(other)
    try:  # use real embeddings when the embedding service is installed and enabled
        from . import embeddings
        s = embeddings.similarity(text, other)
        if s is not None:
            return {"similarity": round(s["score"], 4), "method": s["method"], "device": s.get("device", "cpu")}
    except Exception:
        pass
    return {"similarity": round(_tfidf_sim(_normalize(text), _normalize(other)), 4), "method": "tfidf-char-ngrams"}


# ------------------------------------------------------------------ 8. summarization (extractive, TextRank-style)
def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 2]


@op
def summarize(text: str, sentences: int = 3) -> dict:
    sents = _sentences(text)
    n = max(1, min(int(sentences), 10))
    if len(sents) <= n:
        return {"summary": " ".join(sents), "sentences": len(sents), "method": "passthrough"}
    from sklearn.feature_extraction.text import TfidfVectorizer
    import numpy as np
    stop = _stop_for(_lang(text)["language"])
    vec = TfidfVectorizer(stop_words=list(stop), token_pattern=r"(?u)[\wऀ-ॿ']{2,}")
    try:
        m = vec.fit_transform(sents)
    except ValueError:
        return {"summary": " ".join(sents[:n]), "sentences": n, "method": "lead"}
    sim = (m @ m.T).toarray()
    np.fill_diagonal(sim, 0)
    row = sim.sum(axis=1, keepdims=True)
    row[row == 0] = 1
    P = sim / row
    r = np.full(len(sents), 1 / len(sents))
    for _ in range(40):  # PageRank power iteration
        r = 0.15 / len(sents) + 0.85 * (P.T @ r)
    pos_bonus = np.linspace(0.05, 0, len(sents))  # news-like text front-loads its point
    pick = sorted(np.argsort(-(r + pos_bonus))[:n])
    return {"summary": " ".join(sents[i] for i in pick), "sentences": n, "of": len(sents), "method": "textrank"}


# ------------------------------------------------------------------ 9. relationships
_REL_PAT = re.compile(r"\b([A-Z][\w&-]*(?: [A-Z][\w&-]*)*) (acquired|bought|founded|created|owns|owned|leads|led|manages|joined|"
                      r"partnered with|works at|works for|is the (?:CEO|CTO|founder|head) of|invested in|competes with|launched|built) "
                      r"([A-Z][\w&-]*(?: [A-Z][\w&-]*)*)")


@op
def extract_relationships(text: str) -> dict:
    rels: list[dict] = []
    nlp = _spacy()
    method = "pattern"
    if nlp is not None:
        method = "spacy-dependency+pattern"
        for sent in nlp(text[:30_000]).sents:
            for tok in sent:
                if tok.pos_ != "VERB":
                    continue
                subj = [c for c in tok.children if c.dep_ in ("nsubj", "nsubjpass")]
                objs = [c for c in tok.children if c.dep_ in ("dobj", "attr", "dative")]
                for c in tok.children:  # verb + preposition + object ("partnered with X")
                    if c.dep_ == "prep":
                        objs += [o for o in c.children if o.dep_ == "pobj"]
                for s in subj:
                    for o in objs:
                        if s.ent_type_ and o.ent_type_ or (s.pos_ == "PROPN" and o.pos_ == "PROPN"):
                            rels.append({"subject": " ".join(t.text for t in s.subtree).strip(), "relation": tok.lemma_ or tok.text,
                                         "object": " ".join(t.text for t in o.subtree).strip(), "sentence": sent.text.strip()[:200]})
    for m in _REL_PAT.finditer(text):
        item = {"subject": m.group(1), "relation": m.group(2), "object": m.group(3), "sentence": ""}
        if not any(r["subject"] == item["subject"] and r["object"] == item["object"] for r in rels):
            rels.append(item)
    return {"relationships": rels[:50], "method": method}


# ------------------------------------------------------------------ 10. classification into caller-supplied labels
@op
def classify_text(text: str, labels: list | None = None) -> dict:
    if not labels or not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels):
        raise NlpError("labels must be a non-empty list of strings")
    if len(labels) > 50:
        raise NlpError("at most 50 labels")
    scores = []
    for lab in labels:
        s = 0.0
        try:
            from . import embeddings
            e = embeddings.similarity(text, lab)
            s = e["score"] if e else _tfidf_sim(text, lab)
        except Exception:
            s = _tfidf_sim(text, lab)
        words = set(re.findall(r"\w+", lab.lower())) - EN_STOP
        if words and any(re.search(rf"\b{re.escape(w)}\b", text.lower()) for w in words):
            s += 0.25  # explicit mention of the label's words
        scores.append((lab, s))
    total = sum(max(s, 0) for _, s in scores) or 1.0
    ranked = sorted(scores, key=lambda x: -x[1])
    return {"label": ranked[0][0], "confidence": round(max(ranked[0][1], 0) / total, 3), "scores": {k: round(max(v, 0) / total, 3) for k, v in ranked}}


# ------------------------------------------------------------------ combined analysis
@op
def analyze(text: str, keywords: int = 8) -> dict:
    """One call that returns the whole picture. Shape matches the documented example."""
    clean = _normalize(text)
    lang = _lang(clean)
    intent = classify_intent(clean)
    ents = extract_entities(clean)
    kws = extract_keywords(clean, keywords)
    sent = analyze_sentiment(clean)
    return {
        "language": lang["language"], "language_code": lang["code"], "script": lang["script"],
        "intent": intent.get("intent"), "entities": ents.get("entities", []),
        "keywords": [k["keyword"] for k in kws.get("keywords", [])],
        "sentiment": sent.get("sentiment"), "confidence": min(x for x in (lang["confidence"], intent.get("confidence", 0)) if x is not None),
        "parts": {"intent_scores": intent.get("scores"), "sentiment_score": sent.get("score"), "ner_method": ents.get("method")},
    }


OPERATIONS = {
    "normalize": normalize, "language": detect_language, "intent": classify_intent, "entities": extract_entities,
    "keywords": extract_keywords, "sentiment": analyze_sentiment, "similarity": semantic_similarity,
    "summarize": summarize, "relationships": extract_relationships, "classify": classify_text, "analyze": analyze,
}
