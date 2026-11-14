"""/create - produce a real deliverable as downloadable files: Word documents, PowerPoint decks, code projects (zip, syntax-checked),
dashboards (from an attached dataset, computed by the analysis code) and creative text. The model supplies structure and wording as
validated JSON; files are built by code (python-docx, python-pptx, zipfile)."""
from __future__ import annotations

import ast
import io
import json
import re
import zipfile
from pathlib import Path

from .. import common
from ..registry import Slot, Workflow, register
from ... import dashboards
from . import analyze as analyze_wf

KINDS = ["document", "presentation", "dashboard", "code", "creative"]
KIND_RX = [
    ("document", re.compile(r"\b(?:proposal|report|docx|word document|memo|whitepaper|white paper|policy|resume|cv|case study|manual|sop|contract|business plan|brief)\b", re.I)),
    ("presentation", re.compile(r"\b(?:powerpoint|pptx|slides?|slide deck|deck|presentation)\b", re.I)),
    ("dashboard", re.compile(r"\b(?:dashboards?|kpi|scorecard)\b", re.I)),
    ("code", re.compile(r"\b(?:code|script|program|application|app|website|web page|webpage|api|bot|function|class|game|cli|tool)\b|\bpython\b|\bjavascript\b|\bhtml\b", re.I)),
    ("creative", re.compile(r"\b(?:story|poem|song|lyrics|blog(?: post)?|post|caption|speech|toast|email|letter|essay|script for|slogan|tagline|joke)\b", re.I)),
    ("document", re.compile(r"\b(?:report|proposal|document|docx|word|memo|whitepaper|white paper|policy|plan|resume|cv|brief|summary|case study|manual|guide|sop|contract)\b", re.I)),
]


def detect_kind(text: str) -> str:
    for k, rx in KIND_RX:
        if rx.search(text or ""):
            return k
    return ""


def prefill(text: str, ctx: dict) -> dict:
    out = {"topic": text.strip()}
    k = detect_kind(text)
    if k:
        out["kind"] = k
    f = analyze_wf.pick_file([x for x in ctx.get("files") or [] if x.get("ext") in analyze_wf.TABLE_EXT])
    if f:
        out["file"] = f
    m = re.search(r"\b(\d{1,2})[- ]?(?:slides?|pages?)\b", text, re.I)
    if m:
        out["size"] = m.group(1)
    return out


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:40] or "output"


def _list(v) -> list:
    return v if isinstance(v, list) else []


# ------------------------------------------------------------------ documents
async def make_document(rc, topic: str, audience: str, detail: str) -> dict:
    n_sec = {"short": 4, "detailed": 8}.get(detail, 6)
    await rc.progress(1, n_sec + 3, "Planning the structure")
    data = await rc.json_call(
        f'Plan a professional document. Topic/request: "{topic}". Audience: {audience}. Reply with JSON only: {{"title": "", "sections": [{{"heading": "", "points": ["what this section must cover", "..."]}}]}} '
        f"with exactly {n_sec} sections in a logical order (start with a summary for reports/proposals).", max_tokens=900)
    secs = [x for x in _list(data.get("sections") if isinstance(data, dict) else None) if isinstance(x, dict) and str(x.get("heading", "")).strip()][:n_sec]
    if len(secs) < 3:
        secs = [{"heading": h, "points": []} for h in ("Summary", "Background", "Details", "Recommendations", "Next steps")[:n_sec]]
        rc.notes.append("The model did not return a usable outline, so a standard structure was used.")
    title = str((data or {}).get("title") or topic)[:120] if isinstance(data, dict) else topic[:120]
    out = []
    for i, sec in enumerate(secs):
        await rc.progress(i + 2, n_sec + 3, f"Writing: {sec['heading']}")
        words = {"short": 110, "detailed": 260}.get(detail, 170)
        body = await rc.text_call(
            f'Write the section "{sec["heading"]}" of a document titled "{title}" for {audience}. Cover: {"; ".join(map(str, _list(sec.get("points")))) or "what a reader needs here"}. '
            f"About {words} words, plain professional prose in 1-3 paragraphs, no heading, no markdown. Do NOT invent statistics, quotes, names, dates or prices: where a real "
            "figure is needed write a placeholder like [add figure].", max_tokens=int(words * 2.2))
        paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()] or ["[Content could not be generated for this section.]"]
        out.append({"heading": str(sec["heading"]), "paragraphs": paras})
    await rc.progress(n_sec + 2, n_sec + 3, "Building the Word file")
    files = [rc.out_file(f"{_slug(title)}.docx", common.build_docx(title, out, f"Prepared for: {audience}")),
             rc.out_file(f"{_slug(title)}.md", common.md_from_sections(title, out))]
    placeholders = sum(p.count("[add") for s in out for p in s["paragraphs"])
    notes = ["Edit the downloaded .docx freely; it is a normal Word file."]
    if placeholders:
        notes.append(f"{placeholders} placeholder(s) like [add figure] mark facts only you can supply.")
    return {"blocks": [{"type": "overview", "title": title, "text": f"A {len(out)}-section document for {audience}.",
                        "kpis": [{"label": "Sections", "value": len(out)}, {"label": "Words", "value": sum(len(p.split()) for s in out for p in s["paragraphs"])}]},
                       {"type": "markdown", "title": "Preview", "text": common.md_from_sections(title, out)}],
            "files": files, "notes": notes, "facts": [f"Created document “{title}” with sections: " + ", ".join(s["heading"] for s in out) + f". Audience: {audience}."],
            "what": "a Word document and Markdown copy", "size": "2-4 sentences: what the document contains and what the user should fill in or check"}


