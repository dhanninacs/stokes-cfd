"""The snappyHexMesh writers: the keys OF14 reads, and what a wrong one costs.

**Why these tests check the TEXT and not just that a dict was built.** OpenFOAM ignores keys it
does not recognise. A writer that emits `curvatureLevel`, or `locationInMesh` on a version that
stopped reading it, produces no error at all - just a mesh that does less than the user asked. So
the assertions here are on the emitted text, and the two keys that were wrong in the brief
(`curvatureLevel`, `locationInMesh`) have tests that would fail if they came back.

The evidence for every key is in `tests/fixtures/of14/inventory-thinkyboy.md`.
"""

from __future__ import annotations

import dataclasses

import pytest

from stokes.compile.foam import render_file
from stokes.mesh.drivers import snappy
from stokes.mesh.errors import MeshInvalid


def squashed(text: str) -> str:
    """The dictionary with runs of spaces collapsed to one.

    **The writer ALIGNS columns** (``type            triSurface;``), which is what OpenFOAM's own
    files do and what makes a golden diff clean. Asserting an exact run of spaces would therefore
    test the alignment rather than the key, and every alignment change would break thirty tests for
    no reason. The key and its value are what must be right, so the runs are collapsed here.
    """
    import re

    return re.sub(r"[ \t]+", " ", text)


