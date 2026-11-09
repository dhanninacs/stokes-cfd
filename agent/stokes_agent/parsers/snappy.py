"""Parse a snappyHexMesh log into the layer coverage table (brief M-504).

The table snappy prints at the end of the layer pass (`snappyLayerDriver.C:2456`,
`printLayerData`) — verified in the install's source and against a real run of the pipe with
3 layers (`tests/fixtures/of14/logs/snappy-layers.log`), not written from memory:

    patch      faces    layers   overall thickness
                                      [m]       [%]
    -----      -----    ------   ---       ---
    walls       12345   2.730    0.00123   91.2

The patch column is left-justified to the longest patch name, the other three are 8-wide with
3 significant digits, and the coverage column is the mean over the patch's faces of the
achieved thickness divided by the wanted one. **The wanted count itself is not in the
table** - snappy does not print it - so the report carries what the log gives and the service
pairs it with the counts it asked for.

## Never fail a job over a log

Same rule as every parser here: `feed` raising becomes `{"error": …}` in the report, nothing
more. A truncated table, a 1 MiB line or a log with no table at all give an empty list - the
reader decides what an absent table means, and a parser that throws turns a successful mesh
into a failed job.
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import Any

#: The most of a log that is kept, from its end - the same bound and reason as the checkMesh
#: parser's.
MAX_BYTES = 4 * 1024 * 1024

#: The most rows the report keeps (the M-504 security review's F3): a log whose table carries
#: 200,000 rows would otherwise put every one of them into `meta.layers`, persisted and sent in
#: events. A real mesh's table is one row per patch - a few dozen.
MAX_ROWS = 1024

#: A line longer than this is skipped. OpenFOAM writes no such line, and one that long is
#: either corrupt or built to defeat an anchored scan.
MAX_LINE = 4096

#: The table's header, with the columns the rows carry. **The words, not the padding**: the
#: patch name is padded to the longest name in the table, so the spacing between the columns
#: is not fixed and the anchor is the words in order.
_HEADER = re.compile(r"^patch\s+faces\s+layers\s+overall thickness\s*$")

#: The units line under the header (`                   [m]       [%]`) - what says the
#: thickness column is metres and the last is a percentage.
_UNITS = re.compile(r"^\s*\[m\]\s+\[%\]\s*$")

#: The dashes line that separates the header from the rows.
_DASHES = re.compile(r"^\s*-{3,}\s+-{3,}\s+-{3,}\s+-{3,}\s+-{3,}\s*$")

#: A row: the patch name, then faces (a whole number), then the three means - the mean layer
#: count, the mean overall thickness in metres, and the coverage percentage. All three are
#: floats in OpenFOAM's `%.3g`-ish form (`3`, `2.73`, `0.00123`, `1e-05`), so the tokens are
#: parsed, not pattern-shaped.
_ROW = re.compile(r"^(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def parse_snappy_layers(text: str) -> list[dict[str, Any]]:
    """The final layer table as ``[{patch, faces, layers_mean, thickness_mean,
    coverage_percent}]``.

    Only rows under a table header are read, so a patch name that appears elsewhere in the log
    cannot invent a row. **`layers_target` is deliberately absent**: snappy does not print the
    count it was asked for, and inventing one here would make an absent request look measured.
    The service pairs this table with the counts it requested.
    """
    rows: list[dict[str, Any]] = []
    in_table = False
    for line in _tail(text).splitlines():
        if len(line) > MAX_LINE:
            continue
        if _HEADER.match(line):
            in_table = True
            rows = []  # a later table is the one that counts: the final state of the run
            continue
        if not in_table:
            continue
        if _UNITS.match(line) or _DASHES.match(line):
            continue
        match = _ROW.match(line)
        if match is None:
            # The table ended: a blank line or the next section. A blank line follows the last
            # row (`printLayerData` ends with `endl`), and any other word closes it too.
            if not line.strip():
                in_table = False
            continue
        faces, layers, thickness, coverage = (
            _int(match.group(2)),
            _number(match.group(3)),
            _number(match.group(4)),
            _number(match.group(5)),
        )
        if (
            faces is None
            or layers is None
            or thickness is None
            or coverage is None
            # **Non-finite is not a number here** (the review's F3): `float("nan")` parses, and
            # a NaN reaching `meta.layers` reaches `json.dumps` as `NaN`, which browsers'
            # `JSON.parse` rejects.
            or not all(math.isfinite(v) for v in (layers, thickness, coverage))
        ):
            # A row whose numbers do not parse is not a row: skip it rather than report a
            # partial figure.
            continue
        if len(rows) >= MAX_ROWS:
            # **Capped**: drop the OLDEST rows rather than growing without bound — the tail of
            # the table is the final state of the run, which is the part the report exists for.
            rows.pop(0)
        rows.append(
            {
                "patch": match.group(1),
                "faces": faces,
                "layers_mean": layers,
                "thickness_mean": thickness,
                "coverage_percent": coverage,
            }
        )
    return rows


def _tail(text: str) -> str:
    """The last `MAX_BYTES` of the log, cut at a line boundary."""
    if len(text) <= MAX_BYTES:
        return text
    cut = text[-MAX_BYTES:]
    newline = cut.find("\n")
    return cut[newline + 1 :] if newline != -1 else cut


def _number(text: str) -> float | None:
    """A finite float, or None when the text is not one. Never raises: the log is not ours.

    **Non-finite is None** (the review's F3): `float("nan")` and `float("inf")` parse, and a
    NaN reaching the result meta reaches `json.dumps` as `NaN`, which browsers reject.
    """
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _int(text: str) -> int | None:
    """An int, or None when the text is not one."""
    try:
        return int(text)
    except ValueError:
        return None


class SnappyParser:
    """The `LogParser` for snappyHexMesh: keeps the tail and reads the table once at the end.

    **`feed` returns nothing and `report` does all the work**, exactly as the checkMesh parser
    does: the table is printed at the end of the layer pass, so a per-line parse would report
    half a mesh and then correct itself.
    """

    def __init__(self, **_: Any) -> None:
        # **A deque with a running size, not a list with `pop(0)`** (the review's F3): the list
        # trim was quadratic — 18 s of CPU on a 60 MB log, measured by the review — while the
        # deque's popleft is linear over the whole feed.
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
        """The layer table, read from what was kept."""
        return {"layers": parse_snappy_layers("\n".join(self._chunks))}
