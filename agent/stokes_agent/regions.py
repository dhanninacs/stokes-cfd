"""A multi-region result merged back onto the whole mesh (ADR-0042 decision 6).

`splitMeshRegions` leaves the whole mesh in ``constant/polyMesh`` and writes one mesh per region
beside it; `foamMultiRun` then writes each region's fields under ``<time>/<region>/``. Every reader
the agent has (surfaces, planes, iso-surfaces, probes) reads one mesh and one set of fields, so this
module writes the fields of the whole mesh from the regions': the temperature everywhere, and the
velocity and the pressures where there is a fluid.

The place of each cell is ``constant/cellToRegion`` (the region index of every cell of the whole
mesh, written by the split), with the region names in index order given by the caller, which read
them from the split's own log. The split keeps the cells of a region in the order they have in the
whole mesh, so region cell ``k`` is the ``k``-th cell of the whole mesh with that index; the counts
are checked, and a region whose count differs is refused.

Only ASCII is read and written, and only inside the job folder. Headless: numpy and the standard
library.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .foamascii import FoamReadError, _boundary, _list_after, _numbers, _text, _uniform

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    F64 = npt.NDArray[np.float64]
    I64 = npt.NDArray[np.int64]

#: A region name: an OpenFOAM word, so it cannot be a path.
REGION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: The most regions one merge takes.
MAX_REGIONS = 64

#: The fields merged, with their width, dimensions, class, and what the cells of a region without
#: the field get: the temperature exists everywhere; a solid has no velocity (zero) and no
#: pressure (the lowest fluid value, so the colour range stays the fluids').
FIELDS: tuple[tuple[str, int, str, str, str], ...] = (
    ("T", 1, "[0 0 0 1 0 0 0]", "volScalarField", "lowest"),
    ("U", 3, "[0 1 -1 0 0 0 0]", "volVectorField", "zero"),
    ("p", 1, "[1 -1 -2 0 0 0 0]", "volScalarField", "lowest"),
    ("p_rgh", 1, "[1 -1 -2 0 0 0 0]", "volScalarField", "lowest"),
)

_TIME = re.compile(r"^\d+(\.\d+)?([eE][-+]?\d+)?$")


def merge_regions(case: Path, regions: list[str]) -> str:
    """Write the whole-mesh fields of every time folder that holds region fields.

    Returns one line per time for the step's log. Raises :class:`FoamReadError` when the case is
    not a split case or a count does not match; the caller logs it and leaves the job as it is.
    """
    import numpy as np

    if not regions or len(regions) > MAX_REGIONS:
        raise FoamReadError("merge: no region names")
    for name in regions:
        if not REGION_RE.match(name):
            raise FoamReadError(f"merge: {name!r} is not a region name")
    where = _labels(case / "constant" / "cellToRegion")
    if int(where.max(initial=-1)) >= len(regions):
        raise FoamReadError("merge: the mesh has more regions than were named")
    owner = _labels(case / "constant" / "polyMesh" / "owner")
    patches = _boundary(_text(case / "constant" / "polyMesh" / "boundary"))
    cells_of = [np.flatnonzero(where == index) for index in range(len(regions))]
    lines: list[str] = []
    for time in sorted(_times(case), key=float):
        written = []
        for name, width, dims, cls, fill in FIELDS:
            merged = _merged(case, time, name, width, regions, cells_of, len(where), fill)
            if merged is None:
                continue
            _write(case / time / name, name, cls, dims, merged, owner, patches)
            written.append(name)
        if written:
            lines.append(f"time {time}: {' '.join(written)} on {len(where)} cells")
    if not lines:
        raise FoamReadError("merge: no time folder holds region fields")
    return "\n".join(lines)


def _times(case: Path) -> list[str]:
    """The time folders that hold at least one region folder."""
    found = []
    for entry in case.iterdir():
        timed = entry.is_dir() and not entry.is_symlink() and _TIME.match(entry.name)
        if timed and any(child.is_dir() for child in entry.iterdir()):
            found.append(entry.name)
    return found


def _labels(path: Path) -> I64:
    import numpy as np

    text = _text(path)
    count, inner, _ = _list_after(text, 0, path.name)
    values = np.array(inner.split(), dtype=np.int64)
    if values.size != count:
        raise FoamReadError(f"{path.name}: {values.size} labels for a list of {count}")
    return values


def _internal(path: Path, width: int, cells: int) -> F64:
    """The internal field of one region's field file, one row per cell."""
    import numpy as np

    text = _text(path)
    m = re.search(r"\binternalField\s+(uniform|nonuniform)\s+", text)
    if m is None:
        raise FoamReadError(f"{path.name}: no internalField")
    if m.group(1) == "uniform":
        value = _uniform(text, m.end(), width, path.name)
        return np.tile(value, (cells, 1)) if width > 1 else np.full(cells, float(value[0]))
    count, inner, _ = _list_after(text, m.end(), path.name)
    if count != cells:
        raise FoamReadError(
            f"{path.parent.name}/{path.name}: {count} cell values for {cells} cells of the region"
        )
    return _numbers(inner, count, width, path.name)


def _merged(
    case: Path,
    time: str,
    name: str,
    width: int,
    regions: list[str],
    cells_of: list[Any],  # Any: one index array per region
    total: int,
    fill: str,
) -> F64 | None:
    """The whole-mesh cell values of one field, or None when no region has it at this time."""
    import numpy as np

    out = np.zeros((total, width)) if width > 1 else np.zeros(total)
    have = np.zeros(total, dtype=bool)
    for index, region in enumerate(regions):
        path = case / time / region / name
        if not path.is_file() or path.is_symlink():
            continue
        cells = cells_of[index]
        out[cells] = _internal(path, width, len(cells))
        have[cells] = True
    if not have.any():
        return None
    if fill == "lowest" and not have.all():
        out[~have] = out[have].min()
    return out


def _write(
    path: Path, name: str, cls: str, dims: str, values: F64, owner: I64, patches: Any
) -> None:
    """One whole-mesh field: the cell values, and on each patch the value of the cell behind it."""
    import numpy as np

    vector = values.ndim == 2
    kind = "vector" if vector else "scalar"

    def rows(block: F64) -> str:
        if vector:
            return "\n".join(f"({x:.8g} {y:.8g} {z:.8g})" for x, y, z in block.tolist())
        return "\n".join(f"{v:.8g}" for v in block.tolist())

    parts = [
        "FoamFile\n{\n    format      ascii;\n"
        f"    class       {cls};\n    object      {name};\n}}\n\n"
        f"dimensions      {dims};\n\n"
        f"internalField   nonuniform List<{kind}>\n{len(values)}\n(\n{rows(values)}\n)\n;\n\n"
        "boundaryField\n{\n"
    ]
    for patch in patches:
        if patch.type in ("empty", "wedge", "symmetry", "symmetryPlane"):
            parts.append(
                f"    {patch.name}\n    {{\n        type            {patch.type};\n    }}\n"
            )
            continue
        behind = values[np.asarray(owner[patch.start : patch.start + patch.size])]
        parts.append(
            f"    {patch.name}\n    {{\n        type            calculated;\n"
            f"        value           nonuniform List<{kind}>\n{len(behind)}\n"
            f"(\n{rows(behind)}\n)\n;\n"
            "    }\n"
        )
    parts.append("}\n")
    path.write_text("".join(parts), encoding="ascii", newline="\n")
