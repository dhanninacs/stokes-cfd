"""Path syntax for addressing values in a workspace document.

A path is a dot-separated list of segments: ``layout.left.panels.0.size``. A
plain segment that is all digits is a list index when it meets a list. A key
that contains ``.``, ``[``, ``]`` or ``"`` is written in brackets as a JSON
string, ``shortcuts["view.fit"]``, and ``[3]`` is an explicit list index.
:func:`format_path` gives the canonical spelling that ``workspace.get`` reports
in ``sources`` and warnings; :func:`parse` accepts it and the plain dotted
spelling alike. Resolving dotted keys such as ``shortcuts.view.fit`` against a
document is the job of :mod:`stokes.workspace.access`.

Pure functions; standard library only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from stokes.workspace.errors import WorkspacePathError

JSON = Any  # Any: a decoded JSON value (object, array, string, number, boolean or null)

Segment = str | int
Loc = tuple[Segment, ...]
"""A parsed path: keys are strings, explicit list indices are integers."""

MAX_PATH_CHARS = 512
_PLAIN_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_INDEX = re.compile(r"^-?\d+$")
_DIGITS = re.compile(r"^\d+$")
_DECODER = json.JSONDecoder()


def parse(text: str) -> Loc:
    """Split a path into segments; raises :class:`WorkspacePathError` on bad syntax."""
    if not isinstance(text, str) or not text.strip():
        raise WorkspacePathError("a path must be a non-empty string such as layout.left.size")
    if len(text) > MAX_PATH_CHARS:
        raise WorkspacePathError(f"a path has at most {MAX_PATH_CHARS} characters")
    source = text.strip()
    segments: list[Segment] = []
    i, n = 0, len(source)
    while i < n:
        if source[i] == "[":
            segment, i = _bracket(source, i)
        else:
            j = i
            while j < n and source[j] not in ".[]":
                j += 1
            if j == i or (j < n and source[j] == "]"):
                raise WorkspacePathError(f"empty or malformed segment at position {i} of {text!r}")
            segment, i = source[i:j], j
        segments.append(segment)
        if i < n and source[i] == ".":
            i += 1
            if i == n:
                raise WorkspacePathError(f"path {text!r} ends with a dot")
        elif i < n and source[i] != "[":
            raise WorkspacePathError(f"unexpected {source[i]!r} at position {i} of {text!r}")
    return tuple(segments)


def _bracket(source: str, start: int) -> tuple[Segment, int]:
    """Read ``[3]`` or ``["key"]`` starting at ``source[start] == "["``; returns (segment, next)."""
    inner = start + 1
    if inner < len(source) and source[inner] == '"':
        try:
            value, end = _DECODER.raw_decode(source, inner)
        except ValueError as exc:
            raise WorkspacePathError(f"bad quoted key at position {start}: {exc}") from exc
        if not isinstance(value, str) or end >= len(source) or source[end] != "]":
            raise WorkspacePathError(f"a quoted key must be followed by ']' (position {start})")
        return value, end + 1
    close = source.find("]", inner)
    body = source[inner:close] if close >= 0 else ""
    if close < 0 or not _INDEX.match(body):
        raise WorkspacePathError(
            f'brackets hold an index or a quoted key, e.g. [0] or ["a.b"] (position {start})'
        )
    return int(body), close + 1


def as_index(segment: Segment) -> int | None:
    """The list index a segment stands for, or None when it is not one."""
    if isinstance(segment, int):
        return segment
    return int(segment) if _DIGITS.match(segment) else None


def format_path(loc: Sequence[Segment]) -> str:
    """The canonical spelling of a parsed path (empty for the document itself)."""
    out: list[str] = []
    for segment in loc:
        if isinstance(segment, int):
            out.append(f".{segment}" if out else str(segment))
        elif _PLAIN_KEY.match(segment) and not _DIGITS.match(segment):
            out.append(f".{segment}" if out else segment)
        else:
            out.append(f"[{json.dumps(segment, ensure_ascii=False)}]")
    return "".join(out)


def is_within(path: Sequence[Segment], root: Sequence[Segment]) -> bool:
    """True when ``path`` is ``root`` or lies below it."""
    return tuple(path[: len(root)]) == tuple(root)
