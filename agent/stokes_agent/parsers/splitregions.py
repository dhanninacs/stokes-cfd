"""Parse a `splitMeshRegions` log into the regions and the interfaces between them (ADR-0042).

Two tables, as a real run on Foundation 14 printed them (2026-11-20; the columns are padded to
the longest name, so the headers and rows may be indented):

    Region  nCells  cellZone
    ------  ------  --------
       hot   24960       hot
      cold   24960      cold
     solid   78080    <none>

        Interface  Owner Region  Neighbour Region  nFaces
    -------------  ------------  ----------------  ------
    cold_to_solid          cold             solid   22848
     hot_to_solid           hot             solid   22848

The regions are listed in index order (the order `constant/cellToRegion` numbers them), and the
default region's cell zone is `<none>`.

The interface table is the one that matters: two fluids that share faces are a wall the mesh could
not hold, and only this log says so.

Same rule as every parser here: a log is not ours, so nothing in it can fail the job. A table that
is missing or cut gives empty lists.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

#: The most of a log that is kept, from its end.
MAX_BYTES = 1024 * 1024

#: The most rows either table keeps; a real split has a handful.
MAX_ROWS = 512

#: A line longer than this is skipped.
MAX_LINE = 1024

_REGION_HEADER = re.compile(r"^\s*Region\s+nCells\s+cellZone\s*$")
_INTERFACE_HEADER = re.compile(r"^\s*Interface\s+Owner Region\s+Neighbour Region\s+nFaces\s*$")
_DASHES = re.compile(r"^[\s-]+$")
_REGION_ROW = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]{0,63})\s+(\d+)\s+(\S{1,64})\s*$")
_WORD = r"([A-Za-z][A-Za-z0-9_]{0,127})"
_INTERFACE_ROW = re.compile(rf"^\s*{_WORD}\s+{_WORD}\s+{_WORD}\s+(\d+)\s*$")


def parse_split_regions(text: str) -> dict[str, Any]:
    """`{"regions": [{name, cells}], "interfaces": [{name, owner, neighbour, faces}]}`."""
    regions: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    table = ""
    for line in text.splitlines():
        if len(line) > MAX_LINE:
            continue
        if _REGION_HEADER.match(line):
            table, regions = "regions", []
            continue
        if _INTERFACE_HEADER.match(line):
            table, interfaces = "interfaces", []
            continue
        if not table:
            continue
        if not line.strip():
            table = ""
            continue
        if _DASHES.match(line):
            continue
        if table == "regions":
            row = _REGION_ROW.match(line)
            if row is None:
                table = ""
            elif len(regions) < MAX_ROWS:
                regions.append({"name": row.group(1), "cells": int(row.group(2))})
        else:
            found = _INTERFACE_ROW.match(line)
            if found is None:
                table = ""
            elif len(interfaces) < MAX_ROWS:
                interfaces.append(
                    {
                        "name": found.group(1),
                        "owner": found.group(2),
                        "neighbour": found.group(3),
                        "faces": int(found.group(4)),
                    }
                )
    return {"regions": regions, "interfaces": interfaces}


class SplitRegionsParser:
    """The `LogParser` for splitMeshRegions: keeps the tail and reads the tables at the end."""

    def __init__(self, **_: Any) -> None:
        self._chunks: deque[str] = deque()
        self._size = 0

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """Keep the lines. Returns no events, by design."""
        for line in lines:
            self._chunks.append(line)
            self._size += len(line) + 1
        while self._size > MAX_BYTES and self._chunks:
            dropped = self._chunks.popleft()
            self._size -= len(dropped) + 1
        return []

    def report(self) -> dict[str, Any]:
        """The two tables, read from what was kept."""
        return parse_split_regions("\n".join(self._chunks))
