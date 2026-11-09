"""Checks on CAD source files before any worker starts (ADR-0014, ADR-0021 decision 1.4).
Standard library only.

A source file is untrusted input (CLAUDE.md §4): it is resolved, must be a regular file below
the size limit with a known suffix and a matching header (:mod:`stokes.geometry.formats`), and
is only ever read. The source may live anywhere the user picked it; containment rules apply to
what Stokes writes.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from stokes.geometry import formats
from stokes.geometry.errors import CadUnavailable, GeometryFileError

MAX_SOURCE_BYTES = 1024**3
MAX_STEP_BYTES = MAX_SOURCE_BYTES
"""The old name of the source size limit; both formats share it."""
STEP_SUFFIXES = (".step", ".stp")
_NAME_JUNK_RE = re.compile(r"[^A-Za-z0-9_]+")
MAX_NAME_LENGTH = 64


@cache
def cad_available() -> bool:
    """True when OCP (the ``cad`` extra) can be imported; locating it does not import it."""
    return importlib.util.find_spec("OCP") is not None


def require_cad() -> None:
    if not cad_available():
        raise CadUnavailable(
            "geometry import needs the optional 'cad' extra (cadquery-ocp); "
            'install it with: pip install "stokes-cfd[cad]"',
            details={"extra": "cad"},
        )


def check_source_file(path: str | Path) -> Path:
    """The resolved path of a readable geometry source, or :class:`GeometryFileError` saying
    why not. The suffix picks the format (:mod:`stokes.geometry.formats`), which also probes
    the header; an unknown suffix is a :class:`~stokes.geometry.errors.GeometryUnsupported`
    naming the known formats."""
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise GeometryFileError(
            f"cannot read {raw}: no such file", details={"path": str(raw)}
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise GeometryFileError(f"cannot read {raw}: {exc}", details={"path": str(raw)}) from exc
    if not resolved.is_file():
        raise GeometryFileError(f"{resolved} is not a file", details={"path": str(resolved)})
    fmt = formats.format_for(resolved)
    size = resolved.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise GeometryFileError(
            f"{resolved.name} is {size} bytes; the limit is {MAX_SOURCE_BYTES}",
            details={"path": str(resolved), "size": size},
        )
    formats.probe(resolved, fmt)
    return resolved


def check_step_file(path: str | Path) -> Path:
    """The resolved path of a readable STEP file; the :func:`check_source_file` checks plus the
    STEP suffix, kept for callers that ask for STEP by name."""
    resolved = check_source_file(path)
    if resolved.suffix.lower() not in STEP_SUFFIXES:
        raise GeometryFileError(
            f"{resolved.name} is not a STEP file (expected {' or '.join(STEP_SUFFIXES)})",
            details={"path": str(resolved)},
        )
    return resolved


@dataclass(frozen=True)
class SourceStamp:
    """What a mesh was made from: file name, size and modification time (a cache key)."""

    name: str
    size: int
    mtime_ns: int

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size, "mtime_ns": self.mtime_ns}

    @classmethod
    def of(cls, path: Path) -> SourceStamp:
        info = path.stat()
        return cls(path.name, info.st_size, info.st_mtime_ns)

    @classmethod
    def from_json(cls, data: Any) -> SourceStamp | None:  # Any: header JSON
        if not isinstance(data, dict):
            return None
        name, size, mtime = data.get("name"), data.get("size"), data.get("mtime_ns")
        if not isinstance(name, str) or not isinstance(size, int) or not isinstance(mtime, int):
            return None
        return cls(name, size, mtime)


def node_name_for(text: str, fallback: str = "part") -> str:
    """A node name (letters, digits, underscores; no leading digit) made from free text."""
    name = _NAME_JUNK_RE.sub("_", text).strip("_")
    name = re.sub(r"_+", "_", name)[:MAX_NAME_LENGTH].rstrip("_")
    if not name:
        return fallback
    if name[0].isdigit():
        name = f"{fallback}_{name}"[:MAX_NAME_LENGTH]
    return name


#: A trailing run of hex digits at least this long, which a generated file name carries as a
#: content hash (`demo_geometry.demo_source_name`). Eight is well past the point where a real
#: name ends in hex by accident, and well under the twelve a sha256 prefix uses.
_HASH_TAIL = re.compile(r"[_-][0-9a-f]{8,}$")


def part_name_for(path: Path) -> str:
    """A node name derived from a file name, **without a content hash the file carries**.

    **The hash belongs to the FILE, not to the node** (claude-win2's package-two review item 10):
    `demo_pipe_e4977bbfe7f9.step` exists so that a rebuilt sample is a different file only when its
    bytes differ, which fixed a real defect - a fixed name rewrote the same file, a saved project's
    source looked modified and its tags went stale. But the PART is called `demo_pipe_e4977bbfe7f9`,
    and a user reads a hash where a name belongs.

    **A trailing hex run, not "everything after the last underscore"**: `flange_2` and `inlet_2024`
    are names a person wrote, and stripping them would be worse than the hash. Eight characters of
    `[0-9a-f]` is the shape a generated hash has and a name almost never does.
    """
    stem = _HASH_TAIL.sub("", path.stem)
    # **A name that is ONLY a hash keeps it.** Stripping to nothing would leave an empty node
    # name, which the model refuses - so `deadbeefcafe.step` keeps its name rather than failing.
    return node_name_for(stem or path.stem)


def unique_name(base: str, taken: set[str]) -> str:
    """``base``, or ``base_2``, ``base_3`` ... whichever is not taken."""
    if base not in taken:
        return base
    index = 2
    while f"{base}_{index}" in taken:
        index += 1
    return f"{base}_{index}"