# ------------------------------------------------------------------ presentations
async def make_presentation(rc, topic: str, audience: str, size: str) -> dict:
    try:
        n = max(5, min(int(float(size)), 20)) if size else 9
    except ValueError:
        n = 9
    await rc.progress(1, 3, "Outlining the slides")
    data = await rc.json_call(
        f'Create a slide deck outline. Topic/request: "{topic}". Audience: {audience}. Reply with JSON only: {{"title": "", "subtitle": "", "slides": [{{"title": "short slide title", '
        f'"bullets": ["3 to 5 short bullets, max 12 words each"], "notes": "what the presenter says, 2 sentences"}}]}} with {n} content slides: an agenda or problem first, then the '
        "main points, and a closing slide with takeaways or next steps. Do not invent statistics, quotes or company names.", max_tokens=2200)
    slides = []
    for s in _list(data.get("slides") if isinstance(data, dict) else None):
        if isinstance(s, dict) and str(s.get("title", "")).strip():
            slides.append({"title": str(s["title"]).strip()[:90], "bullets": [str(b).strip()[:150] for b in _list(s.get("bullets")) if str(b).strip()][:6], "notes": str(s.get("notes") or "")[:600]})
    if len(slides) < 3:
        return {"error": "The model did not produce a usable slide outline. Try again, or give a more specific topic."}
    title = str(data.get("title") or topic)[:100]
    await rc.progress(2, 3, "Building the PowerPoint file")
    blob = common.build_pptx(title, slides[:22], str(data.get("subtitle") or f"For {audience}")[:120])
    files = [rc.out_file(f"{_slug(title)}.pptx", blob)]
    await rc.progress(3, 3, "Done")
    return {"blocks": [{"type": "overview", "title": title, "text": f"{len(slides) + 1} slides (a title slide and {len(slides)} content slides) with speaker notes.",
                        "kpis": [{"label": "Slides", "value": len(slides) + 1}]},
                       {"type": "table", "title": "Slide outline", "columns": ["#", "Title", "Key points"], "rows": [[i + 2, s["title"], " · ".join(s["bullets"][:3])] for i, s in enumerate(slides)]}],
            "files": files, "notes": ["Open the .pptx in PowerPoint, Keynote or Google Slides to change layout and add images or charts. Speaker notes are included."],
            "facts": [f"Created a {len(slides) + 1}-slide deck “{title}” for {audience}. Slides: " + "; ".join(s["title"] for s in slides)],
            "what": "a PowerPoint deck with speaker notes", "size": "2-4 sentences: what the deck covers and what to customise (images, real figures)"}


# ------------------------------------------------------------------ code
def check_python(files: list[dict]) -> list[str]:
    errs = []
    for f in files:
        if f["path"].endswith(".py"):
            try:
                ast.parse(f["content"])
            except SyntaxError as exc:
                errs.append(f"{f['path']}: line {exc.lineno}: {exc.msg}")
        elif f["path"].endswith(".json"):
            try:
                json.loads(f["content"])
            except ValueError as exc:
                errs.append(f"{f['path']}: invalid JSON ({exc})")
    return errs


