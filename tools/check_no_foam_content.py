"""Gate: no OpenFOAM tutorial or case content in what Stokes ships (ADR-0029 decision 2, T-318).

OpenFOAM's tutorials are GPL-3. Stokes reads them from the user's own install at run time and
never ships one; this gate makes that a check rather than a memory. It fails on any file under
the shipped trees (`stokes/`, `agent/`, `web/` without its build output, `native/`) that carries:

* **the OpenFOAM banner** (the `\\\\    /   F ield` ASCII art every OpenFOAM file starts with), in
  any file at all; or
* **a `FoamFile` header** at the start of a line, in a file that is not source code. Stokes'
  printers write such headers from code (`stokes/compile/foam.py`), so source files are not checked
  for the word; a dictionary copied into the tree has no source suffix, and that is what is caught.

The tests' own dictionaries are synthetic and live outside these trees. `tests/fixtures/of14/` is
outside them too; whether it stays in the repository is the owner's open decision (E4 in
`docs/00-open-questions.md`). A hit prints the file and the kind of content, never the content.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("stokes", "agent", "web", "native")
#: Build output, dependencies and caches: not ours to ship as source, and large.
SKIP_DIRS = frozenset({"node_modules", "dist", "dist.bak", "__pycache__", ".vite", "build"})
#: Source and text the printers and the docs legitimately put the word `FoamFile` in.
SOURCE_SUFFIXES = frozenset(
    {".py", ".pyi", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".css", ".md", ".html", ".json", ".toml"}
)
#: Binary content the patterns cannot meaningfully match.
SKIP_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".pyc", ".npz", ".step"}
)
MAX_FILE_BYTES = 8 * 1024 * 1024

BANNER = re.compile(r"\\\\\s+/\s+F\s*ield")
HEADER = re.compile(r"(?m)^[ \t]*FoamFile[ \t]*(?:\r?\n[ \t]*)?\{")


def problems_in(path: Path, text: str) -> list[str]:
    """What OpenFOAM content one file carries: the banner, a dictionary header, or nothing."""
    found: list[str] = []
    if BANNER.search(text):
        found.append("the OpenFOAM banner")
    if path.suffix.lower() not in SOURCE_SUFFIXES and HEADER.search(text):
        found.append("an OpenFOAM dictionary header (FoamFile)")
    return found


def files_under(root: Path, dirs: Iterable[str] = SCAN_DIRS) -> Iterable[Path]:
    """Every regular file under the shipped trees, without build output or links."""
    for name in dirs:
        top = root / name
        if not top.is_dir():
            continue
        stack = [top]
        while stack:
            folder = stack.pop()
            for entry in sorted(folder.iterdir()):
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in SKIP_DIRS:
                        stack.append(entry)
                elif entry.is_file() and entry.suffix.lower() not in SKIP_SUFFIXES:
                    yield entry


def scan(root: Path = ROOT, dirs: Iterable[str] = SCAN_DIRS) -> list[str]:
    """One line per offending file: its path relative to ``root`` and what it carries."""
    hits: list[str] = []
    for path in files_under(root, dirs):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_bytes().decode("latin-1")
        except OSError:
            continue
        for problem in problems_in(path, text):
            hits.append(f"{path.relative_to(root).as_posix()}: {problem}")
    return hits


def main() -> int:
    hits = scan()
    for hit in hits:
        print(hit)
    if hits:
        print(f"foam content: {len(hits)} file(s) carry OpenFOAM content; see ADR-0029 decision 2")
        return 1
    print("foam content: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
