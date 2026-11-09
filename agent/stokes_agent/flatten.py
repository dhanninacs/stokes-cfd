"""Flatten a planar symmetry patch of a finished mesh, as far as every cell around it allows.

## Why

cfMesh's tetrahedral and hex-dominant fills leave points of a planar symmetry patch off its plane,
along the edge where a wall meets it: on the mixing elbow up to 1.2 to 2.8 mm with 5 mm cells. The
CAD face is exactly the plane z = 0 and the surface handed to the mesher holds it to the last
digit; the points are wall points that the mesher snapped to the curved wall just past its edge.
OpenFOAM 14's `symmetry` boundary condition accepts that, but the flow does not: the app's own
realizable k-epsilon case on the tetrahedral elbow died at iteration 113 with a negative
temperature, a runaway that starts in cells beside the plane, and ran its 500 iterations once the
plane was flattened (2026-11-09). `improveSymmetryPlanes` flattens the polyhedral type but
inverts cells on these two.

## What

Each patch point is moved onto the fitted plane **by as much of that move as its neighbouring cells
allow**. A full snap of the tetrahedral elbow inverts one cell; a damped one (halve the move of the
points of any cell that would be damaged, repeat, then try to win some of it back) keeps every cell
valid and leaves the few points a sliver cell holds at a fraction of the move. A cell counts as
damaged when it was fine and a face pyramid goes non-positive or its volume falls under a tenth of
what it was; a cell that was already bad must not get worse. Only the points of the patch move, and
only the lines of the ``points`` file that changed are rewritten, so everything else is byte for
byte what the mesher wrote.

Nothing is guaranteed: the polyhedral fill is the one that stays flat by itself, and a mesh with
broken cells beside the plane keeps some points off it. The mesh service measures the flatness of
what comes out (:mod:`stokes.mesh.flatness`) and warns when it is still bent.

## Limits

ASCII polyMesh only (the format cfMesh writes here), a symmetry patch that is planar to begin with
(a curved one has no plane), and at most :data:`MAX_FACES` faces. Anything else is left alone and
reported. Pure numpy, no OpenFOAM; the cost is proportional to the patch, apart from reading the
mesh.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Meshes over this many faces are not flattened: the files are read in Python, and a refusal that
#: leaves the mesh as it is costs less than minutes of parsing. (The elbow tet mesh has 566,000.)
MAX_FACES = 6_000_000
MAX_FILE_BYTES = 2 * 1024**3
#: A patch is planar when this share of its points lies within :data:`PLANAR_TOLERANCE` of its
#: extent from the plane fitted to the closest part of them; the rule of `stokes.mesh.flatness`.
PLANAR_SHARE = 0.9
PLANAR_TOLERANCE = 1e-6
#: A point this close to the plane (as a share of the patch extent) is on it.
ON_PLANE = 1e-7
SYMMETRY_TYPES = ("symmetry", "symmetryPlane")
#: Halve the move of the points of a damaged cell this many times, then give up on those points.
MAX_BACKOFF = 40
#: Rounds that try to win back part of a limited move.
ASCENT_ROUNDS = 4
VOLUME_FLOOR = 0.1
PYRAMID_FLOOR = 1e-3


class FlattenSkipped(Exception):
    """The mesh is left as it is, for a reason a person can read."""


@dataclass(frozen=True)
class FlattenReport:
    patch: str
    points: int
    off_before: int
    max_before: float
    off_after: int
    max_after: float
    moved: int
    limited: int
    seconds: float

    def text(self) -> str:
        return (
            f"flatten {self.patch}: {self.points} points, {self.off_before} off the plane by up "
            f"to {self.max_before * 1000:.3f} mm; moved {self.moved}, {self.limited} of them only "
            f"part of the way; {self.off_after} still off by up to {self.max_after * 1000:.3f} mm "
            f"({self.seconds:.1f} s)"
        )


# ----------------------------------------------------------------------------- reading the mesh
_COUNT = re.compile(r"\n(\d+)\s*\n\(\n")


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise FlattenSkipped(f"{path.name} is too large to read here")
    text = path.read_text()
    if not re.search(r"format\s+ascii\s*;", text[:1000]):
        raise FlattenSkipped(f"{path.name} is not ASCII")
    return text


def _list_body(text: str) -> tuple[re.Match[str], list[str]]:
    found = _COUNT.search(text)
    if found is None:
        raise FlattenSkipped("a mesh file has no list")
    count = int(found.group(1))
    return found, text[found.end() :].split("\n", count)[:count]


def _ints(text: str) -> np.ndarray:
    return np.array(_list_body(text)[1], dtype=np.int64)


def _patch_range(boundary: str, patch: str) -> tuple[int, int, str]:
    for match in re.finditer(r"(\w+)\s*\{([^{}]*)\}", boundary):
        if match.group(1) != patch:
            continue
        body = match.group(2)
        kind = re.search(r"\btype\s+(\w+)\s*;", body)
        count = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        start = re.search(r"\bstartFace\s+(\d+)\s*;", body)
        if kind and count and start:
            return int(start.group(1)), int(count.group(1)), kind.group(1)
        break
    raise FlattenSkipped(f"there is no patch {patch!r}")


@dataclass
class _Mesh:
    """The parts of an ASCII polyMesh this needs, and what it takes to write ``points`` back."""

    points: np.ndarray
    faces: list[np.ndarray]
    owner: np.ndarray
    neighbour: np.ndarray
    points_text: str
    points_head: re.Match[str]
    points_lines: list[str]


def _load(poly: Path) -> _Mesh:
    points_text = _read(poly / "points")
    head, lines = _list_body(points_text)
    points = np.array([[float(v) for v in line.strip("()").split()] for line in lines])
    face_lines = _list_body(_read(poly / "faces"))[1]
    if len(face_lines) > MAX_FACES:
        raise FlattenSkipped(f"{len(face_lines)} faces is over the {MAX_FACES} this will flatten")
    faces = [
        np.array(line[line.index("(") + 1 : line.rindex(")")].split(), dtype=np.int64)
        for line in face_lines
    ]
    owner = _ints(_read(poly / "owner"))
    neighbour = _ints(_read(poly / "neighbour"))
    neighbour = neighbour[neighbour >= 0]  # cfMesh pads the list to every face with -1
    if len(owner) != len(faces):
        raise FlattenSkipped("the owner list and the faces disagree")
    return _Mesh(points, faces, owner, neighbour, points_text, head, lines)


def _fit(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A plane through the points: centre, unit normal and each point's signed distance."""
    centre = points.mean(axis=0)
    normal = np.linalg.svd(points - centre, full_matrices=False)[2][-1]
    distance = np.abs((points - centre) @ normal)
    near = distance <= np.quantile(distance, PLANAR_SHARE)
    centre = points[near].mean(axis=0)
    normal = np.linalg.svd(points[near] - centre, full_matrices=False)[2][-1]
    return centre, normal, (points - centre) @ normal


