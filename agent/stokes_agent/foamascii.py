"""A checked reader of ASCII OpenFOAM meshes and fields, for what Stokes computes from a case.

The adjoint wall sensitivity (ADR-0038 decision 3) needs the wall faces' geometry, their owner
cells and the cell values of U and Ua next to the wall. The STKFLD1 artifacts carry point fields on
patches, which cannot give a wall-normal derivative, so this module reads the case itself:

- ``constant/polyMesh``: ``points``, ``faces``, ``owner`` and ``boundary``;
- a ``volScalarField`` or ``volVectorField`` of a time directory: its internal field and, per
  patch, the face values (``value`` entries, ``uniform`` or ``nonuniform``).

Only ``format ascii`` is read; a binary file is refused, as is any file over :data:`MAX_BYTES`.
Every count is checked against what the list holds. The case is untrusted input (CLAUDE.md §4):
a malformed file is a :class:`FoamReadError`, never a crash or a silently short array.

It lives in the agent because the case lives where the agent ran it (CLAUDE.md §8: each side
owns its disk); the desktop asks for the result through ``job.sensitivity``.

Headless: numpy and the standard library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from stokes.errors import StokesError

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    F64 = npt.NDArray[np.float64]
    I64 = npt.NDArray[np.int64]

MAX_BYTES = 1 << 30
"""The largest file read (1 GiB of ASCII is about 25 M vectors)."""

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


class FoamReadError(StokesError):
    """A case file that is missing, binary, too large or not what its header says."""

    code = "run.foam_read"


def _text(path: Path) -> str:
    if not path.is_file():
        raise FoamReadError(f"{path.name} is not in the case", details={"file": str(path)})
    if path.stat().st_size > MAX_BYTES:
        raise FoamReadError(f"{path.name} is larger than {MAX_BYTES >> 20} MiB")
    text = path.read_text(encoding="utf-8", errors="replace")
    header = re.search(r"FoamFile\s*\{(.*?)\}", text, re.DOTALL)
    if header and re.search(r"\bformat\s+binary\s*;", header.group(1)):
        raise FoamReadError(
            f"{path.name} is binary: write the case with writeFormat ascii",
            details={"file": str(path)},
        )
    body = text[header.end() :] if header else text
    return _COMMENT.sub(" ", body)


def _list_after(text: str, start: int, what: str) -> tuple[int, str, int]:
    """The ``N ( ... )`` list at or after ``start``: (count, the text inside, end index)."""
    m = re.compile(r"(\d+)\s*\(").search(text, start)
    if m is None:
        raise FoamReadError(f"{what}: no list")
    count = int(m.group(1))
    depth = 0
    i = m.end() - 1
    for j in range(i, len(text)):
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return count, text[m.end() : j], j + 1
    raise FoamReadError(f"{what}: the list is not closed")


def _numbers(inner: str, count: int, width: int, what: str) -> F64:
    import numpy as np

    flat = np.array(inner.replace("(", " ").replace(")", " ").split(), dtype=np.float64)
    if flat.size != count * width:
        raise FoamReadError(
            f"{what}: {flat.size} numbers for {count} entries of {width}",
            details={"count": count},
        )
    return flat.reshape(count, width) if width > 1 else flat


# ------------------------------------------------------------------------------- the mesh
@dataclass(frozen=True)
class Patch:
    name: str
    type: str
    start: int
    size: int


@dataclass(frozen=True)
class PolyMesh:
    points: F64
    faces: list[list[int]]
    owner: I64
    neighbour: I64
    """The other cell of each internal face (internal faces come first)."""
    patches: tuple[Patch, ...]

    @property
    def cells(self) -> int:
        # A cell may appear only as a neighbour (the last cell often does): count both lists.
        top = max(
            int(self.owner.max()) if len(self.owner) else -1,
            int(self.neighbour.max()) if len(self.neighbour) else -1,
        )
        return top + 1

    def patch(self, name: str) -> Patch:
        for p in self.patches:
            if p.name == name:
                return p
        raise FoamReadError(f"the mesh has no patch {name!r}", details={"patch": name})


def read_mesh(case: Path) -> PolyMesh:
    """``constant/polyMesh`` of ``case``."""
    import numpy as np

    folder = case / "constant" / "polyMesh"
    text = _text(folder / "points")
    count, inner, _ = _list_after(text, 0, "points")
    points = _numbers(inner, count, 3, "points")
    text = _text(folder / "faces")
    count, inner, _ = _list_after(text, 0, "faces")
    faces = [
        [int(v) for v in body.split()] for _, body in re.findall(r"(\d+)\s*\(([^()]*)\)", inner)
    ]
    if len(faces) != count:
        raise FoamReadError(f"faces: {len(faces)} faces for a count of {count}")
    text = _text(folder / "owner")
    count, inner, _ = _list_after(text, 0, "owner")
    owner = np.array(inner.split(), dtype=np.int64)
    if len(owner) != count:
        raise FoamReadError(f"owner: {len(owner)} entries for a count of {count}")
    text = _text(folder / "neighbour")
    count, inner, _ = _list_after(text, 0, "neighbour")
    neighbour = np.array(inner.split(), dtype=np.int64)
    if len(neighbour) != count or count > len(owner):
        raise FoamReadError(f"neighbour: {len(neighbour)} entries for a count of {count}")
    patches = _boundary(_text(folder / "boundary"))
    for p in patches:
        if p.start < 0 or p.start + p.size > len(faces) or p.start + p.size > len(owner):
            raise FoamReadError(f"patch {p.name} reaches past the faces", details={"patch": p.name})
    if len(points) and any(min(f) < 0 or max(f) >= len(points) for f in faces if f):
        raise FoamReadError("a face names a point that is not in the mesh")
    return PolyMesh(points, faces, owner, neighbour, patches)


def _boundary(text: str) -> tuple[Patch, ...]:
    _, inner, _ = _list_after(text, 0, "boundary")
    out: list[Patch] = []
    for name, body in re.findall(r"([A-Za-z_][\w.:-]*)\s*\{([^{}]*)\}", inner):
        kind = re.search(r"\btype\s+(\w+)\s*;", body)
        n = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        start = re.search(r"\bstartFace\s+(\d+)\s*;", body)
        if not (kind and n and start):
            raise FoamReadError(f"boundary: patch {name} lacks type, nFaces or startFace")
        out.append(Patch(name, kind.group(1), int(start.group(1)), int(n.group(1))))
    return tuple(out)


# ------------------------------------------------------------------------------- fields
@dataclass(frozen=True)
class VolField:
    """A cell field and, per patch, its face values (None where the patch stores none)."""

    name: str
    width: int
    internal: F64
    patches: dict[str, F64 | None]
    types: dict[str, str]


def read_field(case: Path, time: str, name: str, mesh: PolyMesh) -> VolField:
    """``<case>/<time>/<name>``, a scalar or vector volume field."""
    import numpy as np

    text = _text(case / time / name)
    cells = mesh.cells
    m = re.search(r"\binternalField\s+(uniform|nonuniform)\s+", text)
    if m is None:
        raise FoamReadError(f"{name}: no internalField")
    # The header is gone (`_text`): the width is read from the internal field itself.
    ahead = text[m.end() : m.end() + 64].lstrip()
    width = 3 if ahead.startswith(("(", "List<vector>")) else 1
    if m.group(1) == "uniform":
        value = _uniform(text, m.end(), width, name)
        internal = np.tile(value, (cells, 1)) if width > 1 else np.full(cells, float(value[0]))
        after = m.end()
    else:
        count, inner, after = _list_after(text, m.end(), f"{name} internalField")
        if count != cells:
            raise FoamReadError(f"{name}: {count} cell values for {cells} cells")
        internal = _numbers(inner, count, width, f"{name} internalField")
    boundary = text.find("boundaryField", after)
    patches: dict[str, F64 | None] = {}
    types: dict[str, str] = {}
    if boundary >= 0:
        for patch in mesh.patches:
            pm = re.compile(r"\b" + re.escape(patch.name) + r"\s*\{").search(text, boundary)
            if pm is None:
                continue
            end = _block_end(text, pm.end() - 1)
            body = text[pm.end() : end]
            kind = re.search(r"\btype\s+(\w+)\s*;", body)
            types[patch.name] = kind.group(1) if kind else ""
            patches[patch.name] = _patch_values(body, patch.size, width, f"{name}.{patch.name}")
    return VolField(name, width, internal, patches, types)


def read_patch_flux(case: Path, time: str, mesh: PolyMesh, name: str = "phi") -> dict[str, F64]:
    """The face values of a ``surfaceScalarField`` (the volumetric flux ``phi``, m³/s through each
    face, positive out of the domain on a boundary) on every patch that stores them. Only the
    boundary is read: the internal field is one value per internal face and is not needed."""
    text = _text(case / time / name)
    boundary = text.find("boundaryField")
    if boundary < 0:
        raise FoamReadError(f"{name}: no boundaryField")
    out: dict[str, F64] = {}
    for patch in mesh.patches:
        pm = re.compile(r"\b" + re.escape(patch.name) + r"\s*\{").search(text, boundary)
        if pm is None:
            continue
        end = _block_end(text, pm.end() - 1)
        values = _patch_values(text[pm.end() : end], patch.size, 1, f"{name}.{patch.name}")
        if values is not None:
            out[patch.name] = values
    return out


def _block_end(text: str, open_brace: int) -> int:
    depth = 0
    for j in range(open_brace, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    raise FoamReadError("a patch entry is not closed")


def _uniform(text: str, at: int, width: int, what: str) -> F64:
    import numpy as np

    if width == 1:
        m = re.compile(r"([-+0-9.eE]+)").match(text, at)
        if m is None:
            raise FoamReadError(f"{what}: a uniform value is not a number")
        return np.array([float(m.group(1))])
    m = re.compile(r"\(([^()]*)\)").match(text, at)
    if m is None:
        raise FoamReadError(f"{what}: a uniform vector is not (x y z)")
    found: F64 = _numbers(m.group(1), 1, 3, what)[0]
    return found


def _patch_values(body: str, size: int, width: int, what: str) -> F64 | None:
    import numpy as np

    m = re.search(r"\bvalue\s+(uniform|nonuniform)\s+", body)
    if m is None:
        return None
    if m.group(1) == "uniform":
        value = _uniform(body, m.end(), width, what)
        return np.tile(value, (size, 1)) if width > 1 else np.full(size, float(value[0]))
    count, inner, _ = _list_after(body, m.end(), what)
    if count != size:
        raise FoamReadError(f"{what}: {count} face values for {size} faces")
    return _numbers(inner, count, width, what)
