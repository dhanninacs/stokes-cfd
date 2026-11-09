"""What a run's function objects wrote, as tables a client can plot (plan T-311).

OpenFOAM's function objects write `postProcessing/<function>/<time>/<file>.dat`: comment lines
starting `#`, the last of which names the columns (`# Time   p   U`), then one row per write. A
vector or tensor value is written in parentheses, `(1 0 0)`, and becomes one column per component
(`U_x`, `U_y`, `U_z`; `_0`, `_1`, ... past three). A row that does not read as numbers is skipped,
not guessed at.

**Bounded**: at most `MAX_TABLE_BYTES` of a file is read by the agent, at most `MAX_ROWS` rows are
kept (every n-th past that, so the whole run stays on the plot) and at most `MAX_COLUMNS` columns.

Import rules: standard library only. Headless.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

#: The suffixes of the files offered as monitors.
TABLE_SUFFIXES: tuple[str, ...] = (".dat", ".xy", ".csv")
MAX_TABLE_BYTES = 1024 * 1024
MAX_ROWS = 20000
MAX_COLUMNS = 64
_COMPONENTS = ("x", "y", "z")
_GROUP_RE = re.compile(r"\(([^()]*)\)")


@dataclass
class Table:
    """One monitor file as columns of floats."""

    columns: list[str] = field(default_factory=list)
    values: list[list[float]] = field(default_factory=list)
    truncated: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "columns": self.columns,
            "values": dict(zip(self.columns, self.values, strict=True)),
            "truncated": self.truncated,
        }


def is_table(path: str) -> bool:
    """Whether a `postProcessing/` file is one this reader plots."""
    return path.lower().endswith(TABLE_SUFFIXES)


def monitor_name(path: str) -> str:
    """The name a client shows: the path under `postProcessing/`."""
    return path.removeprefix("postProcessing/")


def _cells(line: str) -> list[list[str]]:
    """A data row as cells; a parenthesised group is one cell of several components."""
    cells: list[list[str]] = []
    position = 0
    for match in _GROUP_RE.finditer(line):
        cells.extend([word] for word in line[position : match.start()].replace(",", " ").split())
        cells.append(match.group(1).replace(",", " ").split())
        position = match.end()
    cells.extend([word] for word in line[position:].replace(",", " ").split())
    return cells


def _names(header: list[str], shapes: list[int]) -> list[str]:
    """Column names: the header's, with a component suffix for each component of a group."""
    names: list[str] = []
    for index, width in enumerate(shapes):
        base = header[index] if index < len(header) else f"col{index}"
        if width == 1:
            names.append(base)
        else:
            suffixes = _COMPONENTS if width <= len(_COMPONENTS) else [str(n) for n in range(width)]
            names.extend(f"{base}_{suffix}" for suffix in suffixes[:width])
    return names


def parse_table(text: str) -> Table:
    """The columns and values of one function-object file. Never raises."""
    header: list[str] = []
    rows: list[list[float]] = []
    shapes: list[int] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            words = line.lstrip("#").split()
            if words and not rows:
                header = words
            continue
        cells = _cells(line)
        widths = [len(cell) for cell in cells]
        if shapes is None:
            shapes = widths
        if widths != shapes:
            continue
        try:
            row = [float(word) for cell in cells for word in cell]
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in row):
            continue
        rows.append(row)
    table = Table()
    if shapes is None or not rows:
        return table
    names = _names(header, shapes)[:MAX_COLUMNS]
    if len(rows) > MAX_ROWS:
        stride = math.ceil(len(rows) / MAX_ROWS)
        rows = rows[::stride]
        table.truncated = True
    table.columns = names
    table.values = [[row[index] for row in rows] for index in range(len(names))]
    return table
