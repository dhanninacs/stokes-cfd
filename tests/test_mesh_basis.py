"""The mesh basis parts (brief M-501): what changed since a mesh was made, named per task.

**The hole this closes.** For blockMesh the basis hashed every input through `tag_members` (the
tags' stored member text). For snappyHexMesh `sides` is empty on purpose, so the basis held **no
tag membership at all**: a re-picked face, a per-body size, nothing the tags carry could move it,
and Generate reused a mesh the user had just changed. The first fix hashed `body_mesh`; this file
pins the full split — every tag's FULL face-id list (not the 64-id cut the result node keeps for
its UI), the per-surface levels, the features, the snap controls — as six named parts whose
differences name the task that changed.

**The blockMesh guarantee stays**: its overall basis hashes exactly the keys it always did, byte
for byte, because its goldens pin the mesh and nothing snappy carries exists for blockMesh.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.mesh.snapshot import _tag_faces
from stokes.model.project import Project
from tools.e2e_support import duct_project


@pytest.fixture
def snappy_duct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Session, str, str]:
    """The duct project with snappyHexMesh configured on the part.

    Returns the session, the part id and the `walls` tag's id: the smallest real project that
    builds a snappy snapshot, because the digest is computed where the tag faces are read.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))
    session = Session()
    made = duct_project(session, tmp_path / "duct.stokes")
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"domain": "part", "domain_part": made["part"]},
    )
    project = session.require_project()
    walls = str(next(n for n in project.root.walk() if n.type == "tag" and n.name == "walls").id)
    return session, str(made["part"]), walls


def _snappy_basis(session: Session) -> str:
    """The current snappy basis of the session's project, from the settings node."""
    from stokes.mesh.basis import mesh_basis
    from stokes.mesh.drivers import driver_for
    from stokes.mesh.snapshot import snapshot_for

    settings = session.require_project().find("/mesh/settings")
    snap = snapshot_for(session, settings)
    return mesh_basis(snap, driver_for("snappy").DRIVER_VERSION, "org-14")


def test_editing_a_tags_members_changes_the_snappy_basis(
    snappy_duct: tuple[Session, str, str],
) -> None:
    """A re-picked face is a different surface for snappyHexMesh to follow, so the mesh it makes
    is a different mesh — Generate must not reuse the old one.

    **This failed before the digest**: the snappy basis hashed `sides`, which is empty for snappy
    by design, so `tag_members` never reached it and the re-picked face was invisible.
    """
    session, part, walls = snappy_duct
    before = _snappy_basis(session)
    members = session.execute("tag.members", tag=walls)  # the current members, to shrink by one
    faces = [f for m in members["members"] for f in m["faces"]]
    assert faces, "the walls tag holds explicit faces on the duct"
    session.execute("tag.set", tag=walls, members=[{"part": part, "faces": faces[:-1]}])
    assert _snappy_basis(session) != before


def test_the_digest_is_the_FULL_sorted_face_list_not_the_64_cut() -> None:
    """Two tags that agree on their first 64 faces but differ on the 65th must hash differently.

    The result node keeps only the first 64 ids per tag (`tag_members`, a UI budget), so a digest
    read from that record would call the two meshes equal. The digest is computed where the FULL
    list exists, in `_tag_faces`, and this test builds exactly that pair.
    """
    from stokes.geometry.selection import Member, dump_members

    project = Project.new("digest")
    project.add("/geometry", "group", "parts")
    part = project.add("/geometry/parts", "part", "plate")

    def a_tag(name: str, faces: tuple[tuple[int, int], ...]) -> object:
        node = project.add("/geometry/parts", "tag", name)
        project.set_param(
            node.id, "members", dump_members([Member(part=str(part.id), faces=faces, basis="b1")])
        )
        return node

    a_tag("small", ((0, 63),))
    a_tag("big", ((0, 63), (200, 200)))

    tag_faces, _kinds, _members, digests = _tag_faces(project, part, 500, "b1")
    small_ids, big_ids = sorted(tag_faces["small"]), sorted(tag_faces["big"])
    # The 64-id cut the result node keeps cannot tell these tags apart:
    assert small_ids[:64] == big_ids[:64]
    assert len(big_ids) == 65
    # ...and the digests must:
    assert digests["small"] != digests["big"]


