"""Workspace presets: the shipped ones and the user's own (ADR-0015 §3).

A preset file is a workspace document with a ``preset`` object that holds a
``title`` and a one-line ``description``. The shipped presets live next to this
module in ``presets/`` and carry the four arrangement sections (layout,
islands, topbar, tree), so applying one rearranges the interface and leaves the
theme, shortcuts, viewport and engine choices alone. A preset the user saves
holds the whole effective workspace and lives in
``~/.stokes/presets/<name>.json``. Applying a preset replaces, in the target
scope, every section the preset carries.

Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from stokes.workspace.errors import UnknownPreset, WorkspaceInvalid
from stokes.workspace.files import read_file, write_file
from stokes.workspace.merge import PRESETS_DIR
from stokes.workspace.paths import JSON
from stokes.workspace.rules import Checker, Finding
from stokes.workspace.schema import (
    METADATA,
    PRESET_META,
    SCHEMA_VERSION,
    USER_ONLY,
    sanitise,
)

SHIPPED = ("stokes", "classic", "ribbon", "workflow", "minimal")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_USER_PRESETS = 200

Source = Literal["shipped", "user"]


@dataclass(frozen=True)
class Preset:
    name: str
    title: str
    description: str
    source: Source
    path: Path
    document: dict[str, JSON]
    """The cleaned sections, without ``version`` or ``preset``."""
    findings: tuple[Finding, ...]

    def summary(self) -> dict[str, JSON]:
        data: dict[str, JSON] = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "sections": list(self.document),
        }
        if self.source == "user":
            data["path"] = str(self.path)
        if self.findings:
            data["warnings"] = [finding.to_dict() for finding in self.findings]
        return data


def shipped_dir() -> Path:
    return Path(__file__).with_name("presets")


def user_dir(home: Path) -> Path:
    return home / PRESETS_DIR


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise WorkspaceInvalid(
            f"preset name {name!r}: use 1 to 64 lower-case letters, digits, _ or -, "
            "starting with a letter or a digit",
            details={"name": name},
        )
    return name


def load(path: Path, name: str, source: Source) -> Preset:
    """Read one preset file; problems become findings, as for any workspace file."""
    scope = f"preset:{name}"
    state = read_file(path, scope, within=path.parent)
    clean, findings = sanitise(state.document, scope) if state.readable else ({}, [])
    ck = Checker(partial=True)
    meta = PRESET_META.check(state.document.get("preset", {}), ("preset",), ck)
    meta = meta if isinstance(meta, dict) else {}
    noted = [replace(finding, scope=scope) for finding in ck.findings]
    return Preset(
        name=name,
        title=str(meta.get("title") or name.capitalize()),
        description=str(meta.get("description") or ""),
        source=source,
        path=path,
        document=clean,
        findings=(*state.findings, *findings, *noted),
    )


def find(name: str, home: Path) -> Preset:
    """A preset by name: a shipped one first, then the user's."""
    check_name(name)
    if name in SHIPPED:
        return load(shipped_dir() / f"{name}.json", name, "shipped")
    path = user_dir(home) / f"{name}.json"
    if not path.is_file():
        raise UnknownPreset(
            f"no preset {name!r}; workspace.presets lists the presets there are",
            details={"name": name, "shipped": list(SHIPPED)},
        )
    return load(path, name, "user")


def list_all(home: Path) -> tuple[list[Preset], list[Finding]]:
    """Every shipped preset in a fixed order, then the user's presets by name."""
    presets = [load(shipped_dir() / f"{name}.json", name, "shipped") for name in SHIPPED]
    notes: list[Finding] = []
    folder = user_dir(home)
    if not folder.is_dir():
        return presets, notes
    for path in sorted(folder.glob("*.json"))[:MAX_USER_PRESETS]:
        name = path.stem
        if not NAME_RE.match(name):
            notes.append(_note(name, f"{path.name} is not a valid preset name; it is not listed"))
        elif name in SHIPPED:
            notes.append(
                _note(name, f"{path.name} has the name of a shipped preset; it is not listed")
            )
        else:
            presets.append(load(path, name, "user"))
    return presets, notes


def _note(name: str, message: str) -> Finding:
    return Finding("workspace.preset_name", "notice", (), message, f"preset:{name}")


def save(
    name: str, document: Mapping[str, JSON], home: Path, *, title: str, description: str
) -> Path:
    """Write the user preset ``name`` from a workspace document; returns its path."""
    check_name(name)
    if name in SHIPPED:
        raise WorkspaceInvalid(
            f"{name!r} is a shipped preset; save yours under another name",
            details={"name": name, "shipped": list(SHIPPED)},
        )
    meta = {
        "title": title or name.capitalize(),
        "description": description or f"Saved on {datetime.now(UTC).date().isoformat()}.",
    }
    ck = Checker(partial=True)
    PRESET_META.check(meta, ("preset",), ck)
    if ck.findings:
        problems = "; ".join(f"{f.path[-1]} {f.message}" for f in ck.findings)
        raise WorkspaceInvalid(f"preset {name!r}: {problems}")
    content: dict[str, JSON] = {"version": SCHEMA_VERSION, "preset": meta}
    # **A preset never carries the user's own settings** (T-176 correction 2). `save_preset`
    # snapshots the EFFECTIVE document, which contains `preferences`, so without this a preset would
    # capture the saver's mouse scheme and impose it on whoever applied it. Dropped silently: the
    # preset is still a useful arrangement, and the section was never the point of saving one.
    content.update(
        {
            key: value
            for key, value in document.items()
            if key not in METADATA and key not in USER_ONLY
        }
    )
    path = user_dir(home) / f"{name}.json"
    write_file(path, content)
    return path
