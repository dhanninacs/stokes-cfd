"""Per-face fingerprints, B-rep versions and re-matching (ADR-0020 decision 1).

A fingerprint describes a B-rep face through its tessellation: area, centroid, area-weighted mean
facet normal (length 1 on a plane, near 0 on a closed face such as a whole cylinder) and triangle
count. The surface type (plane, cylinder ...) needs the B-rep and comes with a later slice.

Basis. Face ids are stable while a part's B-rep is unchanged (ADR-0014). :func:`basis_of` names
the B-rep a mesh was made from: the part's ``basis`` parameter when it has one (ADR-0021 decision
8.4: the import writes it, and an operation writes its own on the parts it makes), else a hash of
its source file stamp (name, size, modification time), the solid's index in the file and the face
count (:func:`basis_for`, the same value the import writes). The coarse and fine passes of one
import share a basis; a re-import after the file changed gets a new one. Members with explicit
faces carry the basis their ids refer to; a member whose basis differs from its part's current
one is stale, and
``tag.rematch`` maps its faces onto the new numbering with :func:`match_faces`.

Re-matching needs the fingerprints of the version the ids refer to: the session's cache keeps
them, in memory and in the project's fingerprint archive (:mod:`stokes.geometry.selection_cache`).

Matching. Each old face is compared with the new faces whose centroids lie nearest (eight at
most) through a distance that adds the centroid shift as a fraction of the part's size, the
relative change of area and the change of mean normal, each divided by its tolerance
(:data:`TOLERANCES`). Old faces take their nearest free candidate in order of best distance (one
new face per old face). The confidence is ``exp(-d^2 / 2) * (1 - d / d_alt)``: high for a close
match, near zero when another candidate is almost as close (a pattern of equal faces), so an
ambiguous face is reported unmatched rather than guessed.

Import rule: numpy at module level; scipy inside :func:`match_faces`; no OCP.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from stokes.geometry.files import SourceStamp
from stokes.geometry.mesh import PartMesh
from stokes.geometry.topology import (
    Facets,
    Surface,
    part_face_count,
)

if TYPE_CHECKING:
    from stokes.model.nodes import Node

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]

FIELDS: tuple[str, ...] = ("area", "cx", "cy", "cz", "nx", "ny", "nz", "triangles")
CANDIDATES = 8
TOLERANCES = {"centroid": 0.03, "area": 0.1, "normal": 0.15}
"""Centroid shift as a fraction of the part's bounding-box diagonal, relative area change, and
half the length of the mean-normal difference, each at distance 1."""
DEFAULT_CONFIDENCE = 0.5
_TINY = 1e-30


@dataclass(frozen=True)
class Fingerprints:
    """The faces of one part version, indexed by face id."""

    area: F64
    centroid: F64
    normal: F64
    triangles: I64
    scale: float
    """bounding-box diagonal of the part, metres"""
    quality: str = ""

    @property
    def faces(self) -> int:
        return int(self.area.shape[0])

    def table(self) -> F64:
        """(faces, 8) in :data:`FIELDS` order."""
        return np.column_stack(
            [self.area, self.centroid, self.normal, self.triangles.astype(np.float64)]
        )

    @classmethod
    def from_table(cls, table: F64, scale: float, quality: str = "") -> Fingerprints:
        return cls(
            np.ascontiguousarray(table[:, 0]),
            np.ascontiguousarray(table[:, 1:4]),
            np.ascontiguousarray(table[:, 4:7]),
            table[:, 7].round().astype(np.int64),
            scale,
            quality,
        )

    def face(self, face: int) -> dict[str, Any]:  # Any: JSON
        """One face's fingerprint as JSON (SI units)."""
        return {
            "area": float(self.area[face]),
            "centroid": [float(v) for v in self.centroid[face]],
            "normal": [float(v) for v in self.normal[face]],
            "triangles": int(self.triangles[face]),
        }


