"""/learn - a personalised course, one lesson at a time. The app owns the structure and the progress:
 path (modules) -> lesson -> exercises -> quiz (answers are checked in the browser against a validated key) -> next lesson whose difficulty
follows the quiz score; missed questions come back as a revision section. The model writes lesson text and proposes questions."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ... import db, learning
from .. import book
from ..registry import Slot, Workflow, register

LEVELS = ["beginner", "intermediate", "advanced"]
CODE_RX = re.compile(r"\b(?:python|sql|javascript|java|c\+\+|c#|rust|go|golang|typescript|html|css|react|programming|coding|algorithms?|data structures|pandas|numpy|bash|shell|git)\b", re.I)


PY_RX = re.compile(r"\b(?:python|pandas|numpy|data structures?|algorithms?|programming basics|coding basics|scripting)\b", re.I)
WEB_RX = re.compile(r"\b(?:html|css|javascript|js|dom|web ?(?:development|design|page)|front-?end)\b", re.I)


def detect_mode(text: str) -> str:
    t = text.lower()
    if re.search(r"\binterview\b|\bprepare me for\b|\bprep for\b", t):
        return "interview"
    if re.match(r"\s*(?:explain|what is|what are|how does|how do|why)\b", t):
        return "explain"
    return "course"


def prefill(text: str, ctx: dict) -> dict:
    out: dict = {}
    t = text.strip()
    m = re.search(r"(?:teach me|learn|study|explain|prepare me for|master|understand|introduction to|intro to)\s+(.+?)(?:\s+(?:from|for|at|in|with|using)\s+(?:beginner|intermediate|advanced|scratch|zero).*)?$", t, re.I)
    out["subject"] = (m.group(1) if m else t).strip(" .?")[:120]
    low = t.lower()
    if re.search(r"from (?:beginner|scratch|zero|basics?)|\bbeginner\b|\bnew to\b|\bno experience\b|\bbasics?\b", low):
        out["level"] = "beginner"
    elif re.search(r"\badvanced\b|\bexpert\b", low) and not re.search(r"to advanced", low):
        out["level"] = "advanced"
    elif re.search(r"\bintermediate\b|\bsome experience\b", low):
        out["level"] = "intermediate"
    out["mode"] = detect_mode(t)
    bk = book.pick(ctx.get("files") or [])
    if bk:  # an attached book / notes file: teach from it
        out["book"] = bk
        if out["mode"] != "interview":
            out["mode"] = "book"
        if len(t) < 40 or re.fullmatch(r"(?:teach me|learn|study|explain|help me (?:learn|study))?\s*(?:this|the|my)?\s*(?:book|pdf|file|document|notes|chapter)?\s*", low):
            out["subject"] = re.sub(r"[_\-]+", " ", Path(bk["name"]).stem).strip()[:120]
    mm =re.search(r"(\d{1,3})\s*(?:min|minutes)\b", low)
    if mm:
        out["minutes"] = mm.group(1)
    return out


BAD_Q = re.compile(r"\b(?:on|from|in|at|of) (?:page|p\.)\s*\d+|\bpage \d+|\b(?:figure|fig\.|table|equation|eq\.|section) \d+(?:\.\d+)*|does not (?:provide|state|mention|specify|say)|not (?:explicitly )?(?:stated|mentioned|provided) in the (?:text|passage)", re.I)


def _valid_quiz(data) -> list[dict]:
    qs = []
    for q in (data.get("questions") if isinstance(data, dict) else data) or []:
        if not isinstance(q, dict):
            continue
        opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        try:
            ans = int(q.get("answer"))
        except (TypeError, ValueError):
            continue
        if BAD_Q.search(str(q.get("q", "")) + " " + " ".join(opts)):
            continue  # page/figure trivia and "the text does not say" answers teach nothing
        if str(q.get("q", "")).strip() and len(opts) == 4 and len(set(o.lower() for o in opts)) == 4 and 0 <= ans < 4:
            qs.append({"q": str(q["q"]).strip()[:300], "options": [o[:160] for o in opts], "answer": ans, "explain": str(q.get("explain") or "")[:300]})
    return qs[:6]


async def plan_assessments(rc, mod: dict, subject: str, topics: list[str], lesson: str, lang: str, interview: bool) -> dict:
    """The AI reads the module and decides which tests it actually needs - none is mandatory. -> {quiz, logic, coding, math, why, math_topic}.
    Code keeps the decision sane: a coding test only for modules about programming, a maths test only for maths subjects (its problems are
    generated and checked by code, so they are always right), and the fallback when the model gives no usable answer is a plain quiz."""
    title_text = f'{subject} {mod["title"]}'
    has_code = "```" in lesson or bool(lang) or bool(CODE_RX.search(title_text))
    math_topic = learning.math_topic(subject, mod["title"], mod.get("goal", ""), " ".join(topics[:8]))
    data = await rc.json_call(
        f'Decide which tests this module needs. Module: "{mod["title"]}" in a {subject} course. Topics: {"; ".join(topics[:8]) or mod.get("goal", "")}.\n'
        + (f"Lesson excerpt:\n{lesson[:1800]}\n\n" if lesson else "")
        + "quiz = a multiple-choice quiz (right for factual or conceptual modules). "
        "logic = a typed reasoning test where the learner must predict, explain a cause or find a flaw (right for modules built on cause and effect, algorithms, proofs, analysis, trade-offs). "
        "coding = a typed coding test (ONLY if the module is about writing programs). "
        "math = generated calculation practice (ONLY if the module is about numbers, algebra, statistics or similar). "
        "Choose only what genuinely helps for THIS module; it is fine to choose none (for example an overview or history module). "
        'Reply with JSON only: {"quiz": true or false, "logic": true or false, "coding": true or false, "math": true or false, "why": "one short sentence"}', max_tokens=220)
    if not isinstance(data, dict) or not any(k in data for k in ("quiz", "logic", "coding", "math")):
        return {"quiz": True, "logic": False, "coding": False, "math": False, "math_topic": None, "why": "The model did not give a test plan, so a standard quiz was used.", "fallback": True}
    want = {k: data.get(k) is True or str(data.get(k)).lower() == "true" for k in ("quiz", "logic", "coding", "math")}
    want["coding"] = (want["coding"] or lang == "python") and has_code and not interview  # a Python module always earns a coding test
    want["logic"] = want["logic"] and not interview
    want["math"] = want["math"] and bool(math_topic) and not interview
    return {**want, "math_topic": math_topic if want["math"] else None, "why": str(data.get("why") or "")[:200]}


async def make_path(rc, subject: str, level: str, goal: str, minutes: float, info: dict | None = None) -> list[dict]:
    src = ""
    if info and info.get("ok"):
        heads = info.get("outline") or []
        src = ("The course MUST follow this book. Its chapter headings, in order: " + " | ".join(heads) + ". ") if len(heads) >= 4 else \
              ("The course MUST follow this book. Evenly spaced openings from it: " + " | ".join(info.get("sample") or []) + ". ")
    data = await rc.json_call(
        f'Design a learning path for "{subject}". {src}The learner starts at {level} level{", goal: " + goal if goal else ""} and studies about {int(minutes)} minutes a day. '
        'Reply with JSON only: {"modules": [{"title": "", "goal": "what the learner can do afterwards", "lessons": ["3 to 4 short topics"]}]} '
        + ("with ONE module per chapter or major part of the book, in the book's order (3 to 12 modules). Use the book's own chapter titles. Do not add modules the book does not cover."
           if src else "with 6 to 8 modules in teaching order, from the learner's level up to a confident, practical level."), max_tokens=1200)
    mods = []
    for m in (data.get("modules") if isinstance(data, dict) else None) or []:
        if isinstance(m, dict) and str(m.get("title", "")).strip():
            mods.append({"title": str(m["title"]).strip()[:80], "goal": str(m.get("goal") or "")[:140], "lessons": [str(x)[:60] for x in (m.get("lessons") or [])][:5]})
    if src and len(mods) >= 2:
        return mods[:12]
    if len(mods) < 4:
        rc.notes.append("The model did not return a usable course outline, so a generic structure was used.")
        mods = [{"title": t, "goal": g, "lessons": []} for t, g in (
            (f"Foundations of {subject}", "Know the vocabulary and core ideas"), ("Core concepts", "Apply the main techniques"),
            ("Guided practice", "Solve typical problems with support"), ("Intermediate topics", "Combine techniques"),
            ("Real-world project", "Build something end to end"), ("Review and next steps", "Consolidate and plan further study"))]
    return mods[:8]


async def run(rc) -> dict:
    s, st = rc.slots, dict(rc.run.get("state") or {})
    subject = str(s.get("subject") or "")
    level = str(s.get("level") if s.get("level") in LEVELS else "beginner")
    goal = "" if s.get("goal") in (None, "flexible") else str(s["goal"])
    minutes = float(s.get("minutes") or 30)
    mode = str(s.get("mode") or "course")
    coding = bool(CODE_RX.search(subject))
    hist = st.get("quiz_history") or []

    # adaptive difficulty from the last quiz
    difficulty = st.get("difficulty", "normal")
    revision: list[dict] = []
    if rc.action == "next" and hist:
        last = hist[-1]
        pct = 100 * last["right"] / max(1, last["total"])
        difficulty = "harder" if pct >= 80 else "easier" if pct <= 40 else "normal"
        quiz_mem = (st.get("quizzes") or {}).get(str(last.get("module")), [])
        revision = [quiz_mem[i] for i in last.get("missed", []) if i < len(quiz_mem)]
    if rc.pref("learn.difficulty") == "harder" and difficulty == "normal":
        difficulty = "harder"
    if rc.pref("learn.pace") == "simple" and difficulty == "normal":
        difficulty = "easier"

    total_steps = 5
    # an attached book: index it once (page-tagged, local embeddings), then every lesson is written from retrieved passages
    bk, info = st.get("book"), None
    if not bk:
        slot_file = s.get("file") or s.get("book")
        bk = slot_file if isinstance(slot_file, dict) and slot_file.get("id") else book.pick(getattr(rc, "files", None) or [])
    if isinstance(bk, dict) and bk.get("id") and not bk.get("path"):  # courses made before the path was stored
        bk = dict(bk, path=(db.get_file(bk["id"]) or {}).get("path", ""))
    if bk and mode != "explain":
        if st.get("book") and book.indexed(bk["id"]):
            info = {"ok": True}
        else:
            await rc.progress(1, total_steps, "Reading and indexing your book (first time only)")
            async def _p(label):
                await rc.progress(1, total_steps, label)
            info = await book.prepare(bk, _p)
            if not info["ok"]:
                rc.notes.append(info["reason"] + " The lesson was written from the model's general knowledge instead.")
                bk = None
            else:
                rc.notes.append(f"Book indexed: {info['chunks']:,} passages across about {info['pages']} pages" + (" (reused)." if info.get("reused") else "."))
    else:
        bk = None
    path = st.get("path")
    if not path:
        await rc.progress(1, total_steps, "Designing your learning path")
        path = [{"title": subject.title(), "goal": f"Understand {subject} clearly", "lessons": []}] if mode == "explain" else await make_path(rc, subject, level, goal, minutes, info if bk else None)
    idx = int(st.get("current", 0))
    if rc.action == "next":
        done = set(st.get("learn_progress") or [])
        done.add(f"m{idx}")
        st["learn_progress"] = sorted(done)
        idx = min(idx + 1, len(path) - 1)
    mod = path[idx]
    finished = rc.action == "next" and idx == int(st.get("current", 0))

    await rc.progress(2, total_steps, f"Writing lesson {idx + 1}: {mod['title']}")
    style = {"harder": "Go deeper than usual and include one tricky edge case.", "easier": "Go slower: simpler words, smaller steps, one extra worked example.", "normal": ""}[difficulty]
    interview = mode == "interview"
    hits: list[dict] = []
    topics = list(mod["lessons"])
    if bk:
        # the book's own bookmarks give this chapter's page range and its real section titles (the model's topic list is only a guess)
        entries = await asyncio.to_thread(book.toc, bk.get("path") or (db.get_file(bk["id"]) or {}).get("path", ""))
        span = book.module_ranges(path, entries).get(idx) if entries else None
        secs = book.sections_in(entries, span[0], span[1]) if span else []
        if secs:
            topics = [t for t, _ in secs]
        query = f'{mod["title"]}. ' + ("; ".join(topics) if secs else f'{mod["goal"]} ' + " ".join(mod["lessons"]))
        hits = await book.retrieve(bk["id"], query, span, secs, limit=8)
        if not hits:
            rc.notes.append(f'No passage of the book matched "{mod["title"]}", so this lesson comes from the model\'s general knowledge.')
        elif span:
            rc.notes.append(f"Lesson sources are limited to this chapter (pages {span[0]}-{span[1]}) and cover its sections: {', '.join(topics[:8])}.")
    src = ("Teach ONLY from these passages of the learner's book and mention the page like (p. 12) when you use one. If they do not cover something, say so instead of adding it:"
           + chr(10) + book.as_prompt(hits, 1000) + chr(10) + chr(10)) if hits else ""
    lesson = await rc.text_call(src + (
        (f'Prepare the learner for a {subject} interview. Cover the topic: {mod["title"]}. Give the 6 most commonly asked questions on it with strong concise answers and common mistakes. '
         if interview else
         f'Teach "{mod["title"]}" as part of a {subject} course for a {level} learner. Goal: {mod["goal"]}. Topics: {"; ".join(topics) or "the essentials"}. ')
        + f"{style} Use markdown: a short intro, the core ideas with a real-world analogy, "
        + ("two worked code examples in fenced code blocks, " if coding else "two worked examples, ")
        + "and a 'Key takeaways' list. About 450 words. Do not invent statistics or sources."), max_tokens=1100)
    if len(lesson) < 80:
        return {"error": "The model returned no lesson text. Try again."}
    if hits:
        lesson, bad_cites = book.check_citations(lesson, hits)
        if bad_cites:
            rc.notes.append(f"Removed {len(bad_cites)} page citation(s) (p. {', '.join(str(b) for b in sorted(set(bad_cites)))}) that were not in the passages the lesson was written from.")

    await rc.progress(3, total_steps, "Preparing exercises")
    ex = await rc.json_call(
        ("Base the exercises on these passages of the learner's book (only ones that can be done on a single laptop with no special hardware):" + chr(10) + book.as_prompt(hits[::2][:4], 700) + chr(10) + chr(10) if hits else "") +
        f'Create 3 practice exercises for the lesson "{mod["title"]}" ({subject}, {level}, difficulty {difficulty}). Reply with JSON only: {{"exercises": [{{"task": "", "hint": "", "solution": ""}}]}}'
        + (' and one coding challenge in the same list with the task including a function signature and a small example' if coding else ""), max_tokens=900)
    exercises = [e for e in (ex.get("exercises") if isinstance(ex, dict) else None) or [] if isinstance(e, dict) and str(e.get("task", "")).strip()][:4]

    # hands-on labs: runnable Python with tests (verified before they are shown), a live HTML/JS try-it, or generated maths practice
    lab_blocks: list[dict] = []
    lang = "python" if PY_RX.search(subject + " " + mod["title"]) else "web" if WEB_RX.search(subject + " " + mod["title"]) else ""
    topic = None if lang else learning.math_topic(subject, mod["title"], " ".join(mod["lessons"]))
    if lang == "python" and not interview:
        await rc.progress(3, total_steps, "Building a coding lab (checking my own solutions first)")
        raw = await rc.json_call(
            f'Write 3 small Python coding exercises for the lesson "{mod["title"]}" ({subject}, {level}, difficulty {difficulty}), each solvable with one function. Reply with JSON only: '
            '{"exercises": [{"task": "one or two sentences naming the function and what it returns", "starter": "def name(args):\\n    # your code\\n    pass", '
            '"tests": ["assert name(1) == 2", "assert name(0) == 0", "assert name(5) == 10"], "solution": "def name(args):\\n    return ...", "hint": ""}]} '
            "Use 3 to 5 assert statements per exercise, plain Python only, no input(), no files, no randomness.", max_tokens=1500)
        good = await learning.verified_exercises(raw, 3)
        if good:
            lab_blocks.append({"type": "codelab", "title": "Coding lab", "course_id": rc.run.get("id"), "module": mod["title"], "exercises": good})
            rc.notes.append(f"Coding lab: {len(good)} exercise(s) kept; each model-written solution was run against its own tests first, so the tests are consistent.")
        else:
            rc.notes.append("The coding exercises the model wrote did not pass their own tests, so no coding lab was shown for this lesson. Use the scratchpad or press Next lesson.")
        lab_blocks.append({"type": "codelab", "title": "Python scratchpad", "course_id": rc.run.get("id"), "module": mod["title"], "exercises": [], "scratch": True})
    elif lang == "web":
        raw = await rc.json_call(
            f'Write ONE small, complete HTML page (with inline <style> and <script> if useful) that demonstrates "{mod["title"]}" ({subject}, {level}). '
            'Reply with JSON only: {"html": "<!doctype html>..."} Keep it under 40 lines, no external files or network calls.', max_tokens=900)
        html = str((raw or {}).get("html") or "").strip() if isinstance(raw, dict) else ""
        if len(html) > 40:
            lab_blocks.append({"type": "tryit", "title": "Try it yourself", "html": html[:6000], "course_id": rc.run.get("id"), "module": mod["title"]})
    elif topic:
        lvl = {"easier": 1, "normal": 2, "harder": 3}.get(difficulty, 2)
        lvl = max(1, min(3, lvl - (1 if level == "beginner" else 0) + (1 if level == "advanced" else 0)))
        lab_blocks.append({"type": "mathlab", "title": f"Practice: {topic}", "topic": topic, "level": lvl, "course_id": rc.run.get("id"), "module": mod["title"]})

    await rc.progress(4, total_steps, "Deciding which tests this module needs")
    plan = await plan_assessments(rc, mod, subject, topics, lesson, lang, interview)
    if plan.get("fallback"):
        rc.notes.append(plan["why"])
    await rc.progress(4, total_steps, "Writing the quiz" if plan["quiz"] else "Preparing tests")
    quiz = [] if not plan["quiz"] else _valid_quiz(await rc.json_call(
        ("Only ask about ideas stated in these passages of the learner's book:" + chr(10) + book.as_prompt(hits[:6], 700) + chr(10) + chr(10)
         + "Test understanding (why, what happens if, which is correct and why), not memory of page numbers, figure numbers or exact wording. "
         "Never use 'the text does not say' as an option. " if hits else "") +
        f'Write 5 multiple-choice questions testing the lesson "{mod["title"]}" ({subject}, {level}, difficulty {difficulty}). Each needs exactly 4 distinct options, the index (0-3) of the one '
        'correct option, and a one-sentence explanation. Reply with JSON only: {"questions": [{"q": "", "options": ["","","",""], "answer": 0, "explain": ""}]}', max_tokens=1400))
    if plan["quiz"] and len(quiz) < 3:
        quiz = _valid_quiz(await rc.json_call("Return ONLY JSON. Write 4 multiple-choice questions (exactly 4 distinct options each, correct index 0-3, one-sentence explanation) about " + f'"{mod["title"]}" in {subject}: '
                                              '{"questions": [{"q": "", "options": ["","","",""], "answer": 0, "explain": ""}]}', max_tokens=1100))
    quizzes = dict(st.get("quizzes") or {})
    key = f"m{idx}"
    quizzes[key] = quiz

    # flashcards for spaced repetition (stored in the learning hub; scheduled by SM-2, not by the model)
    new_cards: list[dict] = []
    course_id = rc.run.get("id")
    if course_id and not interview:
        cards_raw = await rc.json_call(
            "From this lesson write 8 flashcards for long-term memory. Mix three kinds: "
            '(1) "basic": a question and its short answer, including 2 "why" or "what happens if" questions; '
            '(2) "cloze": ONE sentence from the lesson with the key term replaced by ____ (front) and the term as the answer (back). '
            "Rules: every card must make sense on its own without the lesson, test ONE fact, and never say 'the text', 'the lesson' or 'the author'. "
            "Answers under 20 words and only from facts stated in the lesson. "
            'Reply with JSON only: {"cards": [{"type": "basic or cloze", "front": "", "back": "", "hint": "optional short clue"}]}'
            + chr(10) + chr(10) + "LESSON:" + chr(10) + lesson[:4000], max_tokens=1100)
        source_text = lesson + " " + " ".join(h["text"] for h in hits)
        kept, dropped = learning.flashcards.quality(learning.valid_cards(cards_raw), source_text)
        for card in kept:
            card["page"] = learning.flashcards.find_page(lesson, card)  # the book page of the sentence it came from, when the lesson cites one
        added = learning.add_cards(course_id, mod["title"], kept)
        new_cards = kept[:max(added, 0)] if added < len(kept) else kept
        if not added:
            new_cards = []
        if dropped:
            rc.notes.append(f"{len(dropped)} flashcard(s) the model wrote were dropped by the quality check ({', '.join(sorted(set(dropped)))}).")
    if course_id:
        learning.log_event(course_id, "lesson", mod["title"])

    blocks = [{"type": "overview", "title": f"{subject.title()} — lesson {idx + 1} of {len(path)}: {mod['title']}",
               "text": f"Level: {level} · about {int(minutes)} min a day · difficulty for this lesson: {difficulty}" + (" (adjusted from your last quiz)" if rc.action == "next" else "") + ".",
               "kpis": [{"label": "Lesson", "value": f"{idx + 1}/{len(path)}"}, {"label": "Quiz questions", "value": len(quiz)}, {"label": "Exercises", "value": len(exercises)}]},
              {"type": "path", "title": "Your learning path", "key": "learn_progress", "modules": path, "current": idx},
              {"type": "markdown", "title": "Lesson", "text": lesson}]
    if hits:
        blocks.append({"type": "markdown", "title": "From your book",
                       "text": f"This lesson was written from {len(hits)} passage(s) of **{bk['name']}**, pages {book.pages_label(hits)}. Open those pages to check any detail."})
    blocks.extend(lab_blocks)
    if new_cards:
        counts = learning.card_counts(course_id)
        blocks.append({"type": "flashcards", "title": "Flashcards", "course_id": course_id, "cards": new_cards[:8], "deck": counts})
    if revision:
        blocks.append({"type": "markdown", "title": "Revision: questions you missed last time",
                       "text": "\n\n".join(f"**{r['q']}**\n\nAnswer: {r['options'][r['answer']]}. {r.get('explain', '')}" for r in revision)})
    if exercises:
        blocks.append({"type": "markdown", "title": "Practice",
                       "text": "\n\n".join(f"**{i + 1}. {e['task']}**\n\n<details><summary>Hint</summary>{e.get('hint', '')}</details>\n\n<details><summary>Solution</summary>\n\n{e.get('solution', '')}\n\n</details>" for i, e in enumerate(exercises))})
    if quiz:
        blocks.append({"type": "quiz", "title": f"Quiz: {mod['title']}", "key": f"quiz_{idx}", "module": key, "questions": quiz})
    elif plan["quiz"]:
        rc.notes.append("A valid quiz could not be generated for this lesson; ask for the next lesson to continue.")
    for kind, label in (("logic", "Logic test"), ("coding", "Coding test")):
        if plan[kind]:
            blocks.append({"type": "check", "kind": kind, "title": f"{label}: {mod['title']}", "course_id": rc.run.get("id"), "module": idx, "module_title": mod["title"], "why": plan.get("why", "")})
    if not (quiz or plan["logic"] or plan["coding"] or plan.get("math") or lab_blocks):
        blocks.append({"type": "markdown", "title": "Tests", "text": "No test is needed for this module" + (f" - {plan['why']}" if plan.get("why") else "") + ". Press **Next lesson** when you are ready."})
    if finished:
        rc.notes.append("This was the last module of the course.")
    await rc.progress(5, total_steps, "Done")
    plans = dict(st.get("plans") or {})
    plans[key] = {k: plan.get(k) for k in ("quiz", "logic", "coding", "math", "math_topic", "why")}  # Test me shows the same recommendation later
    st.update({"path": path, "current": idx, "difficulty": difficulty, "quizzes": quizzes, "plans": plans})
    if bk:
        st["book"] = {"id": bk["id"], "name": bk["name"], "path": bk.get("path") or (db.get_file(bk["id"]) or {}).get("path", "")}
    return {"blocks": blocks, "state": st, "notes": ["Questions are generated by the local model and checked by its own answer key; if one looks wrong, trust the lesson text and your own check."],
            "facts": [f"Lesson {idx + 1} of {len(path)}: {mod['title']} ({subject}, {level}, difficulty {difficulty}).", f"The lesson has {len(exercises)} exercises and a {len(quiz)}-question quiz.",
                      "Lesson outline: " + "; ".join(topics or [mod["goal"]])]
              + ([f"The lesson is based on pages {book.pages_label(hits)} of the learner's book {bk['name']}."] if hits else []) + ([f"{len(revision)} missed question(s) from the last quiz are revised first."] if revision else []),
            "summary": f"Lesson {idx + 1}/{len(path)}: {mod['title']}", "what": "the lesson, exercises and the tests this module needs",
            "size": "2-3 sentences: what this lesson covers and what to do next (read, try the exercises, take the quiz, then press Next lesson)"}


register(Workflow(
    id="learn", skill="learn", title="Personalised learning", description="A step-by-step course with exercises, quizzes and adaptive difficulty",
    keywords=re.compile(r"teach|learn|explain|study|interview|course|tutorial", re.I), general=True, prefill=prefill, run=run,
    slots=[Slot("subject", "Subject", "What do you want to learn?"),
           Slot("book", "Book or notes", "", kind="file", required=False),
           Slot("level", "Your level", "How would you describe your current level?", kind="choice", choices=LEVELS + ["not sure"], pref="learn.level"),
           Slot("goal", "Goal", "", required=False), Slot("minutes", "Minutes per day", "", kind="number", required=False, default=30),
           Slot("mode", "Mode", "", required=False)]))