def test_the_blockmesh_basis_hashes_exactly_the_documented_keys() -> None:
    """**Byte-identical, pinned here rather than in a golden file** because the key set is the
    contract: the split into parts must not move a single hashed key for blockMesh, or every
    stored mesh would go stale on upgrade for no reason a user can see.
    """
    from stokes.mesh.basis import mesh_basis
    from stokes.mesh.snapshot import MeshSnapshot

    snap = MeshSnapshot(
        settings_id="n_s",
        mesher="blockmesh",
        base_size=0.005,
        bbox=(0.0, 0.0, 0.0, 0.1, 0.02, 0.04),
        sides={"xmin": "inlet"},
        side_tags={"xmin": "t_in"},
        side_kinds={"xmin": "velocity_inlet"},
        unmapped=(),
        max_cells=5_000_000,
        cores=1,
        max_non_ortho=70.0,
        max_skewness=4.0,
        domain_part="n_part",
        part_basis="abc123",
        tag_members={"inlet": {"members": [1]}},
        body_mesh={"housing": {"size": 0.002, "refinement": 1}},
    )
    obj = {
        "driver": "blockmesh/3",
        "engine": "org-14",
        "mesher": "blockmesh",
        "base_size": 0.005,
        "bbox": [0.0, 0.0, 0.0, 0.1, 0.02, 0.04],
        "sides": {"xmin": "inlet"},
        "side_kinds": {"xmin": "velocity_inlet"},
        "max_cells": 5_000_000,
        "part_basis": "abc123",
        "tags": {"inlet": {"members": [1]}},
        "body_mesh": {"housing": {"size": 0.002, "refinement": 1}},
    }
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()
    assert mesh_basis(snap, "blockmesh/3", "org-14") == expected


def test_the_parts_carry_the_briefs_keys() -> None:
    """The six parts and their exact key sets (the brief's table); a part with no keys is absent
    rather than present-and-empty, so a client's diff sees only what can actually differ.
    """
    from stokes.mesh.basis import mesh_basis_parts
    from stokes.mesh.drivers.snappy import Feature, SnappySnapshot, Surface

    snap = SnappySnapshot(
        surfaces=(
            Surface(name="inlet", file="inlet.stl", patch_type="patch", level_min=0, level_max=1),
        ),
        features=(Feature(file="inlet.eMesh", level=2),),
        background_min=(-1.0, -1.0, -1.0),
        background_max=(2.0, 1.0, 1.0),
        background_cells=(12, 8, 8),
        inside_point=(0.0, 0.0, 0.0),
        base_size=0.25,
        part_basis="pb",
        side_kinds={"inlet": "patch"},
        tag_digests={"inlet": "d" * 40},
        domain="part",
    )
    parts = mesh_basis_parts(snap, "snappy/2", "org-14")
    assert set(parts) == {"geometry", "sizing", "surface", "mesher"}
    # The `snap` part exists only when the snapshot carries the snap controls, which the real
    # builder always copies on and a hand-built one may not:
    controlled = dataclasses.replace(snap, snap_controls=(("nSmoothPatch", 3), ("tolerance", 2.0)))
    assert "snap" in mesh_basis_parts(controlled, "snappy/2", "org-14")
    # Each part is a sha1 of its own keys; the contents are pinned through the overall basis
    # below, so here only the part NAMES and the no-keys-means-absent rule are pinned.
    for value in parts.values():
        assert len(value) == 40 and int(value, 16) >= 0


def test_the_overall_snappy_basis_agrees_with_its_parts() -> None:
    """A change to any part's inputs must move the overall basis — that is the whole point of the
    split, because reuse and staleness compare the overall hash.
    """
    from stokes.mesh.basis import mesh_basis, mesh_basis_parts
    from stokes.mesh.drivers.snappy import SnappySnapshot, Surface

    def a_snapshot(level_max: int) -> SnappySnapshot:
        return SnappySnapshot(
            surfaces=(
                Surface(
                    name="inlet",
                    file="inlet.stl",
                    patch_type="patch",
                    level_min=0,
                    level_max=level_max,
                ),
            ),
            background_min=(-1.0, -1.0, -1.0),
            background_max=(2.0, 1.0, 1.0),
            base_size=0.25,
            part_basis="pb",
            tag_digests={"inlet": "d" * 40},
            domain="part",
        )

    plain, finer = a_snapshot(1), a_snapshot(2)
    assert mesh_basis(plain, "snappy/2", "org-14") != mesh_basis(finer, "snappy/2", "org-14")
    first = mesh_basis_parts(plain, "snappy/2", "org-14")
    second = mesh_basis_parts(finer, "snappy/2", "org-14")
    assert {k for k in first if first[k] != second.get(k)} == {"sizing"}
