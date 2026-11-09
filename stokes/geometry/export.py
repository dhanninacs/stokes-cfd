"""STL export of selections and parts (ADR-0020 decision 7): one STL solid per selection or part,
in metres, named for OpenFOAM, for snappyHexMesh (``constant/triSurface``).

Where. ``path`` is a file (``*.stl``) or a folder. A file holds every solid: as ASCII, one
``solid <name>`` block each, which snappyHexMesh reads as named regions; as binary, one solid only,
because binary STL carries no names (OpenFOAM reads the attribute bytes as region numbers). A
folder receives ``<name>.stl`` per solid in either format. The target must resolve inside the
project folder or inside the export folder the caller names (docs/engineering/security.md:
resolve, then check containment; a symbolic link that leads out is refused), and only ``.stl``
files are ever written.

What. A solid's triangles are its members' triangles whose face id is selected, in member order,
then in mesh order, with facet normals from the winding (outward on a solid, as imported). The
bytes depend on nothing but the meshes and the members, so a replayed journal writes the same
file.

Import rule: numpy only.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.errors import ValidationError
from stokes.geometry.mesh import PartMesh

F32 = NDArray[np.float32]
I64 = NDArray[np.int64]

SUFFIX = ".stl"
MAX_NAME = 64

#: The largest ASCII STL the agent will stage, in bytes.
#:
#: **Mirrors `agent/stokes_agent/lint.py`'s `MAX_STL_BYTES`**, and it is a mirror rather than an
#: import because the app must not depend on the agent package - the two are separate distributions
#: that talk over the protocol. A surface over this cannot be staged, so the mesh writer refuses it
#: HERE, where the message can name the surface and its size, instead of letting the agent reject
#: the case with a generic staging error.
#:
#: **500 MiB is the owner's decision**, and it is the ONE staged file allowed past the agent's
#: 8 MiB scan limit: that limit bounds what the agent will READ, and a staged STL is measured by
#: `stat` and checked by sampling, so a half-gigabyte surface costs the agent about a megabyte.
#: An ASCII STL is ~260 bytes per triangle, so this is about 2 million triangles; the largest part
#: that fits is one whose tessellation is coarser than that or whose file is reduced first.
MAX_STAGED_BYTES = 500 * 1024 * 1024
_WORD_JUNK = re.compile(r"[^A-Za-z0-9_]+")
_RECORD = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])
_FACET = (
    "  facet normal %.8e %.8e %.8e\n"
    "    outer loop\n"
    "      vertex %.8e %.8e %.8e\n"
    "      vertex %.8e %.8e %.8e\n"
    "      vertex %.8e %.8e %.8e\n"
    "    endloop\n"
    "  endfacet\n"
)
_CHUNK = 20_000


@dataclass(frozen=True)
class Solid:
    """One STL solid: its name and triangle corners (T, 3, 3) in metres."""

    name: str
    corners: F32
    faces: int = 0

    @property
    def triangles(self) -> int:
        return int(self.corners.shape[0])


def solid_name(text: str, taken: set[str]) -> str:
    """An OpenFOAM word from a node name: letters, digits and underscores, no leading digit,
    unique in ``taken`` ignoring case (file names on Windows and macOS ignore it); the lower-case
    form is added to ``taken``."""
    base = re.sub(r"_+", "_", _WORD_JUNK.sub("_", text)).strip("_")[:MAX_NAME] or "solid"
    if base[0].isdigit():
        base = f"s_{base}"[:MAX_NAME]
    name, index = base, 2
    while name.lower() in taken:
        name, index = f"{base}_{index}", index + 1
    taken.add(name.lower())
    return name


def corners_of(mesh: PartMesh, faces: I64 | None) -> F32:
    """The corners of a part's triangles whose face id is in ``faces`` (every one for None)."""
    chunks: list[F32] = []
    for solid in mesh.solids:
        indices = solid.indices
        if faces is not None:
            if solid.face_ids is None:
                continue
            indices = indices[np.isin(solid.face_ids, faces)]
        chunks.append(solid.positions[indices])
    if not chunks:
        return np.zeros((0, 3, 3), dtype=np.float32)
    return np.ascontiguousarray(np.concatenate(chunks), dtype=np.float32)


def facet_normals(corners: F32) -> F32:
    """Unit normals following the winding; zero for a triangle without area."""
    p = corners.astype(np.float64)
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    length = np.linalg.norm(cross, axis=1)
    normal = np.zeros_like(cross)
    np.divide(cross, length[:, None], out=normal, where=length[:, None] > 0.0)
    return normal.astype(np.float32)


# ---------------------------------------------------------------- where to write


def export_root(project_folder: Path | None, folder: str | None) -> Path:
    """The folder exports must stay inside: the one the caller chose, else the project's."""
    if folder is not None:
        _plain(folder)
        try:
            root = Path(folder).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationError(
                f"the export folder {folder} does not exist", details={"folder": folder}
            ) from exc
        if not root.is_dir():
            raise ValidationError(f"{root} is not a folder", details={"folder": folder})
        return root
    if project_folder is None:
        raise ValidationError(
            "the project has no folder yet: save it first, or pass the export folder the user chose"
        )
    return project_folder.resolve()


