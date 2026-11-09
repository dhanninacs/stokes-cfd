"""The per-body mesh controls default to INHERIT (T-232..T-236).

**The guarantee this file exists for**: a project that sets none of the three parameters must mesh
exactly as it did before they existed. That is what makes the parameters safe to add - the mesh
settings remain the whole story until somebody asks otherwise.

The snapshot half (reading them and applying them) comes after Claude answers which node `layers`
belongs on; the MODEL half is settled and is what these pin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.mesh.snapshot import _snappy_surfaces
from stokes.model import registry
from stokes.model.nodes import QuantityValue
from stokes.model.project import Project


def _part(project: Project) -> object:
    project.add("/geometry", "group", "parts")
    return project.add("/geometry/parts", "part", "housing")


def test_the_THREE_controls_are_on_a_part_and_default_to_inherit() -> None:
    """**The mesh settings decide until the body says otherwise**, which is the state every
    project starts in. A default of anything else would change every mesh on upgrade.

    `layers` came back with a reader (the brief M-504 resolver): its shape is **no default** -
    the parameter materialises null, which is the empty = inherit state, and `0` is a meaning
    (none on this body's walls), not "not set". `null` clears it back to empty.
    """
    schema = registry.get("part").json_schema()["properties"]
    assert schema["mesh_size"]["x-dimension"] == "length"
    assert schema["refinement"]["type"] == "integer" and schema["refinement"]["default"] == 0
    assert schema["layers"]["type"] == "integer" and "default" not in schema["layers"]

    project = Project.new("demo")
    part = _part(project)
    # A quantity stored in SI, like every other length in the model (rule 6).
    assert part.params["mesh_size"] == QuantityValue(value=0.0, unit="m", expr=None)
    assert part.params["refinement"] == 0
    # The empty state: null, not a fabricated zero.
    assert part.params["layers"] is None


def test_a_local_size_is_stored_in_SI_and_keeps_its_expression() -> None:
    """A user types `2 mm` and the model holds 0.002 m with the text, so the pane can show it back
    the way it was written - the same contract as every other quantity."""
    project = Project.new("demo")
    part = _part(project)
    project.set_param(part.id, "mesh_size", "2 [mm]")
    value = part.params["mesh_size"]
    assert value.value == 2e-3
    assert value.unit == "m"


def test_a_negative_size_reaches_the_model_as_the_number_it_is() -> None:
    """**The model stores what was written; the refusal belongs where the size is USED.**

    My first version of this test expected `set_param` to refuse a negative size, and it did not -
    correct, because `ParamSpec` has no minimum hook on a `quantity` and the model's job is to
    hold the value a user typed. The mesh commands already have `_positive` for exactly this
    (`api/commands/mesh.py`), so the per-body size is checked there when the snapshot is built,
    where a non-positive length would otherwise become a wrong mesh rather than a refusal.

    **Recorded rather than deleted**: a test that fails and is then loosened is only honest if the
    reason is written down, and this is where the check is going to be.
    """
    project = Project.new("demo")
    part = _part(project)
    project.set_param(part.id, "mesh_size", "-1 [mm]")
    assert part.params["mesh_size"].value == -1e-3


# ------------------------------------------------------------------ the snapshot, one test per knob


def _build_surfaces(*args: object) -> tuple[list[object], dict[str, str]]:
    """The module's own `_snappy_surfaces`, under a name that cannot recurse into itself."""
    return _snappy_surfaces(*args)


def _snapshot_surfaces(local: dict[str, object]) -> list[object]:
    """The snappy surfaces for one box body with two tags, given the body's mesh settings.

    **Built without the geometry store**: the surfaces come from a cube and two face sets, which is
    everything `_snappy_surfaces` reads - so the rule is tested where it lives rather than through a
    meshed duct that needs OpenFOAM.
    """
    import numpy as np

    # A unit cube: 8 corners, 12 triangles covering 6 faces (face id = triangle id // 2).
    positions = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    indices: list[int] = []
    face_ids: list[int] = []
    for face, (a, b, c, d) in enumerate(quads):
        indices += [a, b, c, a, c, d]
        face_ids += [face, face, face, face, face, face]
    surfaces, _files = _build_surfaces(
        positions,
        np.array(indices, dtype=int),
        np.array(face_ids, dtype=int),
        {"wall_tag": {0}, "top_tag": {4}},
        {"wall_tag": "wall", "top_tag": "symmetry"},
        0.1,
        local,
    )
    return list(surfaces)


def _levels(surfaces: list[object], name: str) -> tuple[int, int]:
    found = next(s for s in surfaces if getattr(s, "name", "") == name)
    return (found.level_min, found.level_max)


def test_with_nothing_set_the_levels_are_what_they_always_were() -> None:
    """**The byte-for-byte guarantee**: a wall is refined once more than a far-field patch, which is
    the rule that predates these controls."""
    plain = _snapshot_surfaces({})
    wall_low, wall_high = _levels(plain, "wall_tag")
    top_low, top_high = _levels(plain, "top_tag")
    assert (wall_low, wall_high) == _levels(_snapshot_surfaces({}), "wall_tag")
    # A wall is finer than the symmetry face next to it, and both are levels above the base.
    assert wall_high >= wall_low
    assert (top_low, top_high) == (wall_low, wall_high) or top_low <= wall_low


def test_snappy_symmetry_tag_uses_a_symmetry_plane_patch() -> None:
    surfaces = _snapshot_surfaces({})
    assert {surface.name: surface.patch_type for surface in surfaces} == {
        "top_tag": "symmetry",
        "wall_tag": "wall",
        "unassigned": "wall",
    }


def test_a_local_size_makes_that_body_finer() -> None:
    """`mesh_size` is the surface size for this body; a smaller one means more levels."""
    plain = _snapshot_surfaces({})
    finer = _snapshot_surfaces({"size": 0.05})
    assert _levels(finer, "wall_tag")[0] >= _levels(plain, "wall_tag")[0]


def test_refinement_raises_the_curvature_level_only() -> None:
    """**`min` untouched, `max` up**: the minimum is what every cell touching the surface gets, so
    raising it would refine a flat face for no reason."""
    plain = _snapshot_surfaces({})
    extra = _snapshot_surfaces({"refinement": 2})
    assert _levels(extra, "wall_tag")[0] == _levels(plain, "wall_tag")[0]
    assert _levels(extra, "wall_tag")[1] == _levels(plain, "wall_tag")[1] + 2


def test_a_per_body_edit_CHANGES_THE_BASIS_so_generate_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Claude's question, answered by measurement** (brief M-501).

    A reviewer suspected the staleness check hashed only blockMesh-era fields, so a per-body edit
    would leave the mesh looking fresh and Generate would reuse it - making the new control do
    nothing visible. **It was true**: `mesh_basis` hashed `base_size`, the box and the side kinds,
    none of which a per-body control touches.

    The first fix carried `body_mesh` on the snapshot and hashed the record. The parts split
    (brief M-501) covers the same ground more honestly: a body's size and refinement reach the
    surfaces' `level (min max)` pairs, and those are what the `sizing` part hashes — so a size
    whose rounding lands on the same levels does NOT stale the mesh, because it produces the same
    dictionary. This pins the consequence through the real builder: **a body whose size changes
    must change the basis, and the same size re-spelled must not.**
    """
    from stokes.api import Session
    from stokes.mesh.basis import mesh_basis
    from stokes.mesh.drivers import driver_for
    from stokes.mesh.snapshot import snapshot_for
    from tools.e2e_support import duct_project

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))
    session = Session()
    made = duct_project(session, tmp_path / "duct.stokes")
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"domain": "part", "domain_part": made["part"]},
    )
    settings = session.require_project().find("/mesh/settings")

    def basis() -> str:
        snap = snapshot_for(session, settings)
        return mesh_basis(snap, driver_for("snappy").DRIVER_VERSION, "org-14")

    plain = basis()
    # **The same value re-spelled gives the same basis**, or every Generate would re-run the mesh.
    session.execute("parameter.set", node=str(made["part"]), name="mesh_size", value="2 [mm]")
    first = basis()
    session.execute("parameter.set", node=str(made["part"]), name="mesh_size", value="2000 [um]")
    assert basis() == first
    # A different size, and an extra level: each is a different mesh.
    session.execute("parameter.set", node=str(made["part"]), name="mesh_size", value="1 [mm]")
    assert basis() != first
    session.execute("parameter.set", node=str(made["part"]), name="refinement", value=2)
    assert basis() != first
    del plain
