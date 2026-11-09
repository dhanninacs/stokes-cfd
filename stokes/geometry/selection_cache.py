"""The session's cache of what tags derive from part meshes, and the fingerprint archive
(ADR-0020 decision 1).

:class:`SelectionCache` (``session.extensions["tag"]``) keeps per part mesh its face
fingerprints and face adjacency, computed in one batch for many parts and dropped when the store
replaces the mesh; and per B-rep version (part id, basis) the fingerprints a tag's explicit
faces were stamped with, for ``tag.rematch``.

Persistence. Re-matching needs the fingerprints of the version the ids refer to, which the mesh
cache no longer holds once a part is re-imported. Whenever a command stamps explicit faces with a
version, the cache keeps that version's fingerprints in memory; when the project is saved it
writes one archive, ``<name>.stokes/cache/geometry/fingerprints.bin`` (:func:`write_archive`),
holding exactly the versions the saved tags refer to, from memory or from the archive the
project had. One file, because thousands of small files cost a minute to create on Windows;
written at save time, because a tag that was never saved cannot be reopened anyway. The
archive lies in the project folder and is untrusted on read: size-limited and validated
(:func:`read_archive`).

Import rule: numpy only; no OCP.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import threading
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.errors import MeshFormatError, SelectionError
from stokes.geometry.fingerprints import FIELDS, Fingerprints, fingerprints_of
from stokes.geometry.mesh import PartMesh
from stokes.geometry.topology import (
    Adjacency,
    adjacency_of,
    edges_of,
    facets_of,
    split_adjacency,
    surface_of,
)
from stokes.log import fields, get_logger
from stokes.model.ids import is_id

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.model.project import Project

log = get_logger(__name__)

F64 = NDArray[np.float64]
Version = tuple[str, str]
"""(part id, basis)"""

EXTENSION = "tag"
ARCHIVE = ("cache", "geometry", "fingerprints.bin")
MAGIC = b"STKFACE1"
MAX_ARCHIVE_BYTES = 1024**3
MAX_HEADER_BYTES = 64 * 1024 * 1024
MAX_FACES = 10_000_000
_BASIS_RE = re.compile(r"^[0-9a-f]{1,32}$")


# ---------------------------------------------------------------- the archive


def archive_file(folder: Path) -> Path:
    return folder.joinpath(*ARCHIVE)


def write_archive(folder: Path, versions: Mapping[Version, Fingerprints]) -> Path:
    """Write every version in one file, atomically (temporary file, then replace)."""
    entries: list[dict[str, Any]] = []
    tables: list[F64] = []
    row = 0
    for (part, basis), prints in sorted(versions.items()):
        entries.append(
            {
                "part": part,
                "basis": basis,
                "faces": prints.faces,
                "row": row,
                "scale": prints.scale,
                "quality": prints.quality,
            }
        )
        tables.append(prints.table())
        row += prints.faces
    header = json.dumps(
        {"version": 1, "fields": list(FIELDS), "rows": row, "entries": entries},
        separators=(",", ":"),
    ).encode("utf-8")
    header += b" " * (-len(header) % 8)
    body = (np.concatenate(tables) if tables else np.zeros((0, len(FIELDS)))).astype("<f8")
    target = archive_file(folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(MAGIC + struct.pack("<I", len(header)) + header)
            handle.write(body.tobytes())
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return target


def read_archive(folder: Path) -> dict[Version, Fingerprints]:
    """The versions an archive holds; empty when it is absent or malformed (logged)."""
    path = archive_file(folder)
    try:
        size = path.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise MeshFormatError(f"{size} bytes")
        return _parse(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, MeshFormatError, ValueError, TypeError) as exc:
        log.warning("face fingerprints unreadable", extra=fields(path=str(path), error=str(exc)))
        return {}


def _parse(raw: bytes) -> dict[Version, Fingerprints]:
    if len(raw) < 12 or raw[:8] != MAGIC:
        raise MeshFormatError("not a fingerprint archive")
    (length,) = struct.unpack_from("<I", raw, 8)
    if length > MAX_HEADER_BYTES or 12 + length > len(raw):
        raise MeshFormatError("bad header length")
    header = json.loads(raw[12 : 12 + length])
    if not isinstance(header, dict) or header.get("fields") != list(FIELDS):
        raise MeshFormatError("unknown layout")
    rows, entries = header.get("rows"), header.get("entries")
    if not isinstance(rows, int) or not 0 <= rows <= MAX_FACES or not isinstance(entries, list):
        raise MeshFormatError("bad row count or entries")
    body = raw[12 + length :]
    if len(body) != rows * len(FIELDS) * 8:
        raise MeshFormatError("the table does not match the row count")
    table = np.frombuffer(body, dtype="<f8").reshape(rows, len(FIELDS)).astype(np.float64)
    if not np.isfinite(table).all():
        raise MeshFormatError("non-finite values")
    found: dict[Version, Fingerprints] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise MeshFormatError("a malformed entry")
        part, basis, faces, start, scale = (
            entry.get(k) for k in ("part", "basis", "faces", "row", "scale")
        )
        if not (
            isinstance(part, str)
            and is_id(part)
            and isinstance(basis, str)
            and _BASIS_RE.match(basis)
            and isinstance(faces, int)
            and isinstance(start, int)
            and 0 <= start <= start + faces <= rows
            and isinstance(scale, int | float)
            and math.isfinite(scale)
            and scale >= 0.0
        ):
            raise MeshFormatError("a malformed entry")
        quality = entry.get("quality")
        found[(part, basis)] = Fingerprints.from_table(
            table[start : start + faces], float(scale), quality if isinstance(quality, str) else ""
        )
    return found


def referenced(project: Project) -> set[Version]:
    """The versions the explicit members of the project's tags refer to."""
    from stokes.geometry.model import TAG_TYPE
    from stokes.geometry.selection import load_members

    needed: set[Version] = set()
    for node in project.nodes():
        if node.type != TAG_TYPE:
            continue
        raw = node.params.get("members")
        text = raw if isinstance(raw, str) else json.dumps(raw if raw is not None else [])
        try:
            members = load_members(text or "[]", where=node.path)
        except SelectionError:
            continue  # reported when the tag is used
        needed.update((m.part, m.basis) for m in members if m.faces is not None and m.basis)
    return needed


