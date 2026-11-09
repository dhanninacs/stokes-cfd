#!/usr/bin/env python3
"""Lint every dictionary in the OpenFOAM 14 tutorial corpus and keep a reproducible baseline.

The file set is **every text file** under each tutorial case's ``system/``, ``constant/``
(including a shipped ``constant/polyMesh/``, linted with ``polymesh=True``) and ``0*/``
folders under ``$FOAM_TUTORIALS``, sorted. Each file runs ``stokes_agent.lint.lint_file``.

The corpus baseline (``tests/fixtures/of14/corpus-baseline.json``) holds **relative paths and
findings only, never file content**, so a diff shows exactly which generated shapes newly fail
or stopped failing. ``--baseline`` with ``--write`` writes the baseline; without ``--write``
it compares against it and prints ``N files, M refused, +new, -lost``, exiting 1 on any new
refusal.

Two older ad-hoc numbers exist and do NOT match this tool's set, on purpose:

- ``1382 files, 158 refused`` came from a script that walked ``$FOAM_TUTORIALS`` and collected
  every text file including ``Allrun``/``Allclean`` helper scripts and mesh folders that were
  not under ``0*/`` or ``system/``, so it counted more files per case.
- ``444`` came from a later script that only linted the ``system/`` folder, so it counted far
  fewer. Neither set was committed, which is exactly why this tool exists.

Run twice and compare:

    cd $THINKY_REPO && PYTHONPATH=.:agent python tools/lint_corpus.py \
        --baseline tests/fixtures/of14/corpus-baseline.json

The second run must print ``+0, -0``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from stokes_agent.lint import lint_file, lint_text

#: Which findings this tool's own exit code cares about: any refusal in the corpus that was not
#: in the baseline is a regression and blocks the commit.
EXIT_NEW_REFUSALS = 1


def case_files(tutorials: Path) -> list[Path]:
    """Every text file this corpus tool lints, sorted, from every tutorial case.

    A case is a folder that holds ``system/``; its files come from ``system/``, ``constant/``
    and ``0*/`` only, so helper scripts and mesh folders outside those are not counted.
    """
    files: list[Path] = []
    if not tutorials.is_dir():
        return files
    for system in sorted(tutorials.rglob("system")):
        case = system.parent
        if not case.is_dir():
            continue
        for sub in ("system", "constant"):
            folder = case / sub
            if folder.is_dir():
                files.extend(sorted(folder.rglob("*")))
        for folder in sorted(case.glob("0*")):
            if folder.is_dir():
                files.extend(sorted(folder.rglob("*")))
    return sorted({f for f in files if f.is_file() and not f.is_symlink()})


def relative(path: Path, tutorials: Path) -> str:
    """The path inside the corpus, relative to ``$FOAM_TUTORIALS``."""
    return path.relative_to(tutorials).as_posix()


def findings_of(tutorials: Path) -> dict[str, list[dict[str, str]]]:
    """``{relative path: [{token, why}]}`` for every file that carries at least one refusal.

    **Clean files are omitted** from the result: the baseline records what was refused, not
    what was checked, so the diff sees only the interesting deltas.
    """
    refused: dict[str, list[dict[str, str]]] = {}
    for path in case_files(tutorials):
        rel = relative(path, tutorials)
        # **`lint_text` with `polymesh=True` for a shipped `constant/polyMesh/` file**: the
        # polyMesh flavour is a keyword of `lint_text` (`lint_file` has no such argument), and the
        # brief requires it. Every other file goes through `lint_file`, which handles binary and
        # size limits on its own.
        if "polyMesh" in path.parts:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            findings = lint_text(rel, text, polymesh=True)
        else:
            findings = lint_file(rel, path)
        if findings:
            refused[rel] = [{"token": f.token, "why": f.why} for f in findings]
    return refused


def load_baseline(path: Path) -> dict[str, list[dict[str, str]]]:
    loaded: dict[str, list[dict[str, str]]] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def diff(
    old: dict[str, list[dict[str, str]]], new: dict[str, list[dict[str, str]]]
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    """The findings that appeared (new) and the ones that disappeared (lost) between two sets.

    A finding is the same only when its file, token and why all match exactly; a wording change
    in ``why`` counts as a new refusal plus a lost one, which is the honest answer for a
    regression review.
    """
    new_set = {(file, item["token"], item["why"]) for file, items in new.items() for item in items}
    old_set = {(file, item["token"], item["why"]) for file, items in old.items() for item in items}
    appeared = new_set - old_set
    vanished = old_set - new_set

    def group(triples: set[tuple[str, str, str]]) -> dict[str, list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for file, token, why in sorted(triples):
            grouped.setdefault(file, []).append({"token": token, "why": why})
        return grouped

    return group(appeared), group(vanished)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tutorials",
        default=os.environ.get("FOAM_TUTORIALS", ""),
        help="The OpenFOAM tutorials folder; defaults to $FOAM_TUTORIALS.",
    )
    parser.add_argument(
        "--baseline",
        default="tests/fixtures/of14/corpus-baseline.json",
        help="The corpus baseline file to write (--write) or compare against.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the current findings to the baseline instead of comparing.",
    )
    args = parser.parse_args(argv)
    tutorials = Path(args.tutorials)
    if not args.tutorials or not tutorials.is_dir():
        parser.error("pass --tutorials or set $FOAM_TUTORIALS to a real tutorials folder")
    results = findings_of(tutorials)
    total = len(case_files(tutorials))
    refused = sum(len(items) for items in results.values())

    if args.write:
        target = Path(args.baseline)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {total} files, {refused} refused -> {target}")
        return 0

    baseline = load_baseline(Path(args.baseline))
    new, lost = diff(baseline, results)
    print(
        f"{total} files, {refused} refused, +{sum(map(len, new.values()))}, "
        f"-{sum(map(len, lost.values()))}"
    )
    for file, items in new.items():
        for item in items:
            print(f"NEW {file}: {item['token']} - {item['why']}")
    for file, items in lost.items():
        for item in items:
            print(f"LOST {file}: {item['token']} - {item['why']}")
    return EXIT_NEW_REFUSALS if new else 0


if __name__ == "__main__":
    sys.exit(main())
