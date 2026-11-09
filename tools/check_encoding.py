"""Refuse text that was decoded with the wrong encoding and written back.

**This exists because one shipped.** A picture review found the Properties panel showing
``Source  â€"`` where an em dash belonged and ``0.5 Ã— 0.02`` where a multiplication sign did - the
signature of UTF-8 bytes read as Windows-1252 and re-encoded as UTF-8. The characters are not
"unusual text": they are a stable, mechanically detectable corruption, and a user reads them as a
broken product.

**The check is the corruption itself, not a list of characters.** A grep for ``Ã—`` and ``â€`` finds
the sequences that have been seen; this finds any run that round-trips -
``text.encode('cp1252').decode('utf-8')`` succeeds and changes the text, which is what the damage
IS. A file that merely contains ``Â`` in a comment about a unit does not round-trip, and is not
refused.

**A comment is not exempt.** Unlike the user-text gate, which is about what a user READS, this is
about the bytes in the repository: a mojibake comment is a file that some tool has already mangled,
and the next writer will copy the habit.

Run it directly, or through pytest - ``tests/test_text_encoding.py`` calls the same function, so the
two cannot disagree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: The extensions that hold text a human reads or a tool rewrites. A binary file cannot be mojibake.
TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".mjs",
        ".py",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

#: Where a mangled run is plausible. A leading byte of a multi-byte UTF-8 sequence read as Latin-1
#: always lands in this range, so requiring one of these before attempting the round-trip keeps the
#: scan from doing the work on every line of every file.
MARKERS = ("Â", "Ã", "Å", "Ð", "â", "ð")


def tracked_text_files(root: Path) -> list[Path]:
    """Every tracked file under ``root`` whose suffix says it holds text.

    **Tracked, not walked**: a build output, a generated bundle or an editor's cache is not ours to
    repair, and `web/dist` holds minified copies of the very strings we check - refusing those would
    make the gate fail for the tree's own artefacts.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    names = [name for name in listing.decode("utf-8", errors="replace").split("\0") if name]
    return [root / name for name in names if Path(name).suffix.lower() in TEXT_SUFFIXES]


def damage_in(text: str) -> list[tuple[int, str, str]]:
    """Every line that reverses to something different, as ``(line number, before, after)``.

    **Per LINE, not per file.** Reversing a whole file with a non-UTF-8 byte elsewhere would fail on
    the good text too, and a per-line answer is what a reader needs: the line number and the two
    spellings are a complete repair instruction.
    """
    found: list[tuple[int, str, str]] = []
    for line_no, line in enumerate(text.split("\n"), 1):
        if not any(marker in line for marker in MARKERS):
            continue
        try:
            repaired = line.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired != line:
            found.append((line_no, line, repaired))
    return found


def check(root: Path) -> list[str]:
    """One message per damaged line, empty when the tree is clean."""
    findings: list[str] = []
    for path in tracked_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, before, after in damage_in(text):
            rel = path.relative_to(root)
            findings.append(
                f"{rel}:{line_no}: text decoded with the wrong encoding\n"
                f"      is: {before.strip()[:80]}\n"
                f"    want: {after.strip()[:80]}"
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[1]).resolve() if argv and len(argv) > 1 else Path.cwd()
    findings = check(root)
    if findings:
        sys.stderr.write("encoding gate: text decoded with the wrong encoding found\n")
        for finding in findings:
            sys.stderr.write(f"  {finding}\n")
        sys.stderr.write(
            "\n  Repair by reversing the damage, never by deleting the characters:\n"
            "      text.encode('cp1252').decode('utf-8')\n"
            "  and write every file with encoding='utf-8', newline=''.\n"
        )
        return 1
    sys.stdout.write("encoding gate: clean\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
