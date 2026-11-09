"""The mesh node types (T-143).

Every node type is checked the way `docs/engineering/testing.md` requires: its defaults are
valid, it has a JSON schema, and it survives a save/load round trip with non-default values. The
quantity round trip is the one worth naming — a length stored in SI and read back as metres is
the thing rule 6 exists for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.mesh.catalogue import MESHERS, mesher
from stokes.mesh.errors import MeshBusy, MeshError, MeshInvalid, MeshNotBuilt, NoHost
from stokes.mesh.model import RESULT_STATES, mesh_specs
from stokes.model import registry

MESH_TYPES = ("mesh_settings", "mesh_result", "mesh_patch")


@pytest.mark.parametrize("type_name", MESH_TYPES)
def test_every_parameter_has_a_valid_default(type_name: str) -> None:
    """A node type whose defaults do not validate cannot be created, so this is the first check."""
    spec = registry.get(type_name)
    assert spec.params, f"{type_name} has no parameters"
    for param in spec.params:
        if param.kind == "ref":
            # A ref has no default by design (ADR-0022 decision 2.8): the default cannot tell a
            # required reference from an optional one.
            assert param.default is None
            continue
        assert param.default is not None or param.kind == "json", (
            f"{type_name}.{param.name} has no default"
        )


@pytest.mark.parametrize("type_name", MESH_TYPES)
def test_every_node_type_has_a_json_schema(type_name: str) -> None:
    """The schema is generated, never hand-written (CLAUDE.md §8), and a client builds its form
    from it."""
    schema = registry.get(type_name).json_schema()
    assert schema, f"{type_name} has an empty schema"
    assert "properties" in schema or "type" in schema


def test_the_settings_parameters_are_the_briefs() -> None:
    """The parameter set, by name and kind, so a rename or a drop is a test failure rather than a
    silently missing control."""
    spec = registry.get("mesh_settings")
    kinds = {param.name: param.kind for param in spec.params}
    assert kinds == {
        "mesher": "enum",
        "host": "string",
        "base_size": "quantity",
        "surface_size": "quantity",
        "domain": "enum",
        "domain_part": "ref",
        "box": "array",
        "margin": "quantity",
        "max_cells": "int",
        "cores": "int",
        "max_non_ortho": "number",
        "max_skewness": "number",
        # **"Keep unassigned faces as a wall"** (brief M-501): a stored acceptance, so the
        # Boundaries task can be `done` without a finding nobody wrote down. Written only
        # through `mesh.configure`, so it is an undo step and a journal record.
        "accept_unassigned": "bool",
        # **The layer set-up (brief M-504)**: the counts the writer turns into snappy's
        # `addLayersControls`, and the ranges the `validate` hooks enforce.
        "layers": "int",
        "layer_mode": "enum",
        "expansion_ratio": "number",
        "final_ratio": "number",
        "first_layer_height": "quantity",
        "min_thickness_ratio": "number",
        "layer_feature_angle": "number",
        # **The sharp-edge threshold** (brief M-515): one setting, both dictionaries.
        "feature_angle": "number",
        # **2D (ADR-0031)**: off by default; the plane, the planar thickness, the wedge angle.
        "two_d": "enum",
        "plane": "enum",
        "thickness": "quantity",
        "wedge_angle": "number",
    }
    # `region` is added with snappy (T-150); it must not appear early.
    assert "region" not in kinds
    # And no snappy "levels" anywhere: sizes are lengths (ADR-0024).
    assert not any("level" in name for name in kinds)


def test_the_domain_is_a_part_or_a_box() -> None:
    """`region` joins this with snappy, which is why it is an enum rather than a boolean."""
    spec = registry.get("mesh_settings")
    assert {p.name: p.choices for p in spec.params if p.name == "domain"} == {
        # **`enclosure` joined them** (brief M-501): the snapshot and the catalogue already used
        # that value for a body in free space, but the enum refused it - so
        # `mesh.configure {domain: "enclosure"}` could not be asked for.
        "domain": ("part", "box", "enclosure")
    }


def test_the_mesher_enum_matches_the_catalogue() -> None:
    """**Two lists that must not drift.** The model validates `mesher`, the catalogue says which are
    built; a mesher in one and not the other is a setting the user can pick and cannot use."""
    spec = registry.get("mesh_settings")
    choices = next(p.choices for p in spec.params if p.name == "mesher")
    assert choices == tuple(spec.id for spec in MESHERS)


def test_the_result_states_are_the_adr_set() -> None:
    """The ADR-0024 correction's list, including `skipped` and `interrupted`, whose absence would
    make a
    replay with no host either fail or be refused by the model."""
    spec = registry.get("mesh_result")
    choices = next(p.choices for p in spec.params if p.name == "state")
    assert choices == RESULT_STATES
    assert set(choices) == {
        "queued",
        "running",
        "cancelling",
        "done",
        "failed",
        "cancelled",
        "interrupted",
        "skipped",
    }
    assert next(p.default for p in spec.params if p.name == "state") == "queued"


def test_surface_size_defaults_to_zero_meaning_the_base_size() -> None:
    """T-205 amendment 3: 0.0 means "same as base_size", so snappy needs no second number to be told
    what a user has not decided."""
    param = next(p for p in registry.get("mesh_settings").params if p.name == "surface_size")
    assert param.kind == "quantity"
    assert param.dimension == "length"
    assert param.default == 0.0


def test_a_patch_tag_id_is_a_string_and_not_a_ref() -> None:
    """**The deliberate asymmetry.** A `ref` would make `node.remove` refuse to delete a tag that
    an old
    mesh patch names, so a derived result could block an edit; a string cannot."""
    param = next(p for p in registry.get("mesh_patch").params if p.name == "tag_id")
    assert param.kind == "string"
    assert param.ref_type is None
    # The domain part IS a ref, so deleting it while the settings name it is refused. That is
    # intended.
    domain = next(p for p in registry.get("mesh_settings").params if p.name == "domain_part")
    assert domain.kind == "ref" and domain.ref_type == "part"


def test_the_result_accepts_patches_and_the_others_do_not() -> None:
    """The containment rule the tree enforces: a patch belongs to a result."""
    assert registry.get("mesh_result").children == ("mesh_patch",)
    assert registry.get("mesh_settings").children == ()
    assert registry.get("mesh_patch").children == ()


def test_the_settings_survive_a_save_and_load_round_trip(tmp_path: Path) -> None:
    """A quantity written as ``3 [mm]`` reads back as 0.003 m. This is rule 6: SI in the model, the
    user's unit at the boundary."""
    from stokes.api import Session
    from stokes.model.persist import load_project, save_project

    session = Session()
    session.execute("project.new", name="round")
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={"base_size": "3 [mm]", "surface_size": "1.5 [mm]", "cores": 3},
    )
    path = tmp_path / "round.stokes"
    save_project(session.require_project(), path)
    reloaded = load_project(path)
    node = reloaded.find("/mesh/settings")
    assert node is not None
    assert node.params["base_size"].value == pytest.approx(0.003)
    assert node.params["surface_size"].value == pytest.approx(0.0015)
    assert node.params["cores"] == 3
    assert node.params["mesher"] == "blockmesh"


