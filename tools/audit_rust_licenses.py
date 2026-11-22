"""Gate: every Rust crate linked into a shipped program is on the allow-list (ADR-0035 decision 33).

Reads ``cargo metadata --locked`` for each shipped Rust program (today ``native/implicit-mesh``)
for each shipped platform (:data:`PLATFORMS`), and walks the resolved graph from the program
through its normal dependencies only: development
dependencies are not shipped, and procedural-macro crates (and what only they need) run at build
time and are not linked. Each crate's SPDX expression is checked against
``tools/license_allowlist.toml``, the same allow-list ``audit_licenses.py`` uses: an ``OR`` passes
when one side is allowed, an ``AND`` only when every side is, ``WITH`` keeps its licence's
verdict, and a crate with no licence fails unless ``[verified_rust]`` names it.

Without ``cargo`` on the PATH it says so and exits 0 (a developer machine without Rust); CI passes
``--require``, which makes a missing ``cargo`` a failure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = (ROOT / "native" / "implicit-mesh" / "Cargo.toml",)
OWN_LICENCE = "LicenseRef-Stokes-Proprietary"
PLATFORMS = ("x86_64-unknown-linux-gnu", "x86_64-pc-windows-gnullvm")
"""The targets the programs ship for: the graph is resolved per platform, so a crate that only a
WebAssembly or another platform's build would link is not audited."""

_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+\-]+")


class SpdxError(ValueError):
    pass


def _tokens(expression: str) -> list[str]:
    text = expression.replace("/", " OR ")  # the old crates.io form, "MIT/Apache-2.0"
    found = _TOKEN.findall(text)
    if "".join(found) != re.sub(r"\s+", "", text):
        raise SpdxError(f"cannot read the licence expression {expression!r}")
    return found


def allowed_id(identifier: str, allowed: Iterable[str], denied: Iterable[str]) -> bool:
    """One licence identifier against the allow-list's patterns (case-insensitive substrings)."""
    lowered = identifier.lower()
    if lowered == OWN_LICENCE.lower():
        return True
    if any(d.lower() in lowered for d in denied):
        return False
    return any(a.lower() in lowered for a in allowed)


def evaluate(expression: str, allowed: Iterable[str], denied: Iterable[str]) -> bool:
    """Whether an SPDX expression is acceptable under the allow-list."""
    tokens = _tokens(expression)
    allow, deny = list(allowed), list(denied)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise SpdxError(f"the licence expression {expression!r} ends early")
        token = tokens[position]
        position += 1
        return token

    def factor() -> bool:
        piece = take()
        if piece == "(":
            value = either()
            if take() != ")":
                raise SpdxError(f"unbalanced parentheses in {expression!r}")
            return value
        if piece.upper() in ("AND", "OR", "WITH", ")"):
            raise SpdxError(f"unexpected {piece!r} in {expression!r}")
        value = allowed_id(piece, allow, deny)
        if (peek() or "").upper() == "WITH":
            take()
            take()  # an exception only widens what the licence permits
        return value

    def both() -> bool:
        value = factor()
        while (peek() or "").upper() == "AND":
            take()
            value = factor() and value
        return value

    def either() -> bool:
        value = both()
        while (peek() or "").upper() == "OR":
            take()
            value = both() or value
        return value

    result = either()
    if position != len(tokens):
        raise SpdxError(f"trailing text in the licence expression {expression!r}")
    return result


def shipped(metadata: dict[str, Any]) -> list[dict[str, Any]]:  # Any: cargo metadata JSON
    """The packages linked into the workspace's programs: reachable from the workspace members
    through normal dependencies, never entering a procedural-macro crate."""
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}

    def is_macro(package: dict[str, Any]) -> bool:  # Any: JSON
        return any("proc-macro" in t.get("kind", []) for t in package.get("targets", []))

    seen: set[str] = set()
    frontier = list(metadata.get("workspace_members", []))
    while frontier:
        ident = frontier.pop()
        if ident in seen:
            continue
        seen.add(ident)
        for dep in nodes.get(ident, {}).get("deps", []):
            kinds = [k.get("kind") for k in dep.get("dep_kinds", [{"kind": None}])]
            if None not in kinds:  # only "dev" or "build" edges: not linked
                continue
            target = packages.get(dep["pkg"])
            if target is None or is_macro(target):
                continue
            frontier.append(dep["pkg"])
    return [packages[i] for i in sorted(seen) if i in packages]


def audit(metadata: dict[str, Any], config: dict[str, Any]) -> list[str]:  # Any: JSON, TOML
    allowed = [str(p) for p in config["allowed"]["patterns"]]
    denied = [str(p) for p in config["allowed"]["denied"]]
    verified = {str(k).lower() for k in dict(config.get("verified_rust") or {})}
    problems: list[str] = []
    for package in shipped(metadata):
        name, version = str(package["name"]), str(package["version"])
        expression = package.get("license")
        if name.lower() in verified:
            continue
        if not expression:
            problems.append(f"{name} {version}: no licence expression; add it to [verified_rust]")
            continue
        try:
            ok = evaluate(str(expression), allowed, denied)
        except SpdxError as exc:
            problems.append(f"{name} {version}: {exc}")
            continue
        if not ok:
            problems.append(f"{name} {version}: licence {expression!r} is not on the allow-list")
    return problems


def cargo_metadata(manifest: Path, platform: str) -> dict[str, Any]:  # Any: JSON
    cargo = shutil.which("cargo")
    if cargo is None:
        raise FileNotFoundError("cargo")
    completed = subprocess.run(
        [
            cargo,
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--filter-platform",
            platform,
            "--manifest-path",
            str(manifest),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        raise SystemExit(f"cargo metadata failed for {manifest}: {completed.stderr.strip()[:2000]}")
    data: dict[str, Any] = json.loads(completed.stdout)
    return data


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", action="store_true", help="fail when cargo is missing")
    args = parser.parse_args(argv)
    config = tomllib.loads((ROOT / "tools" / "license_allowlist.toml").read_text(encoding="utf-8"))
    problems: list[str] = []
    checked = 0
    for manifest in PROGRAMS:
        if not manifest.is_file():
            continue
        for platform in PLATFORMS:
            try:
                metadata = cargo_metadata(manifest, platform)
            except FileNotFoundError:
                if args.require:
                    print("rust licence audit: cargo is not on the PATH")
                    return 1
                print("rust licence audit: skipped, cargo is not on the PATH (CI runs it)")
                return 0
            problems.extend(p for p in audit(metadata, config) if p not in problems)
            checked = max(checked, len(shipped(metadata)))
    if problems:
        print("rust licence audit: failed")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"rust licence audit: clean ({checked} crates)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
