"""Parse a Gmsh log: its errors, its warning count and the size of the mesh it made (ADR-0043).

Gmsh prints every message on one line with its level first (`Info    : `, `Warning : `,
`Error   : `) and ends a mesh with `Info    : <nodes> nodes <elements> elements` (measured on
Gmsh 4.15.2). On a terminal it colours the line with ANSI escapes, which are stripped. The report is
what the mesh service needs to say why a job failed - Gmsh's own first error - and nothing more.

Same rule as every parser here: a parser may not fail a job, so a line it cannot read is skipped.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

#: The most error lines kept, and the most characters of each: the first few say why.
MAX_ERRORS = 20
MAX_ERROR_CHARS = 300

#: A line longer than this is skipped; Gmsh writes none.
MAX_LINE = 4096

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_LEVEL = re.compile(r"^(Info|Warning|Error)\s*:\s?(.*)$")
_SIZE = re.compile(r"^(\d+) nodes (\d+) elements\s*$")
#: The summary Gmsh prints at the end of a run with problems; it carries no error of its own.
_SUMMARY = re.compile(
    r"^(-{5,}|Mesh generation error summary|\d+ (warnings?|errors?)|Check the full log)"
)
#: The line the Stokes script prints before growing layers (`stokes.mesh.drivers.gmsh.LAYER_MARK`).
_LAYERS = re.compile(r"^stokes layers (\d{1,2})$")


class GmshParser:
    """The `LogParser` for gmsh: the errors as they come, the counts at the end."""

    def __init__(self, **_: Any) -> None:
        self._errors: deque[str] = deque(maxlen=MAX_ERRORS)
        self._warnings = 0
        self._nodes: int | None = None
        self._elements: int | None = None
        self._stage = ""
        self._layers = 0

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """Keep the errors and the counts. Returns no events, by design."""
        for raw in lines:
            if len(raw) > MAX_LINE:
                continue
            line = _ANSI.sub("", raw).strip()
            layers = _LAYERS.match(line)
            if layers is not None:
                self._layers = int(layers.group(1))
                continue
            match = _LEVEL.match(line)
            if match is None:
                continue
            level, text = match.group(1), match.group(2).strip()
            if level == "Error":
                if text and not _SUMMARY.match(text) and len(self._errors) < MAX_ERRORS:
                    self._errors.append(text[:MAX_ERROR_CHARS])
            elif level == "Warning":
                if not _SUMMARY.match(text):
                    self._warnings += 1
            else:
                size = _SIZE.match(text)
                if size is not None:
                    self._nodes, self._elements = int(size.group(1)), int(size.group(2))
                elif text.startswith("Meshing ") and text.endswith("..."):
                    self._stage = text[len("Meshing ") : -3]
        return []

    def report(self) -> dict[str, Any]:
        """The errors in order, the warning count, the layer count the script grew (0 for none),
        the last stage reached and the mesh size."""
        return {
            "errors": list(self._errors),
            "warnings": self._warnings,
            "layers": self._layers,
            "stage": self._stage,
            "nodes": self._nodes,
            "elements": self._elements,
        }