def contained(raw: str, root: Path) -> Path:
    """``raw`` (absolute, or relative to ``root``) resolved, and inside ``root``."""
    _plain(raw)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        target = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValidationError(f"cannot use {raw}: {exc}", details={"path": raw}) from exc
    if not target.is_relative_to(root):
        raise ValidationError(
            f"{target} lies outside {root}; export inside the project folder or the export "
            "folder you chose",
            details={"path": str(target), "root": str(root)},
        )
    return target


def _plain(text: str) -> None:
    """Refuse control characters in a path: a NUL byte passes some resolvers and fails on open."""
    if any(ord(c) < 32 for c in text):
        raise ValidationError(
            "a path may not contain control characters", details={"path": repr(text)}
        )


# ---------------------------------------------------------------- writing


@dataclass(frozen=True)
class Written:
    path: Path
    solids: tuple[Solid, ...]
    size: int

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "path": str(self.path),
            "bytes": self.size,
            "solids": [
                {"name": s.name, "triangles": s.triangles, "faces": s.faces} for s in self.solids
            ],
        }


def write_stl(target: Path, root: Path, solids: Sequence[Solid], *, binary: bool) -> list[Written]:
    """Write ``solids`` to the file or folder ``target`` (already contained in ``root``)."""
    if not solids:
        raise ValidationError("nothing to export: no selection or part was given")
    as_file = target.suffix.lower() == SUFFIX and not target.is_dir()
    if not as_file and target.exists() and not target.is_dir():
        raise ValidationError(
            f"{target} exists and is not an .stl file or a folder", details={"path": str(target)}
        )
    if as_file:
        if binary and len(solids) > 1:
            raise ValidationError(
                f"a binary STL file holds one solid and {len(solids)} were asked for: give a "
                "folder (one file per solid), or binary false (one ASCII file of named solids)",
                details={"path": str(target), "solids": len(solids)},
            )
        return [_write(target, solids, binary)]
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for solid in solids:
        file = contained(str(target / f"{solid.name}{SUFFIX}"), root)
        written.append(_write(file, [solid], binary))
    return written


def _write(path: Path, solids: Sequence[Solid], binary: bool) -> Written:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("wb") as handle:
            if binary:
                _binary(handle, solids[0])
            else:
                for solid in solids:
                    _ascii(handle, solid)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return Written(path, tuple(solids), path.stat().st_size)


def _binary(handle: Any, solid: Solid) -> None:  # Any: a binary file object
    header = f"Stokes STL {solid.name} (metres)".encode("ascii")[:80].ljust(80, b" ")
    records = np.zeros(solid.triangles, dtype=_RECORD)
    records["normal"] = facet_normals(solid.corners)
    records["corners"] = solid.corners
    handle.write(header)
    handle.write(np.uint32(solid.triangles).astype("<u4").tobytes())
    handle.write(records.tobytes())


def ascii_stl_text(solid: Solid) -> str:
    """One solid as ASCII STL text, **in memory**.

    **Why not `write_stl` into a temporary file**: a snappy job's surfaces are staged as part of the
    case tree (`job.create(files=...)`), which is a mapping of relative path to text, so the bytes
    are needed here rather than on disk. Rendering with the same `_FACET` format and the same
    `facet_normals` keeps a staged surface byte-identical to one the user exports with
    `geometry.export_stl`, which matters because the fidelity measurement compares the mesh against
    the STAGED surface.

    **ASCII, not binary**: the agent refuses to stage a binary file (its linter says "a binary file;
    only text may be staged") and caps a staged text file at 8 MiB, so the caller must check the
    size. :func:`ascii_stl_size` estimates it before the text is built, for exactly that check.
    """
    parts = [f"solid {solid.name}\n"]
    rows = np.concatenate(
        [facet_normals(solid.corners), solid.corners.reshape(-1, 9)], axis=1
    ).astype(np.float64)
    for start in range(0, rows.shape[0], _CHUNK):
        chunk = rows[start : start + _CHUNK]
        parts.append((_FACET * chunk.shape[0]) % tuple(chunk.ravel().tolist()))
    parts.append(f"endsolid {solid.name}\n")
    return "".join(parts)


def ascii_stl_size(triangles: int) -> int:
    """The byte size of the ASCII STL :func:`ascii_stl_text` would produce for `triangles`.

    **Counted rather than measured**, so a caller can refuse an oversized surface BEFORE building a
    string that would be rejected by the agent's 8 MiB staging limit. Each facet is the fixed
    `_FACET` template with 12 floats; `%e` renders each as at most 13 characters, so the estimate is
    an upper bound and a surface that passes it cannot be refused for size.
    """
    per_facet = len(_FACET) + 12 * 13
    return 64 + triangles * per_facet


def _ascii(handle: Any, solid: Solid) -> None:  # Any: a binary file object
    handle.write(f"solid {solid.name}\n".encode("ascii"))
    rows = np.concatenate(
        [facet_normals(solid.corners), solid.corners.reshape(-1, 9)], axis=1
    ).astype(np.float64)
    for start in range(0, rows.shape[0], _CHUNK):
        chunk = rows[start : start + _CHUNK]
        handle.write(((_FACET * chunk.shape[0]) % tuple(chunk.ravel().tolist())).encode("ascii"))
    handle.write(f"endsolid {solid.name}\n".encode("ascii"))
