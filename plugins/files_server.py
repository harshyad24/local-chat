"""Files plugin (MCP, stdio): READ-ONLY access to one folder. Environment: FILES_ROOT (the folder). Nothing outside it is reachable
(paths are resolved and checked, so ".." and links cannot escape), and there is no tool that writes, moves or deletes."""
from __future__ import annotations

import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("files")
READ_ONLY = ToolAnnotations(readOnlyHint=True)
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css", ".log", ".yaml", ".yml", ".ini", ".toml", ".xml", ".sql", ".tsv"}
MAX_BYTES = 2_000_000


def _root() -> Path:
    r = os.environ.get("FILES_ROOT", "")
    if not r or not Path(r).is_dir():
        raise RuntimeError("Files plugin is not configured: choose an existing folder in Plugins.")
    return Path(r).resolve()


def _inside(rel: str) -> Path:
    root = _root()
    p = (root / (rel or "").lstrip("/\\")).resolve()
    if p != root and root not in p.parents:
        raise PermissionError("That path is outside the shared folder.")
    return p


@mcp.tool(annotations=READ_ONLY)
def list_files(path: str = "") -> str:
    """List the files and folders inside the shared folder (or one of its subfolders)."""
    p = _inside(path)
    if not p.is_dir():
        return "Not a folder."
    rows = []
    for x in sorted(p.iterdir(), key=lambda a: (a.is_file(), a.name.lower()))[:200]:
        rows.append(f"{'[dir] ' if x.is_dir() else ''}{x.name}" + ("" if x.is_dir() else f"  ({x.stat().st_size:,} bytes)"))
    return "\n".join(rows) or "(empty)"


@mcp.tool(annotations=READ_ONLY)
def read_text_file(path: str, max_chars: int = 6000) -> str:
    """Read a text file (txt, md, csv, json, code…) from the shared folder."""
    p = _inside(path)
    if not p.is_file():
        return "File not found."
    if p.suffix.lower() not in TEXT_EXT:
        return f"Only text files can be read here ({p.suffix or 'no extension'} is not one)."
    if p.stat().st_size > MAX_BYTES:
        return "That file is too large to read here."
    return p.read_text(encoding="utf-8", errors="replace")[: max(200, min(int(max_chars), 20000))]


@mcp.tool(annotations=READ_ONLY)
def search_files(query: str, limit: int = 10) -> str:
    """Find files whose name or text matches the words of the query (any word counts; files matching more words come first).
    Returns the path and the best matching line of each file."""
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) >= 3]
    if not words:
        return "Give a word to search for."
    root, scored = _root(), []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")][:200]
        for f in fn:
            fp = Path(dp) / f
            rel = fp.relative_to(root).as_posix()
            name_hits = sum(w in f.lower() for w in words)
            best, best_n = "", 0
            if fp.suffix.lower() in TEXT_EXT and fp.stat().st_size <= MAX_BYTES:
                try:
                    for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
                        n = sum(w in line.lower() for w in words)
                        if n > best_n:
                            best, best_n = line.strip()[:160], n
                except OSError:
                    pass
            if name_hits or best_n:
                scored.append((name_hits * 2 + best_n, f"{rel}: {best}" if best else f"{rel}  (name match)"))
    scored.sort(key=lambda x: -x[0])
    return "\n".join(t for _, t in scored[: max(1, min(int(limit), 40))]) or "No matches."


if __name__ == "__main__":
    mcp.run()