# ---------------------------------------------------------------- the session cache


@dataclass
class _Derived:
    """What was derived from one mesh object; dropped once the store replaces the mesh."""

    mesh: weakref.ref[PartMesh]
    fingerprints: Fingerprints | None = None
    adjacency: Adjacency | None = None
    """pairs in the part's own face ids"""


@dataclass
class SelectionCache:
    """Fingerprints and face adjacency per part mesh, and the fingerprints kept per B-rep
    version for re-matching. One per session, in ``session.extensions["tag"]``; it follows
    the session's project through the extension hooks (``stokes.api.session``), all called on
    the session's thread."""

    session: Session
    _derived: dict[str, _Derived] = field(default_factory=dict)
    _kept: dict[Version, Fingerprints] = field(default_factory=dict)
    _archives: dict[Path, dict[Version, Fingerprints]] = field(default_factory=dict)
    # The parsed adjacency archive per folder, with the (size, mtime) stamp it was parsed at. A
    # one-part grow reaches `_derive` on every call and parsing the whole archive costs about 90 ms
    # at 4,500 entries, so an unchanged file is not parsed again.
    _adjacency_archives: dict[
        Path, tuple[tuple[int, int] | None, dict[str, tuple[Adjacency, int, int]]]
    ] = field(default_factory=dict)

    @classmethod
    def of(cls, session: Session) -> SelectionCache:
        with _cache_lock:
            found = session.extensions.get(EXTENSION)
            if not isinstance(found, SelectionCache):
                found = cls(session)
                session.extensions[EXTENSION] = found
            return found

    # ------------------------------------------------------------ derived data
    def fingerprints(self, meshes: Mapping[str, PartMesh]) -> dict[str, Fingerprints]:
        """Fingerprints of each part's current mesh (computed in one batch when missing)."""
        self._derive(meshes, adjacency=False)
        result: dict[str, Fingerprints] = {}
        for part, mesh in meshes.items():
            prints = self._entry(part, mesh).fingerprints
            assert prints is not None  # derived just above
            result[part] = prints
        return result

    def adjacency(self, meshes: Mapping[str, PartMesh]) -> dict[str, Adjacency]:
        """Face adjacency of each part's current mesh, in the part's own face ids."""
        self._derive(meshes, adjacency=True)
        result: dict[str, Adjacency] = {}
        for part, mesh in meshes.items():
            found = self._entry(part, mesh).adjacency
            assert found is not None  # derived just above
            result[part] = found
        return result

    def _entry(self, part: str, mesh: PartMesh) -> _Derived:
        entry = self._derived.get(part)
        if entry is None or entry.mesh() is not mesh:
            entry = _Derived(weakref.ref(mesh))
            self._derived[part] = entry
        return entry

    def _derive(self, meshes: Mapping[str, PartMesh], *, adjacency: bool) -> None:
        missing = [
            (part, mesh)
            for part, mesh in meshes.items()
            if (entry := self._entry(part, mesh)).fingerprints is None
            or (adjacency and entry.adjacency is None)
        ]
        if not missing:
            return
        cached: dict[str, Adjacency] = {}
        if adjacency and self.session.project_path is not None:
            archive = self._adjacency_archive(self.session.project_path)
            for part, mesh in missing:
                found = archive.get(part)
                if found is not None and found[1] == mesh.faces and found[2] == mesh.triangles:
                    cached[part] = found[0]
        surface = surface_of([mesh for _, mesh in missing])
        facets = facets_of(surface)
        prints = fingerprints_of(surface, facets, [_quality(mesh) for _, mesh in missing])
        # The edge sort is what adjacency costs: run it only when a part has no file of its own.
        split = (
            split_adjacency(surface, adjacency_of(surface, facets, edges_of(surface)))
            if adjacency and len(cached) < len(missing)
            else []
        )
        for index, (part, mesh) in enumerate(missing):
            entry = self._entry(part, mesh)
            entry.fingerprints = prints[index]
            if adjacency:
                # The import worker wrote the adjacency for this mesh; build it only when absent.
                entry.adjacency = cached[part] if part in cached else split[index]

    # ------------------------------------------------------------ kept versions
    def keep(self, versions: Mapping[Version, Fingerprints]) -> None:
        """Keep versions' fingerprints for re-matching (a coarse one gives way to a fine one)."""
        for key, prints in versions.items():
            held = self._kept.get(key)
            if held is None or (prints.quality == "fine" and held.quality != "fine"):
                self._kept[key] = prints

    def kept(self, part: str, basis: str) -> Fingerprints | None:
        """The fingerprints a version had when a tag took its faces: from memory, else
        from the archive of the project's folder."""
        found = self._kept.get((part, basis))
        if found is None and self.session.project_path is not None:
            found = self._archive(self.session.project_path).get((part, basis))
        return found

    def _archive(self, folder: Path) -> dict[Version, Fingerprints]:
        if folder not in self._archives:
            self._archives[folder] = read_archive(folder)
        return self._archives[folder]

    def _adjacency_archive(self, folder: Path) -> dict[str, tuple[Adjacency, int, int]]:
        """The adjacency entries of the folder's archive, parsed once per file version.

        The ``(size, mtime_ns)`` stamp decides whether the cached parse still fits, so a grow
        that finds only new parts does not read and parse the whole archive again."""
        from stokes.geometry.adjacency_cache import archive_file as adjacency_file
        from stokes.geometry.adjacency_cache import read_archive

        try:
            stat = adjacency_file(folder).stat()
            stamp: tuple[int, int] | None = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            stamp = None
        held = self._adjacency_archives.get(folder)
        if held is not None and held[0] == stamp:
            return held[1]
        entries = read_archive(folder)
        self._adjacency_archives[folder] = (stamp, entries)
        return entries

    # ------------------------------------------------------------ session hooks
    def project_opened(self, project: Project, path: Path | None) -> None:
        self._forget()

    def project_closed(self) -> None:
        self._forget()

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        """Write the archive: every version the saved tags refer to, from memory or from
        the archive of the folder the project came from (Save as) or is saved in."""
        needed = referenced(project)
        folders = dict.fromkeys(f for f in (previous, path) if f is not None)
        sources = [self._kept, *(self._archive(f) for f in folders)]
        versions: dict[Version, Fingerprints] = {}
        for key in needed:
            found = next((s[key] for s in sources if key in s), None)
            if found is not None:
                versions[key] = found
        if not versions and not archive_file(path).exists():
            return
        try:
            write_archive(path, versions)
        except OSError as exc:
            log.warning("face fingerprints not written", extra=fields(error=str(exc)))
            return
        self._archives[path] = versions

    def project_copied(self, project: Project, path: Path) -> None:
        """Session hook: write the archive into a COPY, leaving the session in the original.

        **A copy gets the same file a save does**, so a reopened copy can resolve its tags. The
        difference is only that the session has not moved, which is why this is not
        `project_saved` (Opus F3)."""
        self.project_saved(project, path, None)

    def _forget(self) -> None:
        self._derived.clear()
        self._kept.clear()
        self._archives.clear()
        self._adjacency_archives.clear()


_cache_lock = threading.Lock()


def _quality(mesh: PartMesh) -> str:
    return mesh.solids[0].quality if mesh.solids else ""
