"""The mesh basis: which inputs a mesh was made from (T-145a, ADR-0024).

A forty-character sha1 that answers one question — *would this driver, on this snapshot, produce
the same
mesh?* — so a stale mesh can be recognised without re-running anything.

## What is in it, and what is deliberately out

In: the driver version, the engine, the mesher, the base size, the domain box, the side-to-tag
mapping,
the cell limit, the part's basis, and the members of the tags that reached a side.

Out, each for a reason:

* **`host`** — the same inputs on another machine produce the same mesh.
* **`cores`** — blockMesh is serial, so including it would mark a mesh stale for a change that
  cannot affect it, and a basis that changes for no reason is a basis users learn to ignore.
  **snappyHexMesh is the exception** (ADR-0025): above one rank its case gains a
  `decomposeParDict` and the mesh is built in pieces, so the rank count is in the snappy part of
  the basis. A serial snappy mesh keeps the hash it had before.
* **the quality thresholds (`max_non_ortho`, `max_skewness`) for blockMesh** — they decide whether
a mesh
  **passes**, not what it is. Moving a threshold must not discard a mesh that is still exactly the
  same mesh.

**The amendment (ADR-0024 §4.4, brief M-501): that is a blockMesh rule only.** A mesher that uses
the quality limits WHILE it meshes — snappyHexMesh's `meshQualityControls`, and the castellated
refinement the limits steer — produces a different mesh when they move, so for a snappy snapshot
the thresholds are **in** the basis, in its `snap` part. For blockMesh they stay out, and a limit
change re-judges the stored quality without a job.

The engine **is** included, because a different OpenFOAM version's blockMesh can produce a
different mesh
from identical input, and that would be a silent difference in the answer.

## The parts (brief M-501)

`mesh_basis_parts` splits the inputs into six named sub-hashes. The **stored overall basis for a
snappy snapshot is the hash of the parts themselves**, so it is consistent with them by
construction; for blockMesh the overall basis hashes exactly the flat key set it always did,
byte for byte — its goldens pin the mesh, and no part key blockMesh does not have may reach it.
The service stores the parts on the result (`meta.basis_parts`); a part that differs names the
tasks whose inputs changed, which is what the stepper shows ("Sizing changed since this mesh").
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.mesh.snapshot import MeshSnapshot


def _rounded(value: Any) -> Any:
    """Floats to twelve significant digits, so a basis is stable across a serialisation round trip.

    A float that survives JSON and comes back a bit different would otherwise change the hash for no
    reason: `0.1 + 0.2` and `0.3` are the same mesh.
    """
    if isinstance(value, float):
        return float(f"{value:.12g}")
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def _hash(obj: Any) -> str:
    """The sha1 hex of a JSON round trip of ``obj``.

    **`usedforsecurity=False`**, as `stokes/geometry/fingerprints.py` does: this is a fingerprint
    that answers "would this produce the same mesh", not a security primitive. Saying so is what
    tells the linter — and a reader — which of the two it is.
    """
    text = json.dumps(_rounded(obj), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _is_snappy(snap: MeshSnapshot) -> bool:
    """Is this a snappy snapshot? **By the mesher the snapshot itself records**, which is what the
    service already trusts when it picks the driver — not an attribute probe that a second
    snapshot shape would silently break.
    """
    return str(getattr(snap, "mesher", "")) == "snappy"


def _follows_surfaces(snap: MeshSnapshot) -> bool:
    """Is this a surface-following snapshot - snappy or the cfMesh family (ADR-0032)?

    Those records carry the surfaces, levels and layers the flat blockMesh key set never saw, so
    their basis is the hash of the parts. **The cfMesh meshers are new**, so no stored basis of
    theirs exists to keep stable.
    """
    from stokes.mesh.model import SURFACE_MESHERS

    return str(getattr(snap, "mesher", "")) in SURFACE_MESHERS


def mesh_basis(snap: MeshSnapshot, driver_version: str, engine: str) -> str:
    """The sha1 hex of everything that decides what the mesh is.

    ``engine`` is ``"<flavour>-<version>"`` from the host record — `"org-14"` on this machine.

    For blockMesh this hashes exactly the flat key set it always did (byte-identical, pinned by
    test). For snappy it hashes the six parts from :func:`mesh_basis_parts`, because a snappy
    snapshot carries inputs the flat key set never saw.
    """
    if _follows_surfaces(snap):
        return _hash({"parts": mesh_basis_parts(snap, driver_version, engine)})
    obj = {
        "driver": driver_version,
        "engine": engine,
        "mesher": snap.mesher,
        "base_size": snap.base_size,
        "bbox": list(snap.bbox),
        "sides": dict(snap.sides),
        # **The kinds are in the basis too** (T-206): a changed kind changes the boundary file's
        # `type`
        # words, so the mesh a reader sees is not the same mesh.
        "side_kinds": dict(snap.side_kinds),
        "max_cells": snap.max_cells,
        "part_basis": snap.part_basis,
        "tags": {key: snap.tag_members.get(key) for key in sorted(snap.sides.values()) if key},
        # **The per-body mesh controls** (brief M-501): a size or a refinement level changes the
        # dictionary, so it has to change the basis. Without this a reviewer was right that Generate
        # would reuse the old mesh after a per-body edit - the basis hashed only the blockMesh-era
        # fields, none of which those controls touch.
        "body_mesh": dict(getattr(snap, "body_mesh", {}) or {}),
    }
    two_d = dict(getattr(snap, "two_d", {}) or {})
    if two_d:
        # **Only when 2D is on** (ADR-0031), so a 3D project's basis is the one it always had.
        obj["two_d"] = two_d
    return _hash(obj)


def mesh_basis_parts(snap: MeshSnapshot, driver_version: str, engine: str) -> dict[str, str]:
    """The six sub-bases (brief M-501), each a sha1 of exactly its keys.

    The key contents are the brief's, and the worker does not choose: `geometry` (part basis,
    domain, inside point, background box, side kinds, and each tag's FULL face digest), `sizing`
    (base size, cell limit, every surface's level pair, cells between levels, refinement regions),
    `surface` (the staged surfaces, the features with levels, the resolve angle), `layers` (when a
    layer count exists), `snap` (the snap controls and, for snappy, the quality limits), `mesher`
    (driver version, engine, mesher).

    **A part with no keys is absent**, not present-and-empty: only what can actually differ is
    hashed, so a client diffing two part records sees the task that changed and nothing else. The
    `layers` part arrives with the layer counts (the boundary-layer package) — no layer control
    exists on any snapshot yet, so it is never emitted today.
    """
    digests = dict(getattr(snap, "tag_digests", {}) or {})
    geometry: dict[str, Any] = {
        "part_basis": snap.part_basis,
        "domain": str(getattr(snap, "domain", "") or ""),
        "bbox": list(snap.bbox),
        "side_kinds": dict(snap.side_kinds),
        "tag_digests": digests,
    }
    inside_point = getattr(snap, "inside_point", None)
    if inside_point is not None:
        geometry["inside_point"] = list(inside_point)
    two_d = dict(getattr(snap, "two_d", {}) or {})
    if two_d:
        # Only when 2D is on (ADR-0031): a 3D project's geometry part is unchanged.
        geometry["two_d"] = two_d
    parts: dict[str, str] = {"geometry": _hash(geometry)}

    sizing: dict[str, Any] = {"base_size": snap.base_size, "max_cells": snap.max_cells}
    surfaces = tuple(getattr(snap, "surfaces", ()) or ())
    if surfaces:
        sizing["surface_levels"] = {
            str(surface.name): [int(surface.level_min), int(surface.level_max)]
            for surface in surfaces
        }
        # **`getattr`, because these two live on the SNAPPY record only** - `MeshSnapshot` is the
        # blockMesh one - and the function takes the shared type.
        sizing["cells_between_levels"] = int(getattr(snap, "cells_between_levels", 0) or 0)
        regions = list(getattr(snap, "refinement_regions", ()) or ())
        if regions:
            sizing["refinement_regions"] = regions
    parts["sizing"] = _hash(sizing)

    if surfaces:
        parts["surface"] = _hash(
            {
                "surfaces": {str(s.name): str(s.file) for s in surfaces},
                "features": [[str(f.file), int(f.level)] for f in getattr(snap, "features", ())],
                # **`getattr` for the same reason as the levels**: the feature angle is on the
                # snappy record, and this function takes the type both records share.
                "resolve_feature_angle": float(getattr(snap, "feature_angle", 0.0) or 0.0),
            }
        )

    # The `layers` part is emitted only when a layer count above zero exists (brief M-504): the
    # resolved counts and the settings that shape them. An override's explicit 0 is not a layer
    # being grown, so it does not hash - nothing about it reaches the dictionary. Without any
    # count nothing about layers reaches the dictionary either, so nothing about layers belongs
    # in the hash — and a hash of nothing would be a part that lies about what it covers.
    counts = {name: count for name, count in getattr(snap, "layer_counts", {}).items() if count > 0}
    if counts:
        parts["layers"] = _hash(
            {"counts": counts, "settings": dict(getattr(snap, "layer_settings", {}) or {})}
        )

    controls = tuple(getattr(snap, "snap_controls", ()) or ())
    if controls:
        parts["snap"] = _hash(
            {
                "snap_controls": {str(name): value for name, value in controls},
                "max_non_ortho": float(snap.max_non_ortho),
                "max_skewness": float(snap.max_skewness),
            }
        )

    mesher: dict[str, Any] = {
        "driver": driver_version,
        "engine": engine,
        "mesher": str(snap.mesher),
    }
    # A parallel snappy run must not reuse a serial result after the user requests more ranks.
    # Keep the old serial hash stable, and keep blockMesh's serial-only setting out of the basis.
    cores = int(getattr(snap, "cores", 1) or 1)
    if _is_snappy(snap) and cores > 1:
        mesher["cores"] = cores
    parts["mesher"] = _hash(mesher)
    return parts


#: Which tasks a changed part names (brief M-501's table). **Every part also makes `volume`
#: stale** — a mesh whose inputs changed is a mesh to run again — while `changed[]` names only
#: the tasks the part belongs to, so a body-size edit says `["sizing"]` and not the whole list.
PART_TO_TASKS: dict[str, tuple[str, ...]] = {
    "geometry": ("geometry", "bodies", "boundaries"),
    "sizing": ("sizing",),
    "surface": ("surface",),
    "layers": ("layers",),
    "snap": ("volume",),
    "mesher": ("volume",),
}

#: The parts in the order their tasks run, so `changed[]` comes out in task order rather than in
#: dict order.
_PART_ORDER: dict[str, int] = {name: index for index, name in enumerate(PART_TO_TASKS)}


def changed_tasks(stored: Any, current: dict[str, str] | None) -> list[str]:
    """The task ids whose basis parts differ, in the tasks' own order (brief M-501).

    ``stored`` is the result's `meta.basis_parts`. **None is not a diff**: a result made before the
    parts existed is handled by the tasks module itself, which marks only `volume` stale with the
    one line that explains why. A part on one side and not the other differs; a part whose hash is
    equal on both sides does not.
    """
    if stored is None or current is None or not isinstance(stored, dict):
        return []
    changed: list[str] = []
    for part in sorted(set(stored) | set(current), key=lambda name: _PART_ORDER.get(name, 99)):
        if part in current and stored.get(part) != current[part]:
            for task in PART_TO_TASKS.get(part, ()):
                if task not in changed:
                    changed.append(task)
    return changed