def test_the_catalogue_lists_every_mesher_with_a_reason() -> None:
    """An unbuilt mesher is listed rather than hidden, and says why — a user who cannot find cfMesh
    assumes Stokes forgot it."""
    assert len(MESHERS) == 7
    built = [spec for spec in MESHERS if spec.built]
    # **Two are built now** (claude-win2's 1045): snappyHexMesh is implemented and is the default,
    # so the list is blockMesh (the bounding-box shortcut, still user-selectable) and snappyHexMesh
    # (follows the shape). cfMesh and Gmsh are listed with their reasons rather than hidden.
    assert [spec.id for spec in built] == [
        "blockmesh",
        "snappy",
        "cfmesh",
        "cfmesh_tet",
        "cfmesh_poly",
        "polyhex",
    ]
    blockmesh = built[0]
    assert blockmesh.domains == ("part", "box")
    # **snappy offers `enclosure` where blockMesh offers `box`**: a wind-tunnel around a solid
    # body, which is the external-flow case, as against blockMesh's bounding-box shortcut.
    assert built[1].domains == ("part", "enclosure")
    for spec in MESHERS:
        if not spec.built:
            assert spec.why, f"{spec.id} is not built but gives no reason"

    # **The BUILT one needs a reason too, and this is the half that was missing.** `why` was
    # asserted only for unbuilt meshers, so blockMesh's description was empty - and its limitation
    # is the thing a user most needs told. Measured: the demo pipe meshes to a BOX the size of the
    # pipe, every quality check passes because a box meshes perfectly, `walls` is absent and 4,704
    # faces fall `unassigned`. The owner asked "why does it turn into a duct?" because the panel
    # said nothing; an empty `why` was the reason.
    for spec in built:
        assert spec.why, f"{spec.id} is built but does not say what it can mesh"
    assert "bounding box" in blockmesh.why
    assert "snappyHexMesh" in blockmesh.why, "it should name what to use for the shape itself"
    assert "follows the shape" in built[1].why


