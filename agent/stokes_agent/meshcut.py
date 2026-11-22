"""The volume mesh where a section plane cuts it: the cells' own polygons and edges, by region.

What the Mesh stage's section shows. A clip plane on the boundary surfaces shows only the skin; a
mesh tool's section shows the cells the plane passes through, each outlined, and in a case of
several regions (ADR-0042) each region in its own colour. This cuts the finished mesh job's whole
mesh (`constant/polyMesh`) with the same cutter the results slice uses (`extract._cut`, polygons
rather than triangles, so the outlines are the cells' own edges and no diagonals).

**The reader is kept** for the last mesh cut: reading a million-cell polyhedral mesh is most of a
cut's cost (measured 18 s for 1.23 M cells, the cut itself under a second), a finished job's mesh
cannot change, and a user moving the plane asks for many cuts of the same mesh.

**The region of each cut polygon** comes from `0/cellToRegion`, the cell field `splitMeshRegions`
writes beside the mesh, and its names from that program's own region table in its log (the region
index is the row order). A one-region mesh has neither, and every polygon is region 0.

Answered in the reply like `vector.sample`: little-endian float32 and uint32 bytes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

#: The field `splitMeshRegions` writes: each cell's region index.
REGION_FIELD = "cellToRegion"

#: The most cut polygons answered. A plane through ten million cells cuts tens of thousands; more
#: than this is a mesh no viewer section needs every outline of, and the reply stays a few MB.
MAX_POLYGONS = 400_000

_lock = threading.Lock()
#: (case directory, mesh stamp) -> the reader, for the last mesh cut.
_held: dict[tuple[str, float], Any] = {}


def _stamp(case_dir: Path) -> float:
    """When the mesh's points were last written: a re-meshed job reads afresh."""
    for name in ("points", "points.gz"):
        path = case_dir / "constant" / "polyMesh" / name
        if path.is_file():
            return path.stat().st_mtime
    return 0.0


def _reader(case_dir: Path) -> Any:
    """The case's reader, opened once per mesh and kept for the next cut."""
    from stokes_agent import extract

    key = (str(case_dir.resolve()), _stamp(case_dir))
    with _lock:
        found = _held.get(key)
        if found is not None:
            return found
    reader = extract.open_case(case_dir, patches=False, internal=False, fields=(REGION_FIELD,))
    with _lock:
        _held.clear()
        _held[key] = reader
    return reader


def region_names(case_dir: Path) -> list[str]:
    """The regions in index order, from `splitMeshRegions`' table; [] for a one-region mesh."""
    from stokes_agent.parsers.splitregions import parse_split_regions

    log = case_dir / "log.splitMeshRegions"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")[: 1024 * 1024]
    except OSError:
        return []
    return [str(row["name"]) for row in parse_split_regions(text).get("regions", [])]


def cut_mesh(case_dir: Path, origin: list[float], normal: list[float]) -> dict[str, Any]:
    """`{positions, triangles, edges, regions, names, cells}` of the mesh at the plane.

    `positions` float32 (P, 3) bytes; `triangles` uint32 (T, 3) bytes, a fan per cut polygon;
    `edges` uint32 (E, 2) bytes, each cell outline edge once; `regions` uint8 (T,) bytes, the region
    index of each triangle; `names` the regions in index order; `cells` how many cells were cut.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkPlane

    from stokes_agent import extract

    case_dir = Path(case_dir)
    mesh = extract._internal_mesh(_reader(case_dir))
    names = region_names(case_dir)
    empty = {
        "positions": b"",
        "triangles": b"",
        "edges": b"",
        "regions": b"",
        "names": names,
        "cells": 0,
    }
    if mesh is None:
        return empty
    plane = vtkPlane()
    plane.SetOrigin(*(float(v) for v in origin))
    plane.SetNormal(*(float(v) for v in normal))
    output = extract._cut(mesh, plane)
    if output.GetPoints() is None or output.GetNumberOfCells() == 0:
        return empty
    if output.GetNumberOfCells() > MAX_POLYGONS:
        raise ValueError(
            f"the plane cuts {output.GetNumberOfCells():,} cells, more than {MAX_POLYGONS:,}"
        )
    positions = np.asarray(vtk_to_numpy(output.GetPoints().GetData()), dtype="<f4").reshape(-1, 3)
    offsets, polys = extract._poly_arrays(output)
    counts = np.diff(offsets.astype("int64"))
    triangles = extract.triangulate_fan(offsets, polys)
    edges = extract.drop_degenerate_edges(extract.unique_edges(offsets, polys))
    region_array = output.GetCellData().GetArray(REGION_FIELD)
    per_polygon = (
        np.rint(np.asarray(vtk_to_numpy(region_array), dtype="float64")).astype("int64")
        if region_array is not None
        else np.zeros(counts.size, dtype="int64")
    ).reshape(-1)[: counts.size]
    per_triangle = np.repeat(per_polygon, np.maximum(counts - 2, 0))
    return {
        "positions": positions.astype("<f4").tobytes(),
        "triangles": triangles.astype("<u4").tobytes(),
        "edges": edges.astype("<u4").tobytes(),
        "regions": np.clip(per_triangle, 0, 255).astype("uint8").tobytes(),
        "names": names,
        "cells": int(counts.size),
    }
