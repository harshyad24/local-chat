# Contributing and reviewer's guide

Thank you for looking at Local Chat. This page is for anyone who wants to **review, test or improve** the project — whether that takes an afternoon or becomes ongoing work. It explains where to start, what is most useful to check, and how credit works.

> **Credit follows what you actually do.** Contributors are listed for the work they really did (a review, a bug report, a pull request, docs, tests). Nobody is listed for work that did not happen, and the history on GitHub is the record. Be specific in what you claim and what you are credited for.

---

## 1. A 30-minute tour

You need Python 3.10–3.12, [Ollama](https://ollama.com) and one model (`ollama pull qwen3.5:9b`). Setup steps are in the [README](README.md#install-and-run).

```
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests    # expect all green (413 on the author's machine)
node --test tests/                      # 28 tests
python run.py                           # http://127.0.0.1:8000
```

Then try these, in order. Write down anything that surprises you.

1. **Plain chat** — ask a question; try *Edit*, *Retry* and branching.
2. **A dashboard** — attach a small CSV and write "analyze this and make a dashboard".
3. **`/learn` on a book** — attach a textbook PDF, send `/learn teach me this book`, open *Learning → Courses*. Check that the lesson's page citations (e.g. "p. 204") really match the book.
4. **Learning → Test me** — pick a module, press "What should I test?", take a logic or coding test and type a deliberately weak answer, then a strong one. Is the score sensible? Is the "book first, internet only if the book doesn't cover it" rule visible in *Checked against*?
5. **Settings → Connections → Plugins** — connect the *Folder* plugin to a throw-away folder and ask the chat about its files. Try to make it leave the folder.

---

## 2. Where the code is (read in this order)

| Order | File | What to look for |
|---|---|---|
| 1 | `README.md` — Architecture section | The seven diagrams: the whole design in ten minutes |
| 2 | `backend/agent.py` | The tool loop: validation, approvals, the dashboard JSON-extraction path for models that cannot call tools |
| 3 | `backend/skills/book.py` | Book retrieval: chapter scoping from PDF bookmarks, hybrid dense + BM25 search, citation check |
| 4 | `backend/skills/workflows/learn.py` | How a lesson, its flashcards and the **test plan** are produced, and which parts are code vs model |
| 5 | `backend/examiner.py` | Typed-answer checking: score bands, citation check, follow-up limit, book-first rule, prompt-injection handling |
| 6 | `backend/learning.py`, `backend/flashcards.py` | SM-2 spaced repetition, card quality filter |
| 7 | `backend/plugins.py`, `backend/mcp_client.py`, `backend/secretbox.py` | Plugin catalog, consent/approval cards, write limits, DPAPI encryption |
| 8 | `plugins/*.py` | The MCP servers (mail, calendar, folder, Telegram bot) — what each can and cannot do |
| 9 | `frontend/js/learnhub.js`, `learnlab.js`, `pluginsui.js` | The Learn and Plugins screens (plain JS, no build step) |
| 10 | `tests/` | What is asserted. Good tests to read first: `test_plugin_security.py`, `test_examiner.py`, `test_book.py` |

The design rule to keep in mind while reading: **the app (code) owns the control flow; the model only writes text, and its output is validated.** Report any place where the model is trusted more than it should be.

---

## 3. What is most useful to review

**Security (highest value)**
- Can the model, or text coming from a web page / email / calendar entry / typed answer, make a plugin act without the approval card? (`mcp_client.needs_approval`, `allow_always`, `describe_call`)
- Can the Folder plugin be made to read outside its folder (`..`, links, odd Windows paths)?
- Can the Telegram `send_message` tool exceed its limits (1,000 characters, 10/hour, 50/day, paired chats only)?
- Does any secret appear in an error message, the activity log, a settings response or a log file?
- Is the Python sandbox (`backend/sandbox_runner.py`, `tools.run_python`) escapable (network, files, environment)?

**Correctness**
- Edge cases in repeating calendar events (`plugins/calendar_server.py` supports only some RRULE forms).
- Are SM-2 intervals and the undo behaviour right (`learning.py`)?
- Do page citations in lessons ever point at a page that was not retrieved?

**Quality of the AI features**
- Does the test planner (`plan_assessments`) recommend sensible tests for different kinds of modules?
- Are the examiner's scores reasonable compared with how *you* would mark the same answer?

**Usability and docs** — anything confusing on the screens, missing instructions, wrong statements in the README.

---

## 4. Good first tasks

Pick one that matches your time. Each is concrete and verifiable.

| # | Task | Time | Output |
|---|---|---|---|
| 1 | Run the whole test suite on **macOS or Linux** and report every failure or skipped test | 1 h | an issue with the log |
| 2 | Run `python scripts/eval_retrieval.py --file-id <id>` on a **different** textbook and post the table (needs the book indexed by `/learn` once) | 1–2 h | an issue with the table |
| 3 | Install `chromadb` on a machine where it runs and compare it with the default store using the same script | 2 h | an issue with numbers |
| 4 | **Human-grade the examiner:** write 20–30 question/answer pairs with your own 0–100 scores, run them through `POST /api/learn/exam/grade`, and compare | half a day | a script + results (very valuable) |
| 5 | Try to break the plugin approval flow or the Folder sandbox (see section 3) | 2–4 h | issues, ideally with a failing test |
| 6 | Add tests for a code path you read and found untested | 1–3 h | a pull request |
| 7 | Add screenshots and a short "first five minutes" section to the README | 1–2 h | a pull request |
| 8 | Replace PyMuPDF (AGPL) with a permissive PDF library in `backend/ocr.py` and `backend/skills/book.py` while keeping chapter scoping working (see `tests/test_book.py`) | 1–2 days | a pull request |

The README's [Under testing / not verified yet](README.md#under-testing--not-verified-yet) list is the other source of tasks.

---

## 5. How to report an issue

Please include: what you did, what you expected, what happened, your OS, Python version, model name and size, and any error text. For a model-quality problem, also say which model you used — small models fail in ways larger ones do not.

**Security problems:** do not post exploit details in a public issue. Contact the maintainer privately first: *(maintainer: add a contact address or use GitHub's private vulnerability reporting).*

## 6. How to send a change

1. Fork the repo and create a branch.
2. Keep the change small and focused; add or update a test that fails without your change.
3. Run `python -m unittest discover -s tests` and `node --test tests/` — both must pass.
4. Follow the existing style: plain functions, no new heavy dependencies without a reason, calculations and validation in code (not left to the model).
5. Open a pull request describing what changed and how you checked it. Do not commit anything from `data/` (it holds personal chats and settings).

---

## 7. Licence and your contribution

The project is under the [PolyForm Noncommercial License 1.0.0](LICENSE). By opening a pull request you confirm that:

1. you wrote the contribution yourself, or you have the right to submit it (for example it is not copied from a source with an incompatible licence, and not written under an employer's rights);
2. it may be distributed under the PolyForm Noncommercial License 1.0.0 as part of this project; and
3. you grant the maintainer a perpetual, worldwide, non-exclusive, royalty-free right to also license your contribution under **other terms, including commercial licences**, so the project can be offered commercially to companies that ask.

You keep the copyright in what you wrote and may reuse it elsewhere. If you cannot agree to point 3, say so in the pull request and the maintainer will discuss it before merging.

*(Plain-language agreement, not legal advice. The maintainer should have it checked before relying on it commercially.)*

## 8. Credit

- Pull requests that are merged: you appear as a contributor on GitHub automatically.
- Reviews, testing and bug reports: you are named in the README's **Contributors and credits** section with a one-line description of what you did, e.g. "Security review of the plugin approval flow" — worded to match your actual work.
- If you co-write a change with the maintainer, the commit carries a `Co-authored-by:` line for you.
- If you prefer not to be named, say so.