def _clean_files(data) -> list[dict]:
    out = []
    for f in _list(data.get("files") if isinstance(data, dict) else None)[:8]:
        if isinstance(f, dict) and isinstance(f.get("content"), str) and f["content"].strip():
            path = re.sub(r"[^\w./\-]+", "_", str(f.get("path") or "main.py")).strip("/.")[:60]
            if path and ".." not in path:
                out.append({"path": path, "content": f["content"]})
    return out


async def make_code(rc, topic: str, language: str, reqs: str) -> dict:
    await rc.progress(1, 4, "Designing the program")
    prompt = (f'Write a complete, working {language} program. Request: "{topic}". Requirements: {reqs or "as described"}. Reply with JSON only: '
              '{"project": "short_name", "files": [{"path": "main.py", "content": "full source"}], "run": "exact command(s) to install and run it", "notes": "one sentence"} '
              "Use at most 5 files, standard library where possible, include clear comments and basic input validation, and a README.md. No placeholders or TODOs.")
    data = await rc.json_call(prompt, max_tokens=3500)
    files = _clean_files(data)
    if not files:
        return {"error": "The model did not return usable code files. Try again with more detail about what the program must do."}
    await rc.progress(2, 4, "Checking the code")
    errs = check_python(files)
    if errs:
        await rc.progress(3, 4, "Fixing syntax errors")
        data2 = await rc.json_call(prompt + "\n\nYour previous answer had these syntax errors, fix them and return the full JSON again:\n" + "\n".join(errs[:6]), max_tokens=3500)
        files2 = _clean_files(data2)
        if files2 and len(check_python(files2)) < len(errs):
            files, data, errs = files2, data2, check_python(files2)
    project = _slug(str((data or {}).get("project") or topic)) if isinstance(data, dict) else _slug(topic)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr(f"{project}/{f['path']}", f["content"])
    out_files = [rc.out_file(f"{project}.zip", buf.getvalue())]
    main = next((f for f in files if f["path"].lower() in ("main.py", "app.py", "index.html", "main.js")), files[0])
    run_cmd = str((data or {}).get("run") or "").strip() if isinstance(data, dict) else ""
    py = [f for f in files if f["path"].endswith(".py")]
    notes = []
    if errs:
        notes.append("Syntax problems remain (the model could not fix them): " + "; ".join(errs[:3]))
    elif py:
        notes.append(f"All {len(py)} Python file(s) pass a syntax check. The program itself was not run, so test it before relying on it.")
    else:
        notes.append("The code was not executed or tested.")
    lang = Path(main["path"]).suffix.lstrip(".") or ""
    preview = f"**{main['path']}**\n\n```{lang}\n" + "\n".join(main["content"].splitlines()[:140]) + "\n```" + (f"\n\n**Run it:**\n\n```\n{run_cmd}\n```" if run_cmd else "")
    await rc.progress(4, 4, "Done")
    return {"blocks": [{"type": "overview", "title": project, "text": str((data or {}).get("notes") or f"{language} project with {len(files)} file(s)."),
                        "kpis": [{"label": "Files", "value": len(files)}, {"label": "Lines", "value": sum(len(f["content"].splitlines()) for f in files)}]},
                       {"type": "markdown", "title": "Main file", "text": preview}],
            "files": out_files, "notes": notes, "facts": [f"Created {language} project {project} with files: " + ", ".join(f["path"] for f in files) + ".", f"How to run: {run_cmd or 'see README'}"] +
            [f"Syntax check: {'passed' if not errs else 'FAILED: ' + '; '.join(errs[:3])}"],
            "what": "a code project (zip) with a syntax check", "size": "3-5 sentences: what it does, how to run it, and that it was only syntax-checked, not run"}