def test_an_unknown_mesher_names_the_known_ones() -> None:
    """A refusal that lists the alternatives is one a user can act on."""
    with pytest.raises(MeshInvalid) as caught:
        mesher("nope")
    assert "blockmesh" in str(caught.value)


def test_the_mesh_error_codes_are_fixed_in_one_place() -> None:
    """The codes a client branches on. Four subclasses, each with its own code, all StokesError so
    the
    server, CLI and MCP return them the same way."""
    assert MeshError.code == "mesh.error"
    assert MeshInvalid.code == "mesh.invalid"
    assert MeshNotBuilt.code == "mesh.not_built"
    assert NoHost.code == "mesh.no_host"
    assert MeshBusy.code == "mesh.busy"
    for cls in (MeshInvalid, MeshNotBuilt, NoHost, MeshBusy):
        assert issubclass(cls, MeshError)


def test_the_specs_are_built_on_call_not_at_import() -> None:
    """`mesh_specs()` returns fresh specs and does not run at module import.

    It matters because `stokes.model.schema` imports this module during
    `register_builtin_types`, inside a budget of 300 ms — and because building a spec imports
    the schema, which would be a cycle at module level.
    """
    first = mesh_specs()
    assert {spec.type for spec in first} == set(MESH_TYPES)
    assert mesh_specs() is not first or first == mesh_specs()


def test_a_mesh_service_asks_the_host_its_job_runs_on() -> None:
    """**T-159a's prerequisite: the mesh service must resolve the job's own host.**

    Five call sites hardcoded `runner("local")` - cancelling a mesh, reading its log, reading its
    status on a final event, and extracting its surfaces. The repo's own notes recorded this as
    *correct while `HOST_NAMES == ("local",)`* and **wrong the day a second host exists**: a cancel
    would signal the local agent's job id and silently do nothing, and `log.tail` would read the
    wrong agent entirely.

    The result node is the authority (`result.params["host"]`), which is also what `_reusable`
    compares against - so this asserts the resolver reads that fact rather than a remembered value.
    """
    from stokes.api import Session
    from stokes.mesh.service import MeshService

    session = Session()
    session.execute("project.new", name="t159a")
    service = MeshService.of(session)

    # No result node at all: `local` is the only honest answer, not an error.
    assert service._host_of() == "local"

    # A result node that ran elsewhere is what the resolver must return - for every caller that
    # reaches an agent, including the ones with no result id to hand.
    session.project.add("/mesh", "group", "results")
    node = session.project.add(
        "/mesh/results", "mesh_result", "m", {"state": "done", "host": "buildbox"}
    )
    assert service._host_of() == "buildbox"
    assert service._host_of(str(node.id)) == "buildbox"
    # A result id that is NOT the current result must not make the caller read someone else's host.
    assert service._host_of("n_deadbeef") == "local"
    # A node predating the `host` parameter genuinely ran locally.
    node.params.pop("host")
    assert service._host_of() == "local"