def basis_of(mesh: PartMesh, solid: int, node: Node | None = None) -> str | None:
    """The B-rep version of a part's mesh: the ``basis`` of the part ``node`` when it holds one,
    else the hash of the mesh's source stamp (projects saved before ``basis`` existed); None for
    a mesh with neither."""
    if node is not None:
        stored = node.params.get("basis")
        if isinstance(stored, str) and stored:
            return stored
    stamp = mesh.source
    if stamp is None:
        return None
    # **No units here.** `mesh.units` is the DISPLAY unit — metres for a mesh whose positions are
    # already converted (`PartMesh` defaults to `"m"`) — not the source unit the basis is keyed by.
    # Passing it made this legacy path hash a literal `"m"` and disagree with the basis the import
    # wrote, which is what the `test_the_import_caches_every_solid_at_its_parts_version` failure
    # was.
    # The source's own units are resolved where the source is known (`basis_for_source`).
    return basis_for(stamp, solid, part_face_count(mesh))


def basis_for(stamp: SourceStamp, solid: int, faces: int, units: str | None = None) -> str:
    """The version of solid ``solid``, of ``faces`` faces, in the source file ``stamp`` describes:
    what :func:`basis_of` gives its mesh. The store writes it on the part when the file is read
    and the import worker records it beside the B-rep it caches, so the two agree.

    **``units`` is part of the identity for the formats whose units come from the import parameter**
    (``formats.py``: BREP, STL and OBJ declare ``units="param"``; STEP, IGES and 3MF declare them in
    the file). Claude's decision (2026-10-12), after I established that an existing part's units CAN
    be changed — `parameter.set node=… name=units value=inch` is accepted and takes effect:

    > for formats whose units come from the import parameter, the units are part of the identity
    > of the geometry, so they belong in the basis. For STEP/IGES/3MF they come from the file bytes
    > and already change the basis, so leave those alone.

    He is right that the hazard is wider than topology: `cache._fresh_against` compares the source
    stamp for the **mesh** too, so a units change served the old-scale mesh as well, and a client
    that trusted the record's `scale` still drew the part at the wrong size.

    A `None` means "the caller does not know the units" and hashes exactly as before, so a caller
    that cannot say (a legacy project with no `units` param) keeps its old basis rather than missing
    every cache on upgrade.

    **The units join the hash only when they are NOT the format's default.** That is the whole
    compatibility rule (Claude's review, 2026-10-13): hashing the default too would change the basis
    of every BREP, STL and OBJ part ever imported, because those projects all carry an explicit
    `units: "mm"` — the import always writes one, so "no units" is a state that only pre-`units`
    projects are in. Every such part would then fail with "changed since it was imported" and every
    tag stamped against the old basis would go stale: an upgrade that breaks the user's project. The
    units are only *news* when they are not what the reader would have used anyway, which is exactly
    when they change the geometry the basis is naming.

    The comparison is on the **scale**, not the text, so `mm`, `millimetre` and `millimeter` are one
    default rather than three hashes for one geometry.
    """
    parts = [stamp.name, str(stamp.size), str(stamp.mtime_ns), str(solid), str(faces)]
    if units and not is_default_units(units):
        # **The text the caller typed, NOT its canonical spelling** (defect 3 of the second review).
        #
        # The basis is **stored** in the node at import and stamped into every tag made from it. So
        # whatever goes into this hash becomes a fact on disk, and changing what goes in renames the
        # geometry of every part already there: `inch`, `meter` and `metre` now hashed as `in`, `m`
        # and `m`, so those parts read as "changed since it was imported" and every tag stamped
        # against the old value went stale — an upgrade that breaks the user's project, for a fix
        # that
        # was supposed to stop a re-import.
        #
        # The aliases still agree, because the comparison is made elsewhere: `basis_matches`
        # compares
        # `canonical_units` of both sides, so `in` and `inch` are one basis **when deciding
        # staleness**
        # without either one's stored value moving. Canonicalise for comparison, never for the hash.
        parts.append(units)
    return hashlib.sha1("\0".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def canonical_units(units: str) -> str:
    """The one spelling of ``units``, so aliases of a unit hash alike.

    Returns the text unchanged when it names no length unit this can resolve: a name that cannot be
    placed is not canonicalised into something else, and `basis_for` has already decided it is not
    the
    default, so the geometry is still distinguished. Callers that need to know whether it is a real
    unit use `unit_scale`, which raises.
    """
    from stokes.units.registry import ureg

    try:
        return str(ureg().Quantity(1.0, units).units)
    except Exception:
        return units


def basis_for_comparison(
    stamp: SourceStamp,
    solid: int,
    faces: int,
    units: str | None,
) -> str:
    """The basis a caller should COMPARE against a stored one: aliases of a unit canonicalised.

    **Why this is a second function and not a change to `basis_for`** (defect 3 of the second
    review).
    A stored basis is a fact on disk — written at import, stamped into every tag — so what goes into
    it
    cannot change without renaming the geometry of every part already there. `basis_for` therefore
    hashes the units **text as typed**, and `inch` keeps the value it has always had.

    But `in` and `inch` are one unit, and a part imported as one must not read as changed when the
    other is used (defect 6 of the first review). Canonicalising **here** gives the comparison that:
    a caller compares `basis_for_comparison(...)` against the stored value, so a tag stamped at
    `inch`
    matches a part whose units now say `in`, and neither stored value has moved.

    The two hashes are equal for the same text, so a caller may use this unconditionally: it costs
    one
    extra hash only where the units are not already canonical.
    """
    return basis_for(stamp, solid, faces, canonical_units(units) if units else None)


def is_default_units(units: str) -> bool:
    """True when ``units`` is the length unit a reader uses when the import names none.

    By scale rather than by name, so the aliases of one unit are one answer. An unreadable name is
    NOT the default: it is a claim this cannot place, and treating it as the default would let a
    typo
    silently hash as millimetres.
    """
    from stokes.geometry.meshfile import DEFAULT_UNITS, unit_scale

    try:
        return unit_scale(units) == unit_scale(DEFAULT_UNITS)
    except Exception:
        return False


def basis_for_source(
    source: Path | None,
    stamp: SourceStamp,
    solid: int,
    faces: int,
    units: str | None = None,
) -> str:
    """The basis of a solid **read from a source file**, resolving the units the one correct way.

    Every caller that writes or recomputes a basis for an imported part goes through here, because
    the units must be resolved *identically* or the two sides disagree and a part looks "changed
    since it was imported". Getting that wrong cost three attempts and 26 test failures:

    1. the raw import parameter (`job.units`) is `None` when the caller typed none, while
       the reader fills in the format's default;
    2. a name derived from the scale is wrong — `unit_name(0.0254)` is `"in"` where the reader says
       `"inch"`;
    3. a STEP declares its units in the file, so `read_units` returns None for it and the file's own
       bytes are the identity — passing the model's `"mm"` made every STEP re-read its source.

    `read_units(source, units)` is the single resolution, and it is what `part_spec` hands the
    reader.
    """
    from stokes.geometry.formats import read_units

    resolved = read_units(source, units) if source is not None else None
    return basis_for(stamp, solid, faces, resolved)


def fingerprints_of(
    surface: Surface, facets: Facets, quality: Sequence[str] = ()
) -> list[Fingerprints]:
    """One :class:`Fingerprints` per part of the batch."""
    total = surface.face_total
    named = surface.face >= 0
    face = surface.face[named]
    area = facets.area[named]
    weights = np.bincount(face, weights=area, minlength=total)
    centroid = np.zeros((total, 3))
    normal = np.zeros((total, 3))
    for axis in range(3):
        centroid[:, axis] = np.bincount(
            face, weights=area * facets.centroid[named, axis], minlength=total
        )
        normal[:, axis] = np.bincount(
            face, weights=area * facets.normal[named, axis], minlength=total
        )
    has_area = weights > 0.0
    np.divide(centroid, weights[:, None], out=centroid, where=has_area[:, None])
    np.divide(normal, weights[:, None], out=normal, where=has_area[:, None])
    triangles = np.bincount(face, minlength=total).astype(np.int64)
    scales = _diagonals(surface)
    result = []
    for part in range(surface.parts):
        lo, hi = int(surface.face_offset[part]), int(surface.face_offset[part + 1])
        result.append(
            Fingerprints(
                weights[lo:hi],
                centroid[lo:hi],
                normal[lo:hi],
                triangles[lo:hi],
                scales[part],
                quality[part] if part < len(quality) else "",
            )
        )
    return result


def _diagonals(surface: Surface) -> list[float]:
    """The bounding-box diagonal of each part (0 for a part without vertices)."""
    counts = np.diff(surface.vertex_offset)
    filled = counts > 0
    result = np.zeros(surface.parts)
    if filled.any():
        starts = surface.vertex_offset[:-1][filled]
        points = surface.positions.astype(np.float64)
        low = np.minimum.reduceat(points, starts, axis=0)
        high = np.maximum.reduceat(points, starts, axis=0)
        result[filled] = np.linalg.norm(high - low, axis=1)
    return [float(v) for v in result]


# ---------------------------------------------------------------- matching


@dataclass(frozen=True)
class Match:
    """Where an old face went: ``new`` is None when it has no confident counterpart."""

    old: int
    new: int | None
    confidence: float

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {"from": self.old, "to": self.new, "confidence": round(self.confidence, 4)}


def match_faces(
    old: Fingerprints,
    new: Fingerprints,
    wanted: Iterable[int],
    min_confidence: float = DEFAULT_CONFIDENCE,
) -> list[Match]:
    """Map the ``wanted`` faces of ``old`` onto ``new`` (module docstring, "Matching")."""
    asked = [int(f) for f in wanted]
    chosen, confidence = _assign(old, new)
    result = []
    for face in asked:
        if 0 <= face < old.faces and chosen[face] >= 0 and confidence[face] >= min_confidence:
            result.append(Match(face, int(chosen[face]), float(confidence[face])))
        else:
            known = float(confidence[face]) if 0 <= face < old.faces else 0.0
            result.append(Match(face, None, known))
    return result


def _assign(old: Fingerprints, new: Fingerprints) -> tuple[I64, F64]:
    """For every old face: its new face (-1 for none) and the confidence."""
    chosen = np.full(old.faces, -1, dtype=np.int64)
    confidence = np.zeros(old.faces)
    usable = np.flatnonzero(new.triangles > 0)
    meshed = np.flatnonzero(old.triangles > 0)
    if usable.size == 0 or meshed.size == 0:
        return chosen, confidence
    scale = max(old.scale, new.scale, _TINY)
    k = min(CANDIDATES, int(usable.size))
    tree = import_module("scipy.spatial").cKDTree(new.centroid[usable] / scale)
    _, near = tree.query(old.centroid[meshed] / scale, k=k)
    near = usable[np.asarray(near, dtype=np.int64).reshape(meshed.size, k)]
    distance = _distance(old, new, meshed, near, scale)
    order = np.argsort(distance, axis=1)
    near = np.take_along_axis(near, order, axis=1)
    distance = np.take_along_axis(distance, order, axis=1)
    taken = np.zeros(new.faces, dtype=bool)
    slot = np.full(meshed.size, -1, dtype=np.int64)
    for row in np.argsort(distance[:, 0], kind="stable").tolist():
        for column in range(k):
            candidate = int(near[row, column])
            if not taken[candidate]:
                taken[candidate] = True
                slot[row] = column
                break
    rows = np.flatnonzero(slot >= 0)
    best = distance[rows, slot[rows]]
    others = distance[rows].copy()
    others[np.arange(rows.size), slot[rows]] = np.inf
    alternative = others.min(axis=1) if k > 1 else np.full(rows.size, np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        margin = np.where(np.isfinite(alternative), 1.0 - best / alternative, 1.0)
    score = np.exp(-0.5 * best**2) * np.clip(np.nan_to_num(margin, nan=0.0), 0.0, 1.0)
    chosen[meshed[rows]] = near[rows, slot[rows]]
    confidence[meshed[rows]] = score
    return chosen, confidence


def _distance(old: Fingerprints, new: Fingerprints, rows: I64, near: I64, scale: float) -> F64:
    shift = np.linalg.norm(new.centroid[near] - old.centroid[rows][:, None, :], axis=2) / scale
    a_old, a_new = old.area[rows][:, None], new.area[near]
    growth = np.abs(a_new - a_old) / np.maximum(np.maximum(a_old, a_new), _TINY)
    turn = np.linalg.norm(new.normal[near] - old.normal[rows][:, None, :], axis=2) / 2.0
    return np.asarray(
        np.sqrt(
            (shift / TOLERANCES["centroid"]) ** 2
            + (growth / TOLERANCES["area"]) ** 2
            + (turn / TOLERANCES["normal"]) ** 2
        ),
        dtype=np.float64,
    )
