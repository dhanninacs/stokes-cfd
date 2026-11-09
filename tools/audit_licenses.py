"""Gate: every installed package's licence is on the allow-list (CLAUDE.md §1 rule 2).

Runs ``pip-licenses`` for the current interpreter and checks each entry
against ``tools/license_allowlist.toml``. Exit 1 lists the offenders.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OWN_PACKAGES = {"stokes-cfd", "stokes_cfd", "stokes-native", "stokes_native"}


def installed_licenses() -> list[dict[str, str]]:
    completed = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-system"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise SystemExit(f"pip-licenses failed: {completed.stderr.strip()}")
    data = json.loads(completed.stdout)
    return [
        {"name": str(d["Name"]), "version": str(d["Version"]), "license": str(d["License"])}
        for d in data
    ]


def audit(entries: list[dict[str, str]], config: dict[str, Any]) -> list[str]:  # Any: TOML
    allowed = [str(p).lower() for p in config["allowed"]["patterns"]]
    denied = [str(p).lower() for p in config["allowed"]["denied"]]
    explicit = {str(k).lower(): str(v) for k, v in dict(config.get("packages") or {}).items()}
    verified = {str(k).lower(): str(v) for k, v in dict(config.get("verified") or {}).items()}
    problems: list[str] = []
    for entry in entries:
        name = entry["name"].lower()
        if name in OWN_PACKAGES:
            continue
        licence = entry["license"]
        lowered = licence.lower()
        if name in explicit or name in verified:
            continue
        if any(d in lowered for d in denied):
            problems.append(
                f"{entry['name']} {entry['version']}: licence {licence!r} is denied "
                "(LGPL needs an explicit [packages] entry)"
            )
            continue
        if any(a in lowered for a in allowed):
            continue
        problems.append(
            f"{entry['name']} {entry['version']}: licence {licence!r} not on the allow-list; "
            "verify it and add a [verified] entry"
        )
    return problems


def main() -> int:
    config = tomllib.loads((ROOT / "tools" / "license_allowlist.toml").read_text(encoding="utf-8"))
    entries = installed_licenses()
    problems = audit(entries, config)
    if problems:
        print("licence audit: problems")
        for line in problems:
            print("  " + line)
        return 1
    print(f"licence audit: {len(entries)} packages clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
