"""Keep each fluid zone to the cells its openings reach (ADR-0042), before the mesh is split.

A fluid body's surface can enclose pockets the flow can never reach: in a lattice exchanger each
stream's channels are cut back from the other stream's headers by a wall, and the cut leaves sealed
fragments of channel. snappyHexMesh's zoning puts them in the fluid's cell zone, `splitMeshRegions`
makes them part of the fluid region, and the region's pressure has no reference in them: measured
on a gyroid plate exchanger meshed at 0.6 mm, the hot region came out as 3 disconnected pieces and
the cold as 7, and the first `p_rgh` solve of each returned NaN (GAMG, 1000 iterations). At 0.8 mm
the pockets were too small to be meshed and the same case solved.

This reads the whole mesh (`owner`, `neighbour`, `boundary`, `cellZones`, all ASCII), finds the
face-connected pieces of each named zone, and keeps in the zone only the pieces that own a face of
one of the zone's open patches (its inlets and outlets). The rest leave the zone, so
`splitMeshRegions` gives them to the default region (the solid): a sealed pocket of a solid is
solid. A zone none of whose pieces reaches an opening keeps its largest piece, so a wrongly named
patch never empties a region.

The cell zones are rewritten in place; nothing else in the mesh changes. Numpy only.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

#: The most bytes of one mesh file read: a 10 M-cell mesh's `owner` is about 150 MB as text.
MAX_BYTES = 2_000_000_000

_HEADER_END = re.compile(rb"\n\s*(\d+)\s*\n\s*\(")


def _label_list(path: Path) -> np.ndarray:
    """An ASCII `labelList` file's values, as int64."""
    data = path.read_bytes()[:MAX_BYTES]
    found = _HEADER_END.search(data)
    if found is None:
        raise ValueError(f"{path.name} is not an ASCII label list")
    count = int(found.group(1))
    body = data[found.end() : data.rfind(b")")]
    values = np.array(body.split(), dtype=np.int64)
    if values.size != count:
        raise ValueError(f"{path.name} says {count} labels and holds {values.size}")
    return values


def _patches(path: Path) -> dict[str, tuple[int, int]]:
    """`{patch: (startFace, nFaces)}` from a `boundary` file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, tuple[int, int]] = {}
    for match in re.finditer(r"(\w+)\s*\{([^}]*)\}", text):
        body = match.group(2)
        start = re.search(r"\bstartFace\s+(\d+)\s*;", body)
        count = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        if start and count:
            out[match.group(1)] = (int(start.group(1)), int(count.group(1)))
    return out


_ZONE = re.compile(rb"(\w+)\s*\{\s*cellLabels\s+List<label>\s*(\d+)\s*\(([^)]*)\)\s*;\s*\}", re.S)


def _zones(path: Path) -> tuple[bytes, list[tuple[str, np.ndarray]], bytes]:
    """A `cellZones` file as (header, [(name, cells)], footer)."""
    data = path.read_bytes()[:MAX_BYTES]
    zones: list[tuple[str, np.ndarray]] = []
    first = last = None
    for match in _ZONE.finditer(data):
        cells = np.array(match.group(3).split(), dtype=np.int64)
        if cells.size != int(match.group(2)):
            raise ValueError(
                f"zone {match.group(1).decode()!r} says {match.group(2).decode()} cells"
            )
        zones.append((match.group(1).decode(), cells))
        first = match.start() if first is None else first
        last = match.end()
    if first is None or last is None:
        raise ValueError("cellZones holds no zone this can read")
    head = data[:first]
    # The count before the list's "(" is rewritten with the zones themselves.
    head = re.sub(rb"\n\s*\d+\s*\n\s*\(\s*$", b"\n", head.rstrip() + b"\n")
    return head, zones, data[last:]


def _write_zones(path: Path, head: bytes, zones: list[tuple[str, np.ndarray]], tail: bytes) -> None:
    parts = [head.rstrip(b"\n"), b"\n\n", str(len(zones)).encode(), b"\n(\n"]
    for name, cells in zones:
        parts += [
            name.encode(),
            b"\n{\ncellLabels      List<label> \n",
            str(cells.size).encode(),
            b"\n(\n",
            b"\n".join(str(int(c)).encode() for c in cells),
            b"\n)\n;\n}\n",
        ]
    rest = tail.lstrip()
    parts.append(rest if rest.startswith(b")") else b")\n" + rest)
    path.write_bytes(b"".join(parts))


def components(
    cells: np.ndarray, owner: np.ndarray, neighbour: np.ndarray, n_cells: int
) -> np.ndarray:
    """A piece label for every cell of `cells` (face-connected within them), in `cells`' order."""
    inside = np.zeros(n_cells, dtype=bool)
    inside[cells] = True
    a, b = owner[: neighbour.size], neighbour
    both = inside[a] & inside[b]
    a, b = a[both], b[both]
    label = np.arange(n_cells, dtype=np.int64)
    while True:
        low = np.minimum(label[a], label[b])
        before = label.copy()
        np.minimum.at(label, a, low)
        np.minimum.at(label, b, low)
        label = label[label]  # pointer jumping: a label points at its own piece's smallest cell
        if np.array_equal(label, before):
            break
    found: np.ndarray = label[cells]
    return found


def prune_zones(case_dir: Path, openings: dict[str, list[str]]) -> str:
    """Keep each zone named in `openings` to the pieces that reach one of its patches; a report."""
    mesh = Path(case_dir) / "constant" / "polyMesh"
    owner = _label_list(mesh / "owner")
    neighbour = _label_list(mesh / "neighbour")
    patches = _patches(mesh / "boundary")
    head, zones, tail = _zones(mesh / "cellZones")
    n_cells = int(max(owner.max(initial=-1), neighbour.max(initial=-1))) + 1
    lines: list[str] = []
    changed = False
    kept_zones: list[tuple[str, np.ndarray]] = []
    for name, cells in zones:
        names = openings.get(name)
        if not names or cells.size == 0:
            kept_zones.append((name, cells))
            continue
        piece = components(cells, owner, neighbour, n_cells)
        reach: set[int] = set()
        for patch in names:
            if patch not in patches:
                continue
            start, count = patches[patch]
            owners = owner[start : start + count]
            on = np.isin(cells, owners)
            reach.update(int(p) for p in np.unique(piece[on]))
        pieces, sizes = np.unique(piece, return_counts=True)
        if not reach:
            reach = {int(pieces[np.argmax(sizes)])}
            lines.append(f"{name}: no piece reaches {', '.join(names)}; kept the largest")
        keep = np.isin(piece, np.fromiter(reach, dtype=np.int64))
        dropped = int(cells.size - keep.sum())
        lines.append(
            f"{name}: {pieces.size} pieces, {len(reach)} kept, {dropped} cells "
            f"({dropped / max(cells.size, 1):.3%}) given to the default region"
        )
        if dropped:
            changed = True
        kept_zones.append((name, cells[keep]))
    if changed:
        _write_zones(mesh / "cellZones", head, kept_zones, tail)
    return "\n".join(lines) if lines else "no zone to prune"
