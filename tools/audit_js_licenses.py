"""Gate: JavaScript dependencies in web/node_modules are on the licence allow-list.

Reads each installed package's ``package.json`` (direct and transitive) and
applies the same patterns as the Python audit. Skips when ``web/node_modules``
is absent (no front-end build on this machine).
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "web" / "node_modules"


def licence_of(package_json: Path) -> str:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "UNREADABLE"
    licence = data.get("license") or data.get("licenses")
    if isinstance(licence, dict):
        licence = licence.get("type")
    if isinstance(licence, list):
        licence = " OR ".join(
            str(entry.get("type", entry) if isinstance(entry, dict) else entry) for entry in licence
        )
    return str(licence or "UNKNOWN")


def package_name(package_json: Path) -> str | None:
    """The package name when ``package_json`` is a package root, else None.

    A package root sits directly under a ``node_modules`` directory as
    ``<name>/package.json`` or ``@scope/<name>/package.json``; manifests deeper
    inside a package (sub-path exports, examples) are not packages.
    """
    parts = package_json.relative_to(MODULES).parts[:-1]
    anchors = [i for i, part in enumerate(parts) if part == "node_modules"]
    tail = parts[anchors[-1] + 1 :] if anchors else parts
    if len(tail) == 1 and not tail[0].startswith("@"):
        return tail[0]
    if len(tail) == 2 and tail[0].startswith("@"):
        return "/".join(tail)
    return None


def main() -> int:
    if not MODULES.is_dir():
        print("js licence audit: web/node_modules absent, skipped")
        return 0
    config = tomllib.loads((ROOT / "tools" / "license_allowlist.toml").read_text(encoding="utf-8"))
    allowed = [str(p).lower() for p in config["allowed"]["patterns"]]
    denied = [str(p).lower() for p in config["allowed"]["denied"]]
    verified = {str(k).lower(): str(v) for k, v in dict(config.get("verified_js") or {}).items()}
    problems: list[str] = []
    count = 0
    for package_json in sorted(MODULES.rglob("package.json")):
        name = package_name(package_json)
        if name is None:
            continue
        count += 1
        if name.lower() in verified:
            continue
        licence = licence_of(package_json)
        lowered = licence.lower()
        if any(d in lowered for d in denied) and "lgpl" not in lowered:
            problems.append(f"{name}: licence {licence!r} is denied")
        elif not any(a in lowered for a in allowed):
            problems.append(
                f"{name}: licence {licence!r} not on the allow-list; "
                "verify and add to [verified_js]"
            )
    if problems:
        print("js licence audit: problems")
        for line in problems:
            print("  " + line)
        return 1
    if count == 0:
        # An installed tree with no packages audits nothing, which is not a pass: it is the shape
        # an emptied node_modules takes (a recursive delete following a worktree junction did this
        # once), and it would let the gate go green on a tree with no dependencies at all.
        print(f"js licence audit: no packages found under {MODULES}; expected an installed tree")
        return 1
    print(f"js licence audit: {count} packages clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