# ----------------------------------------------------------------------------- the cells
class _Cells:
    """Cell volumes and face-pyramid volumes of the cells around a set of moving points.

    OpenFOAM's own measures: a face's area vector and centre from its triangle fan about the
    average of its points, a cell's centre as the average of its face centres, a pyramid as
    ``Sf . (Cf - Cc) / 3``, signed outward from the cell (inward for the neighbour).
    """

    def __init__(self, mesh: _Mesh, moving: np.ndarray) -> None:
        self.faces = mesh.faces
        n_faces, n_internal = len(mesh.faces), len(mesh.neighbour)
        touching = np.array([bool(moving[f].any()) for f in mesh.faces])
        # Every cell with a moving point, then every face of those cells: the pyramids of a cell
        # need all its faces. Faces by cell, from the two lists the mesh already has.
        cell_of = np.concatenate([mesh.owner, mesh.neighbour])
        face_of = np.concatenate([np.arange(n_faces), np.arange(n_internal)])
        cells = np.unique(cell_of[np.concatenate([touching, touching[:n_internal]])])
        keep = np.isin(cell_of, cells)
        owned = np.concatenate([np.ones(n_faces, bool), np.zeros(n_internal, bool)])
        self.cells = cells
        self.face_of = face_of[keep]
        self.is_owner = owned[keep]
        self.local = np.searchsorted(cells, cell_of[keep])
        self.unique_faces = np.unique(self.face_of)
        self.face_slot = np.searchsorted(self.unique_faces, self.face_of)

    def _face_geometry(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        count = len(self.unique_faces)
        area = np.zeros((count, 3))
        centre = np.zeros((count, 3))
        sizes = np.array([len(self.faces[f]) for f in self.unique_faces])
        for size in np.unique(sizes).tolist():
            slots = np.where(sizes == size)[0]
            ring = points[np.array([self.faces[self.unique_faces[s]] for s in slots])]
            middle = ring.mean(axis=1)
            total = np.zeros((len(slots), 3))
            weight = np.zeros(len(slots))
            weighted = np.zeros((len(slots), 3))
            for i in range(size):
                p, q = ring[:, i], ring[:, (i + 1) % size]
                triangle = 0.5 * np.cross(q - p, middle - p)
                magnitude = np.linalg.norm(triangle, axis=1)
                total += triangle
                weight += magnitude
                weighted += (p + q + middle) / 3 * magnitude[:, None]
            area[slots] = total
            safe = np.maximum(weight, 1e-300)[:, None]
            centre[slots] = np.where(weight[:, None] > 0, weighted / safe, middle)
        return area, centre

    def health(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(volume, smallest pyramid) of each cell in :attr:`cells`."""
        area, centre = self._face_geometry(points)
        ncells = len(self.cells)
        sums = np.zeros((ncells, 3))
        np.add.at(sums, self.local, centre[self.face_slot])
        middle = sums / np.bincount(self.local, minlength=ncells)[:, None]
        sign = np.where(self.is_owner, 1.0, -1.0)
        reach = centre[self.face_slot] - middle[self.local]
        pyramid = sign * np.einsum("ij,ij->i", area[self.face_slot], reach) / 3
        volume = np.zeros(ncells)
        np.add.at(volume, self.local, pyramid)
        smallest = np.full(ncells, np.inf)
        np.minimum.at(smallest, self.local, pyramid)
        return volume, smallest


# ----------------------------------------------------------------------------- the line search
class _Search:
    """How far each off-plane point may move: the share of its full move its cells allow."""

    def __init__(
        self,
        mesh: _Mesh,
        ids: np.ndarray,
        on: np.ndarray,
        distance: np.ndarray,
        normal: np.ndarray,
        movable: np.ndarray,
    ) -> None:
        self.mesh, self.normal = mesh, normal
        self.at = ids[movable]
        self.start = on[movable]
        self.distance = distance[movable]
        moving = np.zeros(len(mesh.points), bool)
        moving[self.at] = True
        self.cells = _Cells(mesh, moving)
        self.volume0, self.pyramid0 = self.cells.health(mesh.points)
        self.healthy = (self.pyramid0 > 0) & (self.volume0 > 0)
        self.slot = np.full(len(mesh.points), -1, dtype=np.int64)
        self.slot[self.at] = np.arange(len(self.at))
        self._members: dict[int, np.ndarray] = {}

    def moved(self, share: np.ndarray) -> np.ndarray:
        trial = self.mesh.points.copy()
        trial[self.at] = self.start - (share * self.distance)[:, None] * self.normal
        return trial

    def damaged(self, trial: np.ndarray) -> np.ndarray:
        volume, pyramid = self.cells.health(trial)
        worse = np.where(
            self.healthy,
            (pyramid <= PYRAMID_FLOOR * self.pyramid0) | (volume <= VOLUME_FLOOR * self.volume0),
            (pyramid < self.pyramid0 - 0.5 * np.abs(self.pyramid0))
            | (volume < self.volume0 - 0.5 * np.abs(self.volume0)),
        )
        return np.where(worse)[0]

    def points_of(self, cell: int) -> np.ndarray:
        """The moving points a cell holds, as indices into the movable points."""
        if cell not in self._members:
            used = self.cells.face_of[self.cells.local == cell]
            found = self.slot[np.unique(np.concatenate([self.mesh.faces[f] for f in used]))]
            self._members[cell] = found[found >= 0]
        return self._members[cell]

    def points_of_all(self, cells: np.ndarray) -> np.ndarray:
        if len(cells) == 0:
            return np.zeros(0, dtype=np.int64)
        return np.unique(np.concatenate([self.points_of(int(c)) for c in cells]))

    def run(self) -> np.ndarray:
        share = np.ones(len(self.at))
        for _ in range(MAX_BACKOFF):
            bad = self.damaged(self.moved(share))
            if len(bad) == 0:
                break
            for cell in bad:
                share[self.points_of(int(cell))] *= 0.5
        else:
            share[self.points_of_all(self.damaged(self.moved(share)))] = 0.0
        # Win back part of what the back-off took: try half of the rest, keep it where no cell
        # objects.
        for _ in range(ASCENT_ROUNDS):
            limited = share < 1.0
            if not limited.any():
                break
            trial = np.where(limited, share + (1.0 - share) * 0.5, share)
            refused = np.zeros(len(share), bool)
            refused[self.points_of_all(self.damaged(self.moved(trial)))] = True
            share = np.where(refused, share, trial)
        return share


def _write_points(path: Path, mesh: _Mesh, points: np.ndarray, changed: np.ndarray) -> None:
    """Rewrite ``points`` with only the changed lines replaced, atomically."""
    out = list(mesh.points_lines)
    for index in changed.tolist():
        x, y, z = points[index]
        out[index] = f"({x:.10g} {y:.10g} {z:.10g})"
    after_list = mesh.points_text[mesh.points_head.end() :].split("\n", len(out))[len(out)]
    temporary = path.with_name(f".{path.name}.flatten.tmp")
    try:
        text = mesh.points_text[: mesh.points_head.end()] + "\n".join(out) + "\n" + after_list
        temporary.write_text(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def flatten_patch(case: Path, patch: str) -> FlattenReport:
    """Flatten ``patch`` of the ASCII mesh in ``case``, or raise `FlattenSkipped`."""
    started = time.perf_counter()
    poly = case / "constant" / "polyMesh"
    first, count, kind = _patch_range(_read(poly / "boundary"), patch)
    if kind not in SYMMETRY_TYPES:
        raise FlattenSkipped(f"{patch!r} is a {kind} patch, not a symmetry plane")
    mesh = _load(poly)
    ids = np.unique(np.concatenate([mesh.faces[f] for f in range(first, first + count)]))
    on = mesh.points[ids]
    centre, normal, distance = _fit(on)
    extent = float(np.linalg.norm(on.max(axis=0) - on.min(axis=0)))
    if (
        extent <= 0
        or float(np.quantile(np.abs(distance), PLANAR_SHARE)) > PLANAR_TOLERANCE * extent
    ):
        raise FlattenSkipped(f"{patch!r} is not a plane")
    tolerance = ON_PLANE * extent
    off = np.abs(distance) > tolerance
    if not off.any():
        return FlattenReport(patch, len(ids), 0, 0.0, 0, 0.0, 0, 0, time.perf_counter() - started)

    search = _Search(mesh, ids, on, distance, normal, np.where(off)[0])
    share = search.run()
    final = search.moved(share)
    after = np.abs((final[ids] - centre) @ normal)
    changed = search.at[share > 0]
    _write_points(poly / "points", mesh, final, changed)
    return FlattenReport(
        patch,
        len(ids),
        int(off.sum()),
        float(np.abs(distance).max()),
        int((after > tolerance).sum()),
        float(after.max()),
        len(changed),
        int(((share > 0) & (share < 1)).sum()),
        time.perf_counter() - started,
    )