def a_snapshot(**overrides: object) -> snappy.SnappySnapshot:
    """One surface in a box - the smallest case that exercises every block."""
    base: dict[str, object] = {
        # **The layer settings the writer validates** (the M-504 review's F2): a snapshot from
        # the test helper carries the same defaults a fresh project would, so the tests exercise
        # the writer the way the service does.
        "layer_settings": {
            "mode": "final_ratio",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "first_layer_height": 0.0,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
        "surfaces": (
            snappy.Surface(
                name="inlet",
                file="inlet.stl",
                region="inlet",
                patch_type="patch",
                level_min=2,
                level_max=3,
            ),
            snappy.Surface(
                name="walls",
                file="walls.stl",
                region="walls",
                patch_type="wall",
                level_min=1,
                level_max=2,
            ),
        ),
        "features": (snappy.Feature(file="inlet.eMesh", level=3),),
        "background_min": (-1.0, -1.0, -1.0),
        "background_max": (2.0, 1.0, 1.0),
        "background_cells": (12, 8, 8),
        "inside_point": (0.0, 0.0, 0.0),
        "base_size": 0.25,
    }
    base.update(overrides)
    snap = snappy.SnappySnapshot(**base)  # type: ignore[arg-type]
    # **Every surface needs its staged STL**, because `write` now refuses a case that declares a
    # file it was not given: `surfaceFeatures` would die on the host with `Cannot read
    # ".../x.stl"` (measured, on the demo). A fixture that skips this is not a case snappy could
    # run, so it is filled in here - and tests that deliberately omit one override `stl_files`.
    if "stl_files" not in overrides:
        staged = {
            f"{snappy.GEOMETRY_DIR}/{s.file}": f"solid {s.name}\nendsolid {s.name}\n"
            for s in snap.surfaces
        }
        snap = dataclasses.replace(snap, stl_files=staged)
    return snap


# ------------------------------------------------------------------ the files exist and parse


def test_the_writer_emits_the_four_files_a_snappy_case_needs() -> None:
    files = snappy.write(a_snapshot())
    # The four dictionaries, **plus the staged surfaces**: snappy reads those from
    # `constant/geometry/`, so a dictionary naming them is useless without them.
    assert {
        "system/blockMeshDict",
        "system/surfaceFeaturesDict",
        "system/snappyHexMeshDict",
        "system/controlDict",
    } <= set(files)
    assert all(
        k.startswith(f"{snappy.GEOMETRY_DIR}/")
        for k in files
        if k
        not in {
            "system/blockMeshDict",
            "system/surfaceFeaturesDict",
            "system/snappyHexMeshDict",
            "system/controlDict",
        }
    )


def test_symmetry_surface_patch_info_supports_nonplanar_symmetry() -> None:
    snap = a_snapshot(
        surfaces=(
            snappy.Surface(
                name="symmetry",
                file="symmetry.stl",
                region="symmetry",
                patch_type="symmetry",
            ),
        )
    )
    files = snappy.write(snap)
    text = squashed(files["system/snappyHexMeshDict"])
    assert "symmetry" in text and "type symmetry;" in text
    assert snappy.patch_types(snap) == {"symmetry": "symmetry"}


def test_no_fvSchemes_or_fvSolution_because_nothing_reads_them() -> None:
    """**Measured**: snappyHexMesh, blockMesh, surfaceFeatures and checkMesh read `controlDict` and
    their own dicts. The tutorials carry the solver's files because they go on to solve."""
    files = snappy.write(a_snapshot())
    assert "system/fvSchemes" not in files
    assert "system/fvSolution" not in files


def test_the_dictionaries_carry_a_FoamFile_header_with_their_object_name() -> None:
    """OpenFOAM reads the header's `object` and refuses a mismatch, so a copy-paste error here is a
    hard failure rather than a silent one."""
    files = snappy.write(a_snapshot())
    for path, obj in (
        ("system/blockMeshDict", "blockMeshDict"),
        ("system/surfaceFeaturesDict", "surfaceFeaturesDict"),
        ("system/snappyHexMeshDict", "snappyHexMeshDict"),
    ):
        text = files[path]
        assert "FoamFile" in text
        assert f"object {obj};" in squashed(text), f"{path} names the wrong object"
        assert 'location "system";' in squashed(text)


# ------------------------------------------------------------------ the two keys that were wrong


def test_curvatureLevel_is_NEVER_emitted_because_OF14_does_not_have_it() -> None:
    """**The correction that matters most.** `grep -rn "curvatureLevel" src/` over the OF14 tree
    returns nothing, so an emitted `curvatureLevel` is silently ignored: no error, just a mesh that
    looks unrefined. Curvature refinement is `resolveFeatureAngle` + `level (min max)`."""
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "curvatureLevel" not in text
    assert "resolveFeatureAngle 30.0;" in squashed(text), (
        "the angle that does the curvature work is missing"
    )


def test_the_inside_point_is_insidePoint_not_locationInMesh() -> None:
    """Both are accepted (`insidePoints` > `insidePoint` > `locationInMesh`), but `insidePoint` is
    what every OF14 tutorial uses and is the documented form for this version."""
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "insidePoint (0.0 0.0 0.0);" in squashed(text)
    assert "locationInMesh" not in text


# ------------------------------------------------------------------ the surfaces and patch names


def test_each_surface_becomes_a_triSurface_in_the_geometry_block() -> None:
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "type triSurface;" in squashed(text)
    assert 'file "inlet.stl";' in squashed(text)


def test_a_region_names_the_patch_so_the_users_tag_survives() -> None:
    """**Without the `regions` block the patch is named after the file stem**, so a tag called
    `inlet` would appear as `inlet.stl` and the boundary conditions written for `inlet` would match
    no patch."""
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "name inlet;" in squashed(text)


def test_the_level_pair_is_written_for_every_surface() -> None:
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "level (2 3);" in squashed(text)
    assert "level (1 2);" in squashed(text)


def test_the_patch_type_comes_from_the_boundary_kind() -> None:
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "type patch;" in squashed(text)
    assert "type wall;" in squashed(text)


# ------------------------------------------------------------------ the required snap keys


def test_every_snap_control_OF14_REQUIRES_is_present() -> None:
    """`snapParameters.C:33-36` reads these four with `lookup`, not `lookupOrDefault`: a dictionary
    missing one is a hard failure at run time, on the host, with the user waiting."""
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    for key in ("nSmoothPatch", "tolerance", "nSolveIter", "nRelaxIter"):
        assert f"{key} " in text, f"snapControls is missing the required key {key}"


def test_both_feature_snap_switches_are_written_explicitly() -> None:
    """`implicitFeatureSnap` defaults to FALSE in OF14 and we want it on. `explicitFeatureSnap`
    defaults to true; written anyway so the dictionary records the intent rather than relying on a
    reader knowing the default."""
    text = snappy.write(a_snapshot())["system/snappyHexMeshDict"]
    assert "implicitFeatureSnap true;" in text
    assert "explicitFeatureSnap true;" in text


# ------------------------------------------------------------------ surfaceFeatures


def test_surfaceFeatures_uses_an_INCLUDED_angle_of_150() -> None:
    """**150, not 30.** The utility measures the angle between the two faces, so 150 is the
    complement of a 30-degree dihedral - and 150 is what OF14's own propeller tutorial uses. A
    writer that passed `feature_angle` (30) straight through would extract almost nothing and the
    mesh would lose its sharp edges with no error."""
    text = snappy.write(a_snapshot())["system/surfaceFeaturesDict"]
    assert "includedAngle 150.0;" in squashed(text)
    assert "includedAngle 30" not in text


def test_surfaceFeatures_lists_every_surface() -> None:
    text = snappy.write(a_snapshot())["system/surfaceFeaturesDict"]
    assert '"inlet.stl"' in text
    assert '"walls.stl"' in text


# ------------------------------------------------------------------ the background block


def test_the_background_is_one_hex_with_the_requested_cell_counts() -> None:
    text = snappy.write(a_snapshot())["system/blockMeshDict"]
    assert "hex (0 1 2 3 4 5 6 7) (12 8 8) simpleGrading (1 1 1)" in text


def test_the_background_patches_are_type_patch() -> None:
    """Measured against `motorBike/system/blockMeshDict:35-55`. snappy replaces them from the
    surfaces, so the name is only a placeholder until then."""
    text = snappy.write(a_snapshot())["system/blockMeshDict"]
    assert "type patch;" in squashed(text)
    # **The faces must be listed**, measured: a boundary entry without them makes blockMesh fail
    # with `keyword faces is undefined in dictionary "background"`, and snappy then finds no
    # polyMesh at all. Six face index tuples, one per hex side.
    assert "faces" in squashed(text)
    assert "(0 3 7 4)" in squashed(text)


def test_paths_use_forward_slashes_so_a_Windows_case_still_parses() -> None:
    """A backslash inside an OpenFOAM dictionary is an escape sequence, so a Windows path would
    break a case that works on Linux - the one platform difference that could cost the owner a
    working Save as."""
    assert snappy.foam_path("constant\\geometry\\inlet.stl") == "constant/geometry/inlet.stl"
    assert snappy.foam_path("a/b.stl") == "a/b.stl"


# ------------------------------------------------------------------ validation


def test_a_case_with_no_surfaces_is_refused_by_name() -> None:
    with pytest.raises(MeshInvalid, match="no surfaces"):
        snappy.write(a_snapshot(surfaces=()))


def test_an_inverted_level_pair_is_refused() -> None:
    bad = snappy.Surface(name="s", file="s.stl", level_min=3, level_max=1)
    with pytest.raises(MeshInvalid, match="at least the min"):
        snappy.write(a_snapshot(surfaces=(bad,)))


def test_an_inside_point_OUTSIDE_the_background_box_is_refused() -> None:
    """**The failure this check exists for.** snappy keeps the cells reachable from the point; a
    point outside the box means no cells, and the utility's own error talks about the mesh rather
    than about the point."""
    with pytest.raises(MeshInvalid, match="outside the background box"):
        snappy.write(a_snapshot(inside_point=(99.0, 0.0, 0.0)))


def test_an_empty_background_box_is_refused() -> None:
    with pytest.raises(MeshInvalid, match="empty or inverted"):
        snappy.write(a_snapshot(background_max=(-1.0, 1.0, 1.0)))


def test_a_background_over_the_cell_limit_is_refused_with_both_numbers() -> None:
    with pytest.raises(MeshInvalid, match="over the limit"):
        snappy.write(a_snapshot(background_cells=(200, 200, 200), max_cells=1_000_000))


def test_a_zero_cell_axis_is_refused() -> None:
    with pytest.raises(MeshInvalid, match="at least one cell"):
        snappy.write(a_snapshot(background_cells=(0, 8, 8)))


# ------------------------------------------------------------------ the steps


def test_the_steps_run_in_the_order_the_case_needs() -> None:
    """`blockMesh` makes the background, `surfaceFeatures` writes the `.eMesh` the dict's
    `features` reads, then snappy, then the quality check."""
    names = [step["name"] for step in snappy.steps(a_snapshot())]
    assert names == ["blockMesh", "surfaceFeatures", "snappyHexMesh", "checkMesh"]


def test_snappyHexMesh_runs_with_NO_flag_because_overwrite_is_the_default() -> None:
    """Measured against `tests/fixtures/of14/help/snappyHexMesh.txt`: `-overwrite` is listed as
    "Deprecated option, this is now default behaviour"."""
    steps = {step["name"]: step for step in snappy.steps(a_snapshot())}
    assert steps["snappyHexMesh"]["argv"] == ["snappyHexMesh"]
    assert "-overwrite" not in steps["snappyHexMesh"]["argv"]


def test_checkMesh_carries_the_parser_the_reports_need() -> None:
    steps = {step["name"]: step for step in snappy.steps(a_snapshot())}
    assert steps["checkMesh"].get("parse") == "checkMesh"


def test_parallel_snappy_decomposes_and_reconstructs_before_checking() -> None:
    snap = a_snapshot(cores=4)
    files = snappy.write(snap)
    assert "numberOfSubdomains 4;" in files["system/decomposeParDict"]
    run_steps = snappy.steps(snap)
    assert [step["name"] for step in run_steps] == [
        "blockMesh",
        "surfaceFeatures",
        "decomposePar",
        "snappyHexMesh",
        "reconstructPar",
        "checkMesh",
    ]
    assert run_steps[2]["argv"] == ["decomposePar", "-noFields"]
    assert run_steps[3]["ranks"] == 4
    assert run_steps[3]["argv"] == ["snappyHexMesh"]
    assert run_steps[4]["argv"] == ["reconstructPar", "-constant", "-noFields"]


def test_box_snappy_parallel_basis_tracks_ranks_without_surfaces() -> None:
    from stokes.mesh.basis import mesh_basis_parts

    box = a_snapshot(surfaces=(), features=(), stl_files={}, cores=1)
    parallel_box = dataclasses.replace(box, cores=4)
    serial_parts = mesh_basis_parts(box, snappy.DRIVER_VERSION, "org-14")
    parallel_parts = mesh_basis_parts(parallel_box, snappy.DRIVER_VERSION, "org-14")
    assert serial_parts["mesher"] != parallel_parts["mesher"]


# ------------------------------------------------------------------ the helpers


def test_snapped_levels_returns_a_pair_per_surface() -> None:
    pairs = snappy.snapped_levels(0.008, (("a", 0.002), ("b", 0.004)))
    assert pairs == ((2, 3), (1, 2))


def test_level_warnings_name_the_surface_and_both_sizes() -> None:
    """A level is an integer, so 3 mm from an 8 mm base cannot be hit; the warning says what was
    asked for and what will actually be built."""
    warnings = snappy.level_warnings(0.008, (("walls", 0.003),))
    assert len(warnings) == 1
    assert "walls" in warnings[0]
    assert "3.000 mm" in warnings[0]
    assert "4.000 mm" in warnings[0]


def test_level_warnings_is_silent_when_the_size_is_hit_exactly() -> None:
    assert snappy.level_warnings(0.008, (("walls", 0.002),)) == []


def test_the_driver_version_is_named_so_a_cached_mesh_is_not_confused() -> None:
    """It enters the mesh basis, so a mesh built by an older writer is not reused as this one's.

    **`snappy/3`** (brief M-504): the layer pass — the resolved `addLayersControls`, the relaxed
    quality rules beside it, and the parsed coverage table. A mesh whose basis was computed by
    `snappy/2` (which wrote the empty layers block whatever anyone asked) must be regenerated
    once rather than mistaken for current.
    """
    assert snappy.DRIVER_VERSION == "snappy/5"


def test_the_geometry_dir_is_constant_geometry_not_triSurface() -> None:
    """**Measured**: the OF14 motorBike case has `constant/geometry/` and no `constant/triSurface/`,
    and its dict says feature edges are "read from constant/geometry for now"."""
    assert snappy.GEOMETRY_DIR == "constant/geometry"


def test_the_foam_dict_helper_is_still_reachable_for_other_writers() -> None:
    """A cheap guard that this module did not quietly re-implement the writer: the header it emits
    is the shared one."""
    assert "FoamFile" in render_file("X", snappy.Dict(()))


# ------------------------------------------------------------------ the layer pass (M-504)


def test_no_layers_is_BYTE_IDENTICAL_to_the_dictionary_before_the_layer_controls() -> None:
    """**The guarantee the layer work stands on**: a project that never asked for layers gets
    the dictionary it always got, byte for byte. The golden was captured from the writer as it
    was before the layer pass (commit before the M-504 driver work), by running the same
    snapshot through it.
    """
    from pathlib import Path

    golden = Path(__file__).parent / "golden" / "mesh" / "snappyHexMeshDict.nolayers"
    assert snappy.write(a_snapshot())["system/snappyHexMeshDict"] == golden.read_text()


def test_layers_three_on_the_wall_writes_the_block_and_not_the_other_patches() -> None:
    """Settings 3 on walls: `addLayers true`, `walls` carries `nSurfaceLayers 3`, and the inlet
    patch — not a wall — carries no entry at all (snappy gives a patch missing from `layers {}`
    none, which is the decision: inlets and outlets never inherit)."""
    snap = a_snapshot(layer_counts={"walls": 3})
    text = squashed(snappy.write(snap)["system/snappyHexMeshDict"])
    assert "addLayers true;" in text
    assert "relativeSizes true;" in text
    assert "expansionRatio 1.2;" in text
    assert "finalLayerThickness 0.3;" in text
    assert "minThickness 0.1;" in text
    assert "featureAngle 100;" in text
    assert "nSurfaceLayers 3" in text
    # The keys nobody sets, at the tutorial values the inventory records.
    assert "nGrow 0;" in text and "nLayerIter 50;" in text
    assert "nRelaxedIter 20;" in text and "nRelaxIter 3;" in text
    assert "minMedianAxisAngle 90;" in text
    # ...and the block is under the wall's own entry, not a global count.
    assert "walls\n {\n nSurfaceLayers 3;\n }" in text


def test_an_override_of_zero_writes_no_entry() -> None:
    """Snappy has no "zero layers" spelling - a patch missing from `layers {}` gets none - so
    an explicit 0 leaves the patch out, while the count table still records the decision."""
    snap = a_snapshot(layer_counts={"walls": 5, "cap": 0})
    text = squashed(snappy.write(snap)["system/snappyHexMeshDict"])
    assert "nSurfaceLayers 5" in text
    assert "cap" not in text.split("layers")[1].split("}")[0]


def test_first_height_mode_writes_the_ABSOLUTE_keys() -> None:
    """`first_height` mode: `relativeSizes false` and `firstLayerThickness` in metres, with
    `minThickness` scaled by the first layer height - with `relativeSizes false` snappy reads
    `minThickness` as a LENGTH, and the raw ratio would put a 0.1 m floor on a millimetre part
    and silently remove every layer."""
    snap = a_snapshot(
        layer_counts={"walls": 3},
        layer_settings={
            "mode": "first_height",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "first_layer_height": 0.0002,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
    )
    text = squashed(snappy.write(snap)["system/snappyHexMeshDict"])
    assert "relativeSizes false;" in text
    assert "firstLayerThickness 0.0002;" in text
    assert "finalLayerThickness" not in text
    assert "minThickness 2e-05;" in text  # the ratio, scaled by the height


def test_first_height_mode_WITHOUT_a_height_is_refused() -> None:
    """A height of 0 asks for layers with no thickness; the refusal names the setting."""
    snap = a_snapshot(
        layer_counts={"walls": 3},
        layer_settings={
            "mode": "first_height",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "first_layer_height": 0.0,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
    )
    with pytest.raises(MeshInvalid, match="needs a first layer height"):
        snappy.write(snap)


def test_the_relaxed_quality_rules_come_only_WITH_layers() -> None:
    """`relaxed { maxNonOrtho 75; }` in `meshQualityControls` is read only during the layer pass
    (`snappyLayerDriver.C:3145`), so with no layers it is dead text and stays out - and the
    no-layer golden above holds. With layers it is there, beside `nRelaxedIter`."""
    without = squashed(snappy.write(a_snapshot())["system/snappyHexMeshDict"])
    assert "relaxed" not in without
    with_layers = squashed(
        snappy.write(a_snapshot(layer_counts={"walls": 3}))["system/snappyHexMeshDict"]
    )
    assert "relaxed\n {\n maxNonOrtho 75;\n }" in with_layers


def test_every_layer_dictionary_passes_the_agent_lint() -> None:
    """The corpus rule (README): a generated dictionary the agent would stage is one the lint
    accepts. Both modes and the no-layer block, over the four files each write emits."""
    from stokes_agent.lint import lint_text

    cases = [
        a_snapshot(),  # no layers: the empty block
        a_snapshot(layer_counts={"walls": 3}),  # final_ratio mode
        a_snapshot(
            layer_counts={"walls": 3},
            layer_settings={
                "mode": "first_height",
                "expansion_ratio": 1.2,
                "final_ratio": 0.3,
                "first_layer_height": 0.0002,
                "min_thickness_ratio": 0.1,
                "feature_angle": 100.0,
            },
        ),
    ]
    for snap in cases:
        for rel, text in snappy.write(snap).items():
            found = lint_text(rel, text)
            assert not found, f"{rel}: {found[0].to_json()}"


def test_the_layers_part_of_the_basis_moves_with_the_counts_and_only_then() -> None:
    """The basis's `layers` part (brief M-501's stub, filled by M-504): emitted only when a
    count is above zero, so a count change moves exactly that part and a mode change with no
    layers moves nothing."""
    from stokes.mesh.basis import mesh_basis_parts

    bare = a_snapshot()
    parts = mesh_basis_parts(bare, snappy.DRIVER_VERSION, "org-14")
    assert "layers" not in parts

    three = dataclasses.replace(bare, layer_counts={"walls": 3})
    with_layers = mesh_basis_parts(three, snappy.DRIVER_VERSION, "org-14")
    assert "layers" in with_layers

    five = dataclasses.replace(three, layer_counts={"walls": 5})
    with_five = mesh_basis_parts(five, snappy.DRIVER_VERSION, "org-14")
    assert with_layers["layers"] != with_five["layers"]

    # A mode change with no counts: nothing to hash, no part appears.
    quiet = dataclasses.replace(
        bare, layer_settings={"mode": "first_height", "first_layer_height": 0.001}
    )
    assert "layers" not in mesh_basis_parts(quiet, snappy.DRIVER_VERSION, "org-14")


# --------------------------------------------------------- the F1 security fixes (M-504 review)


def test_a_fully_overlapped_tag_gets_NO_layer_entry() -> None:
    """**The F1 correctness half**: a tag whose faces another tag claimed has no surface, so
    snappy could not grow layers on it — and the first draft wrote the entry anyway (snappy
    warns "does not match any patch" and moves on). The writer drops it."""
    snap = a_snapshot(layer_counts={"walls": 3, "ghost": 3})
    text = squashed(snappy.write(snap)["system/snappyHexMeshDict"])
    assert "ghost" not in text
    assert "nSurfaceLayers 3" in text


def test_a_layer_key_that_is_not_a_bare_word_is_REFUSED() -> None:
    """**The F1 injection half**: a name from a loaded `project.json` (which validates no names)
    reached the dictionary unchecked — `$FOAM_CASE`, `"(.*)"` (layers on EVERY patch) and `a;b`
    passed the lint as keys, measured by the review's probes. Each is refused with a typed error
    before any text is written."""
    for bad in ("$FOAM_CASE", '"(.*)"', "a;b", "wall\n", "${FOAM_CASE}"):
        snap = a_snapshot(layer_counts={bad: 3})
        with pytest.raises(MeshInvalid, match="not a bare patch name"):
            snappy.write(snap)


def test_loaded_values_that_skipped_the_hooks_are_REFUSED() -> None:
    """**The F2 injection half**: a loaded `project.json` validates no parameters, so the ranges
    the hooks enforce on `parameter.set` must be re-checked where the dictionary is written.
    The review measured `nSurfaceLayers 1000000000`, `expansionRatio 1e9` and `minThickness -1`
    reaching the dictionary from one."""
    base = {
        "mode": "final_ratio",
        "expansion_ratio": 1.2,
        "final_ratio": 0.3,
        "first_layer_height": 0.0,
        "min_thickness_ratio": 0.1,
        "feature_angle": 100.0,
    }
    # **Every case carries a count, or the writer's early return (nothing to write) skips the
    # validation entirely** — that IS the contract: no layers, nothing to check.
    cases = [
        ("a huge count", a_snapshot(layer_counts={"walls": 1_000_000_000})),
        (
            "a NaN expansion",
            a_snapshot(
                layer_counts={"walls": 3}, layer_settings={**base, "expansion_ratio": float("nan")}
            ),
        ),
        (
            "a 1e9 expansion",
            a_snapshot(layer_counts={"walls": 3}, layer_settings={**base, "expansion_ratio": 1e9}),
        ),
        (
            "a negative minimum",
            a_snapshot(
                layer_counts={"walls": 3}, layer_settings={**base, "min_thickness_ratio": -1}
            ),
        ),
        (
            "a NaN feature angle",
            a_snapshot(
                layer_counts={"walls": 3}, layer_settings={**base, "feature_angle": float("nan")}
            ),
        ),
        (
            "a bogus mode",
            a_snapshot(layer_counts={"walls": 3}, layer_settings={**base, "mode": "bogus"}),
        ),
    ]
    for what, snap in cases:
        with pytest.raises(MeshInvalid, match=r"must be|is not one of"):
            snappy.write(snap), what


def test_a_bogus_mode_cannot_bypass_the_first_height_guard() -> None:
    """**The mode predicate is ONE** (the review's F2): the validator and the writer must test
    the mode the same way, or an unknown mode skips the guard while the writer treats it as
    absolute — `relativeSizes false` with a zero height, measured by the review."""
    snap = a_snapshot(
        layer_counts={"walls": 3},
        layer_settings={
            "mode": "bogus",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "first_layer_height": 0.0,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
    )
    with pytest.raises(MeshInvalid, match="is not one of"):
        snappy.write(snap)


def test_first_height_mode_with_a_height_still_writes() -> None:
    """The validation must not refuse the honest case: first-height mode with a finite,
    positive height writes the absolute dictionary."""
    snap = a_snapshot(
        layer_counts={"walls": 3},
        layer_settings={
            "mode": "first_height",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "first_layer_height": 0.0002,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
    )
    text = squashed(snappy.write(snap)["system/snappyHexMeshDict"])
    assert "relativeSizes false;" in text
    assert "firstLayerThickness 0.0002;" in text


def test_the_background_grid_is_anchored_at_the_part() -> None:
    """The elbow's bbox (x ±200, y -225..200, z 0..50 mm) at 5 mm: every bounding plane of the part
    is a grid plane, the spacing is exactly the base size, and the pad is at least what was asked.
    The old box put z = 0 a third of the way into a cell and the symmetry plane snapped bumpy."""
    from stokes.mesh.snapshot import _aligned_box, _snappy_cells

    bbox = (-0.2, -0.225, 0.0, 0.2, 0.2, 0.05)
    box = _aligned_box(bbox, 0.05, 0.005)
    counts = _snappy_cells(box, 0.005, 5_000_000)
    for axis in range(3):
        low, high = box[axis], box[axis + 3]
        assert low <= bbox[axis] - 0.05 + 1e-12 and high >= bbox[axis + 3] + 0.05 - 1e-12
        assert (high - low) / counts[axis] == pytest.approx(0.005)
        for plane in (bbox[axis], bbox[axis + 3]):
            steps = (plane - low) / 0.005
            assert steps == pytest.approx(round(steps), abs=1e-9)
