"""Parse a checkMesh log into the mesh's quality numbers (T-146, T-208).

Standard library only, and **every pattern below is anchored to a line that exists in
`tests/fixtures/of14/logs/`** — captured from real runs. Three of them differ from the
phrasing one would write from memory, and each difference is noted where it matters,
because a pattern that matches nothing returns `None` and `None` looks exactly like
"the log did not say".

## Never guess a number

A key whose line is absent is `None`, and the reader decides. The one thing this module must
not do is report a plausible value for a line that was not there: a mesh reported `OK`
because a figure defaulted to zero is worse than one reported unknown.

**The single exception is `failed_checks`**, which is `0` when no `Failed n mesh checks.` line
appears. checkMesh prints that line only on failure, so its absence is a reading rather than a
gap — and `None` would lose the difference between "nothing failed" and "the log was
unreadable".

## `checkMesh` exits 0 even when it fails its checks

Measured: the skewed-cavity fixture ends `Failed 2 mesh checks.` and the process exits
**0**. So the exit code is not the verdict and this parser is the only thing that reads
it — `mesh_ok` is "the log contains `Mesh OK.`", not "the command succeeded".
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import Any

#: The most of a log that is read, from its end. A mesh log is a few hundred kilobytes; a
#: file past this is not one, and reading it all would be a way to make the agent allocate
#: without limit.
MAX_BYTES = 4 * 1024 * 1024

#: A line longer than this is skipped. OpenFOAM writes no such line, and one that long is
#: either corrupt or built to defeat an anchored scan.
MAX_LINE = 4096

# **Anchored, no nested quantifiers, and the number is captured in a fixed position.** Each
# has the real fixture line it matches in the comment above it.
_POINTS = re.compile(r"^ {4}points:\s+(\d+)\s*$")
_FACES = re.compile(r"^ {4}faces:\s+(\d+)\s*$")
_INTERNAL_FACES = re.compile(r"^ {4}internal faces:\s+(\d+)\s*$")
_CELLS = re.compile(r"^ {4}cells:\s+(\d+)\s*$")
_PATCH_COUNT = re.compile(r"^ {4}boundary patches:\s+(\d+)\s*$")
# `Mesh non-orthogonality Max: 5.95045 average: 1.63034` — **`Max:` with a colon**, and
# `average:` after it.
_NON_ORTHO = re.compile(
    r"^ {4}Mesh non-orthogonality Max:\s+([-\d.eE+]+)\s+average:\s+([-\d.eE+]+)\s*$"
)
# `    Max skewness = 0.260575 OK.` and
# ` ***Max skewness = 21.4007, 244 highly skew faces detected …`
# **The failing form carries a `***` prefix and a comma**, so the pattern allows both and
# does not anchor on the leading spaces.
_SKEWNESS = re.compile(r"^\s*\*{0,3}Max skewness\s*=\s*([-\d.eE+]+)")
# **Two forms, and the failing one is the one that matters.** The install's own source,
# `src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C:724`, prints on failure the words
# `High aspect ratio cells found, Max aspect ratio:` followed by the number and the cell
# count — a `***` prefix, the words `cells found,`, and a COLON. The passing form is
# `    Max aspect ratio = 8.1407 OK.` with an equals sign. A pattern written for one reads
# `None` on the other, so a mesh that failed THIS check reported no aspect ratio at all —
# and the threshold is 1000, so any real mesh can reach it.
_ASPECT = re.compile(
    r"^\s*(?:\*{0,3}Max aspect ratio\s*=|\*{0,3}High aspect ratio cells found,\s*"
    r"Max aspect ratio:)\s*([-\d.eE+]*\d)"
)
# `Min volume = 1.6902e-10. Max volume = 3.83579e-09.  Total volume = 1.4516e-05.  Cell volumes OK.`
# `    Min volume = 1.6902e-10. Max volume = 3.83579e-09.  Total volume = 1.4516e-05.`
# `  Cell volumes OK.`
# **The sentence's full stop is not part of the number.** A greedy character class swallowed it, so
# `float()` refused `3.83579e-09.` and the value came back None. The group now ends at a digit.
_VOLUME = re.compile(r"^\s*Min volume\s*=\s*([-\d.eE+]*\d)\.\s*Max volume\s*=\s*([-\d.eE+]*\d)")
# **The failing form is a different SENTENCE, not the same one with a different number.**
# Verified in the
# install's own source (`src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C:845`):
#
#     " ***Zero or negative cell volume detected.  " << "Minimum negative volume: " << minVolume  # noqa: ERA001 - quoted from the install's source, as evidence
#
# A non-positive cell volume is the worst thing a mesh can contain, and it was the one case that
# reported
# nothing at all — the parser returned None exactly where a user most needs the number.
_NEGATIVE_VOLUME = re.compile(
    r"^\s*\*{0,3}Zero or negative cell volume detected\.\s*"
    r"Minimum negative volume:\s*([-\d.eE+]*\d)"
)
#: `…, Number of negative volume cells: 3`
_NEGATIVE_CELLS = re.compile(r"Number of negative volume cells:\s*(\d+)")
# `   *Number of severely non-orthogonal (> 70 degrees) faces: 242.`
_SEVERE_NON_ORTHO = re.compile(r"^\s*\*{0,3}Number of severely non-orthogonal[^:]*:\s*(\d+)")
# `Mesh OK.` — the verdict, and the only thing that means the checks passed.
_MESH_OK = re.compile(r"^Mesh OK\.\s*$")
# `Failed 2 mesh checks.`
_FAILED = re.compile(r"^Failed\s+(\d+)\s+mesh checks?\.\s*$")
# The patch table: a header, then `    inlet               30       62       ok (…)`
_PATCH_HEADER = re.compile(r"^\s*Patch\s+Faces\s+Points\s+Surface topology")
_PATCH_ROW = re.compile(r"^ {4}(\S+)\s+(\d+)\s+(\d+)\s+(\S.*?)\s*$")
# The cell-type block (brief M-506 Part A): `Overall number of cells of each type:` then seven
# lines, `    hexahedra:     12225`. **All seven are reported, absent as 0** — a log without the
# block (a very old checkMesh) means no cells of any named type were counted, and inventing a
# `None` shape for three of the seven would make the page special-case them.
_CELL_TYPE_HEADER = re.compile(r"^Overall number of cells of each type:\s*$")
_CELL_TYPE_ROW = re.compile(
    r"^ {4}(hexahedra|prisms|wedges|pyramids|tet wedges|tetrahedra|polyhedra):\s+(\d+)\s*$"
)
_CELL_TYPE_KEYS = {
    "hexahedra": "hex",
    "prisms": "prism",
    "wedges": "wedge",
    "pyramids": "pyramid",
    "tet wedges": "tet_wedge",
    "tetrahedra": "tet",
    "polyhedra": "poly",
}


def parse_checkmesh(text: str) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
    # A dispatcher over the ~15 line formats checkMesh prints, one branch each; splitting it would
    # separate the formats from the code that reads them.
    """The quality numbers in a checkMesh log.

    Absent keys are `None`. The function never raises: a log is output from a program we do
    not control, and a parser that throws turns a successful mesh into a failed job.
    """
    tail = _tail(text)
    found: dict[str, Any] = {
        "cells": None,
        "points": None,
        "faces": None,
        "internal_faces": None,
        "boundary_patches": None,
        "max_non_ortho": None,
        "avg_non_ortho": None,
        "severe_non_ortho_faces": None,
        "max_skewness": None,
        "max_aspect_ratio": None,
        "min_volume": None,
        "max_volume": None,
        "negative_volume_cells": None,
        # **`0` here is a reading, not a default.** checkMesh prints `Failed n mesh checks.`
        # only when something failed, so a log without that line reports zero failures — unlike
        # the figures above, where an absent line means the log did not say. An adversarial
        # review flagged this as breaking my own "absent is None" rule; it is the one key where
        # absence IS information, and it is called out here rather than changed, because `None`
        # would lose the distinction between "no failures" and "the log was unreadable".
        "failed_checks": 0,
        "mesh_ok": False,
        "patches": [],
        # **All seven, absent as 0** (brief M-506 Part A): a log without the block says no cells
        # of any named type, which is a reading, not a gap — the same reasoning as
        # `failed_checks` above.
        "cell_types": {
            "hex": 0,
            "prism": 0,
            "wedge": 0,
            "pyramid": 0,
            "tet_wedge": 0,
            "tet": 0,
            "poly": 0,
        },
    }
    in_patches = False
    in_cell_types = False
    for line in tail.splitlines():
        if len(line) > MAX_LINE:
            continue
        match = _CELLS.match(line)
        if match is not None:
            found["cells"] = int(match.group(1))
            continue
        match = _POINTS.match(line)
        if match is not None:
            found["points"] = int(match.group(1))
            continue
        match = _FACES.match(line)
        if match is not None:
            found["faces"] = int(match.group(1))
            continue
        match = _INTERNAL_FACES.match(line)
        if match is not None:
            found["internal_faces"] = int(match.group(1))
            continue
        match = _PATCH_COUNT.match(line)
        if match is not None:
            found["boundary_patches"] = int(match.group(1))
            continue
        match = _NON_ORTHO.match(line)
        if match is not None:
            found["max_non_ortho"] = _number(match.group(1))
            found["avg_non_ortho"] = _number(match.group(2))
            continue
        match = _SKEWNESS.match(line)
        if match is not None:
            found["max_skewness"] = _number(match.group(1))
            continue
        match = _SEVERE_NON_ORTHO.match(line)
        if match is not None:
            found["severe_non_ortho_faces"] = int(match.group(1))
            continue
        match = _ASPECT.match(line)
        if match is not None:
            found["max_aspect_ratio"] = _number(match.group(1))
            continue
        match = _VOLUME.match(line)
        if match is not None:
            found["min_volume"] = _number(match.group(1))
            found["max_volume"] = _number(match.group(2))
            continue
        match = _NEGATIVE_VOLUME.match(line)
        if match is not None:
            # A negative volume is reported INSTEAD of the min/max sentence, so it fills
            # `min_volume`
            # with the figure the log carries — which is negative, and that is the point.
            found["min_volume"] = _number(match.group(1))
            cells = _NEGATIVE_CELLS.search(line)
            if cells is not None:
                found["negative_volume_cells"] = int(cells.group(1))
            continue
        if _MESH_OK.match(line):
            found["mesh_ok"] = True
            continue
        match = _FAILED.match(line)
        if match is not None:
            found["failed_checks"] = int(match.group(1))
            continue
        if _CELL_TYPE_HEADER.match(line):
            in_cell_types = True
            continue
        if in_cell_types:
            match = _CELL_TYPE_ROW.match(line)
            if match is None:
                # The block ended: a blank line or the next section.
                if not line.strip() or line.startswith("Checking"):
                    in_cell_types = False
                continue
            found["cell_types"][_CELL_TYPE_KEYS[match.group(1)]] = int(match.group(2))
            continue
        if _PATCH_HEADER.match(line):
            in_patches = True
            continue
        if in_patches:
            match = _PATCH_ROW.match(line)
            if match is None:
                # The table ended: a blank line or the next section.
                if not line.strip() or line.startswith("Checking"):
                    in_patches = False
                continue
            found["patches"].append(
                {
                    "name": match.group(1),
                    "faces": int(match.group(2)),
                    "points": int(match.group(3)),
                }
            )
    return found


def _tail(text: str) -> str:
    """The last `MAX_BYTES` of the log, cut at a line boundary."""
    if len(text) <= MAX_BYTES:
        return text
    cut = text[-MAX_BYTES:]
    newline = cut.find("\n")
    return cut[newline + 1 :] if newline != -1 else cut


def _number(text: str) -> float | None:
    """A finite float, or None when the text is not one. Never raises: the log is not ours.

    **Non-finite is None** (the second review's required fix A2): `float("1e999")` parses as
    `inf`, and an inf reaching the result meta reaches `json.dumps` as `Infinity`, which
    browsers' `JSON.parse` rejects. The snappy parser has had this since the M-504 fixes; the
    review undid the checkMesh parser's `2badfe1` message and found it still accepted it.
    """
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class CheckMeshParser:
    """The `LogParser` for checkMesh: keeps the tail and reads it once at the end.

    **`feed` returns nothing and `report` does all the work.** checkMesh's figures are a
    summary printed at the end, so a per-line parse would report half a mesh and then
    correct itself — and a client that acted on the first numbers would act on a mesh whose
    checks had not finished.
    """

    def __init__(self, **_: Any) -> None:
        # **A deque with a running size** (the M-504 review's F3, applied to the checkMesh
        # parser as the review asks): the list's `pop(0)` trim was quadratic.
        self._chunks: deque[str] = deque()
        self._size = 0

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """Keep the lines. Returns no events, by design."""
        for line in lines:
            self._chunks.append(line)
            self._size += len(line) + 1
        # Bounded: keep the last MAX_BYTES, dropping whole lines from the front in O(1) each.
        while self._size > MAX_BYTES and self._chunks:
            dropped = self._chunks.popleft()
            self._size -= len(dropped) + 1
        return []

    def report(self) -> dict[str, Any]:
        """The parsed quality, read from what was kept."""
        return parse_checkmesh("\n".join(self._chunks))
