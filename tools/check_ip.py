"""Gate: no employer identifiers, data or machine paths in code, tests, tools or fixtures
(CLAUDE.md §1 rule 3 and §8).

Terms come from the private ``tools/ip_blocklist.txt`` (one per line, ``#`` comments; never
committed) or, when it is absent, from ``tools/ip_blocklist.example.txt``. Terms are matched
case-insensitively.

Three generic patterns catch a user's home folder in its Windows, Linux and macOS spellings:
such a path names the person and the machine, whatever the blocklist holds. A placeholder
(a folder name in angle or curly brackets, or a shell variable) passes, and so does a relative
path that merely has a folder called ``home`` in it. A hit prints the kind of path, never the
path. This file is not scanned: the patterns are written out in it.

Documentation under ``docs/`` and the root markdown files are not scanned: they may describe
the rule.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("stokes", "agent", "tests", "tools", "native", "web/src", "web/DESIGN.md")
# Installed packages (`web/node_modules`) are third-party code, not the repository's, and are
# untracked; walking them takes over a minute and tests/test_gates.py gives the gate two.
SKIP_FILES = {"ip_blocklist.txt", "ip_blocklist.example.txt", "check_ip.py"}
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".npz", ".bin", ".stl", ".step"}
MAX_FILE_BYTES = 8 * 1024 * 1024

# A name must follow the folder: not a placeholder (<you>, {user}, $USER, %USERNAME%) and not
# the end of the text. Slashes may be doubled, as in a path inside a Python string.
USER_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a Windows user folder", re.compile(r"[a-z]:[\\/]+users[\\/]+[^\s<{$%\\/]", re.IGNORECASE)),
    ("a Linux home folder", re.compile(r"(?<![\w.:-])/home/+[^\s<{$%/]")),
    ("a macOS user folder", re.compile(r"(?<![\w.:-])/Users/+[^\s<{$%/]")),
)


def load_terms() -> list[str]:
    terms: list[str] = []
    private = ROOT / "tools" / "ip_blocklist.txt"
    source = private if private.exists() else ROOT / "tools" / "ip_blocklist.example.txt"
    for line in source.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip().lower()
        if text:
            terms.append(text)
    return terms


def line_problems(line: str, terms: list[str]) -> list[str]:
    """What is wrong with one line of text: blocked terms and machine paths."""
    lowered = line.lower()
    problems = [f"contains {term!r}" for term in terms if term in lowered]
    problems += [f"contains {kind}" for kind, pattern in USER_PATHS if pattern.search(line)]
    return problems


def scan(terms: list[str]) -> list[str]:
    hits: list[str] = []
    for directory in SCAN_DIRS:
        base = ROOT / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if (
                not path.is_file()
                or path.name in SKIP_FILES
                or path.suffix in SKIP_SUFFIXES
                or "__pycache__" in path.parts
                or "node_modules" in path.parts
                or path.stat().st_size > MAX_FILE_BYTES
            ):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, start=1):
                hits.extend(
                    f"{path.relative_to(ROOT)}:{number}: {problem}"
                    for problem in line_problems(line, terms)
                )
    return hits


#: A Stokes session token is 32 URL-safe characters after `token=`. **A live token in a file is a
#: credential**: it authenticates every request and the WebSocket, and anyone who can read the file
#: (a shared drive, a repository, a message) can drive the whole API - `run.start` included.
#: The only place one belongs is a private file the server writes at start, mode 600, under `$HOME`.
TOKEN_RE = re.compile(r"token=[A-Za-z0-9_\-]{20,}")

#: Where a token must never appear. Anything shipped or shared.
TOKEN_SCAN_DIRS = ("docs", "tools", "web", "stokes", "agent", "tests", "CHANGELOG.md", "README.md")

#: The one file allowed to hold one, by name: the server's own output, under `$HOME`, and this gate
#: cannot see it because it is outside the repository.
TOKEN_ALLOWED_SUFFIXES = (".pyc", ".png", ".zip")


def token_problems() -> list[str]:
    """Lines that carry something shaped like a session token.

    **Deliberately a shape, not a list of the tokens we have used.** A list would need editing every
    time the server restarts, and the token this catches is the one nobody remembered to add. A
    placeholder is written as `<...>` or `{...}` and does not match the character class.
    """
    found: list[str] = []
    root = Path(__file__).resolve().parent.parent
    for area in TOKEN_SCAN_DIRS:
        base = root / area
        paths = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for path in paths:
            if (
                not path.is_file()
                or path.suffix in TOKEN_ALLOWED_SUFFIXES
                or "node_modules" in path.parts
            ):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if TOKEN_RE.search(line):
                    where = f"{path.relative_to(root)}:{number}"
                    found.append(f"{where}: a session token in plain text")
    return found


def main() -> int:
    hits = scan(load_terms())
    tokens = token_problems()
    if hits or tokens:
        print("IP gate: blocked identifiers, machine paths or plain-text tokens found")
        for hit in hits + tokens:
            print("  " + hit)
        return 1
    print("ip gate: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