# ------------------------------------------------------------------ dashboards
async def make_dashboard(rc, topic: str, f: dict) -> dict:
    if f["ext"] not in analyze_wf.TABLE_EXT:
        return {"error": "A dashboard needs a table: attach a CSV or Excel file."}
    res = await analyze_wf.tabular(rc, f["path"], f["name"], topic)
    try:
        spec, summary, warnings = analyze_wf.build_dashboard(res, topic, f["name"])
    except dashboards.DashboardError as exc:
        return {"error": f"Not enough usable KPIs and charts for a dashboard: {exc}"}
    blocks = [{"type": "dashboard", "spec": spec}] + [b for b in res["blocks"] if b["type"] == "findings"][:1]
    res["files"] += [rc.out_file(f"{_slug(spec['title'])}.json", json.dumps(spec, ensure_ascii=False, indent=1))]
    return {"blocks": blocks, "files": res["files"], "notes": res["notes"] + ([f"Skipped: {w}" for w in warnings] if warnings else []) + ["Use the PNG / Copy / CSV buttons on the dashboard to export it."],
            "facts": [f"Built dashboard “{spec['title']}” ({summary}) from {f['name']}."] + res["facts"][:14],
            "what": "an interactive dashboard (exportable as PNG/CSV)", "size": "3-5 sentences of insight: what stands out and what to watch"}


# ------------------------------------------------------------------ creative text
async def make_creative(rc, topic: str, audience: str, detail: str) -> dict:
    await rc.progress(1, 2, "Writing")
    words = {"short": 150, "detailed": 700}.get(detail, 350)
    text = await rc.text_call(f'{topic}\n\nAudience/tone: {audience}. About {words} words. Output only the finished piece, with a title on the first line if it suits the form.', max_tokens=int(words * 2.5))
    if len(text) < 20:
        return {"error": "The model returned no text. Try again."}
    title = text.splitlines()[0].strip("# *")[:80] or topic[:60]
    files = [rc.out_file(f"{_slug(title)}.docx", common.build_docx(title, [{"paragraphs": [p for p in re.split(r"\n{2,}", text) if p.strip()]}])), rc.out_file(f"{_slug(title)}.md", text)]
    return {"blocks": [{"type": "markdown", "title": title, "text": text}], "files": files, "notes": [], "facts": [f"Wrote a piece titled “{title}” ({len(text.split())} words)."],
            "what": "the written piece and a Word/Markdown copy", "size": "1-2 sentences: offer to adjust tone or length"}


async def run(rc) -> dict:
    s = rc.slots
    kind = str(s.get("kind") or "document")
    topic = str(s.get("topic") or "")
    audience = "a general business audience" if s.get("audience") in (None, "flexible", "") else str(s["audience"])
    detail = str(s.get("detail") or ("short" if rc.pref("style.detail") == "brief" else "detailed" if rc.pref("style.detail") == "detailed" else "standard"))
    if kind == "presentation":
        res = await make_presentation(rc, topic, audience, str(s.get("size") or ""))
    elif kind == "code":
        res = await make_code(rc, topic, str(s.get("language") if s.get("language") not in (None, "flexible") else "Python"), "" if s.get("requirements") in (None, "flexible") else str(s["requirements"]))
    elif kind == "dashboard":
        f = s.get("file")
        res = await make_dashboard(rc, topic, f) if isinstance(f, dict) else {"error": "A dashboard needs a data file: attach a CSV or Excel file."}
    elif kind == "creative":
        res = await make_creative(rc, topic, audience, detail)
    else:
        res = await make_document(rc, topic, audience, detail)
    res.setdefault("summary", f"Created {kind}")
    return res


register(Workflow(
    id="create", skill="create", title="Create a deliverable", description="Documents, presentations, dashboards, code and creative writing as downloadable files",
    keywords=re.compile(r"creat|generat|build|write|make|design|draft", re.I), general=True, prefill=prefill, run=run,
    slots=[Slot("kind", "Deliverable", "What should I create?", kind="choice", choices=["document", "presentation", "dashboard", "code", "creative"]),
           Slot("topic", "Request", "", required=False),
           Slot("audience", "Audience", "Who is it for? (so I can pitch the tone and detail)", ask_if=lambda s: s.get("kind") in ("document", "presentation", "creative"),
                hint="e.g. executives, students, a client, my manager"),
           Slot("requirements", "Requirements", "What must the program do? List the main features.", ask_if=lambda s: s.get("kind") == "code" and len(str(s.get("topic") or "").split()) < 12),
           Slot("language", "Language", "", required=False, default="Python"),
           Slot("detail", "Length", "", kind="choice", choices=["short", "standard", "detailed"], required=False),
           Slot("size", "Size", "", required=False),
           Slot("file", "Data file", "Attach the CSV or Excel file with the data for the dashboard (+ button) and send it.", kind="file", ask_if=lambda s: s.get("kind") == "dashboard")]))
