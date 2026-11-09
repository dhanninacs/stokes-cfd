"""The ``extract_fluid`` operation: cavities, and regions around a seed (ADR-0021 decision 6.1;
T-026). The two modes run in this process with ``run_op``, so the geometry is tested without a
session; the last test creates one through a session and undoes it."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.errors import GeometryError, OperationInvalid, SeedOutside
from stokes.geometry.operations.base import OpInput
from stokes.geometry.operations.faces import map_history
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import create_and_wait, part_id, run_op

MM3 = 1e-9  # m^3 per mm^3
RADIUS, LENGTH = 5.0, 30.0  # the tee every region test uses
# The three cylinders' union in mm^3: the run, the branch, minus half a Steinmetz bicylinder.
UNION_MM3 = math.pi * RADIUS**2 * LENGTH + math.pi * RADIUS**2 * (LENGTH / 2) - 8 * RADIUS**3 / 3


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    """One worker kept ahead for the session test, as the service does."""
    worker.prewarm(1)
    yield None
    worker.shutdown_pool()


def disk(radius: float, at: tuple[float, float, float], axis: tuple[float, float, float]) -> Any:
    """A planar face (a hand-made cap) of radius ``radius`` at ``at``, normal to ``axis``."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    circle = gp_Circ(gp_Ax2(gp_Pnt(*at), gp_Dir(*axis)), radius)
    wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circle).Edge()).Wire()
    return BRepBuilderAPI_MakeFace(wire).Face()


# --------------------------------------------------------------------- cavity
def test_a_cavity_is_the_inner_void_of_a_hollow_box() -> None:
    hollow = fx.hollow_box((20.0, 20.0, 20.0), (10.0, 10.0, 10.0))
    result = run_op("extract_fluid", [("block", hollow)], {"mode": "cavity"})
    output = result.outputs[0]
    assert output.name == "block_fluid"  # <input>_fluid, the first and only cavity
    after = output.report["check_after"]
    assert after["valid"] and after["closed"] and after["solids"] == 1
    assert after["volume"] == pytest.approx(1000.0 * MM3, rel=1e-6)  # the inner box, positive
    assert result.report["mode"] == "cavity" and result.report["capped"] == 0
    row = result.report["cavities"][0]
    assert row["name"] == "block_fluid" and len(row["centroid"]) == 3
    assert row["volume"] == pytest.approx(1000.0 * MM3, rel=1e-6)
    assert result.report["check_before"][0]["volume"] == pytest.approx(7000.0 * MM3, rel=1e-6)
    assert result.warnings == []
    # The void's faces are the input's inner faces: the history maps those six, and only those.
    inputs = [OpInput(part_id(0), "block", hollow)]
    mapped = map_history(output.history, inputs, output.shape)[part_id(0)]
    assert sorted(mapped) == [6, 7, 8, 9, 10, 11]
    assert {i for ids in mapped.values() for i in ids} == set(range(after["faces"]))


def test_a_cavity_below_min_volume_is_skipped_and_then_reported() -> None:
    hollow = fx.hollow_box((20.0, 20.0, 20.0), (10.0, 10.0, 10.0))
    kept = run_op("extract_fluid", [("block", hollow)], {"mode": "cavity", "min_volume": 9e-7})
    assert len(kept.outputs) == 1  # 1000 mm^3 is 1e-6 m^3: 0.9e-6 keeps it, 2e-6 skips it
    with pytest.raises(GeometryError, match="no closed cavity in block") as caught:
        run_op("extract_fluid", [("block", hollow)], {"mode": "cavity", "min_volume": 2e-6})
    assert caught.value.code == "geometry.error"


def test_a_body_without_a_cavity_is_an_error() -> None:
    with pytest.raises(GeometryError, match="no closed cavity in body_0"):
        run_op("extract_fluid", [fx.box(10.0, 10.0, 10.0)], {"mode": "cavity"})


# --------------------------------------------------------------------- region
def test_the_region_is_the_three_cylinders_the_tee_is_made_of() -> None:
    result = run_op("extract_fluid", [("tee", fx.pipe_tee(RADIUS, LENGTH))], {"seed": [0, 0, 0]})
    output = result.outputs[0]
    assert output.name == "tee_fluid"
    after = output.report["check_after"]
    assert after["valid"] and after["closed"] and after["solids"] == 1
    assert after["volume"] == pytest.approx(UNION_MM3 * MM3, rel=0.01)
    assert result.report["capped"] == 3 and result.report["uncapped"] == 0
    candidates = result.report["candidates"]
    assert len(candidates) == 1  # the tee's faces bound one volume
    assert candidates[0]["volume"] == pytest.approx(UNION_MM3 * MM3, rel=0.01)
    assert len(candidates[0]["centroid"]) == 3
    caps = output.tags["caps"]
    assert len(caps) == 3  # the three openings, tagged so the user can name them
    assert sum(f.IsSame(g) for f in caps for g in caps) == 3  # three distinct faces


def test_a_seed_outside_every_volume_lists_the_candidates() -> None:
    tee = fx.pipe_tee(RADIUS, LENGTH)
    with pytest.raises(SeedOutside) as caught:
        run_op("extract_fluid", [("tee", tee)], {"seed": [0.0, 0.05, 0.0]})  # 50 mm off the axis
    assert caught.value.code == "geometry.seed_outside"
    candidates = caught.value.details["candidates"]
    assert len(candidates) == 1  # the tee bounds one volume
    assert candidates[0]["volume"] == pytest.approx(UNION_MM3 * MM3, rel=0.01)
    assert len(candidates[0]["centroid"]) == 3
    assert caught.value.details["seed"] == [0.0, 0.05, 0.0]


def test_caps_from_a_tag_seal_the_openings_without_auto_cap() -> None:
    tee = fx.pipe_tee(RADIUS, LENGTH)
    half = LENGTH / 2.0
    caps = [
        disk(RADIUS, (-half, 0.0, 0.0), (1.0, 0.0, 0.0)),
        disk(RADIUS, (half, 0.0, 0.0), (1.0, 0.0, 0.0)),
        disk(RADIUS, (0.0, 0.0, half), (0.0, 0.0, 1.0)),
    ]
    members = [{"part": part_id(i + 1), "faces": [0]} for i in range(3)]
    shapes = [("tee", tee), *[(f"cap_{i}", cap) for i, cap in enumerate(caps)]]
    result = run_op(
        "extract_fluid",
        shapes,
        {"auto_cap": False, "caps": "n_000000ff", "caps_members": members, "seed": [0, 0, 0]},
    )
    output = result.outputs[0]
    assert result.report["capped"] == 0 and result.report["uncapped"] == 0
    assert output.report["check_after"]["volume"] == pytest.approx(UNION_MM3 * MM3, rel=0.01)
    assert len(output.tags["caps"]) == 3


def test_a_cap_tag_naming_a_part_that_is_not_an_input_is_refused() -> None:
    with pytest.raises(OperationInvalid, match="not an input"):
        run_op(
            "extract_fluid",
            [("tee", fx.pipe_tee(RADIUS, LENGTH))],
            {"caps_members": [{"part": "n_0000ffff", "faces": [0]}]},
        )


# --------------------------------------------------------------------- session
def test_the_fluid_and_its_caps_come_out_of_a_session(tmp_path: Path) -> None:
    source = fx.write_brep(fx.pipe_tee(RADIUS, LENGTH), tmp_path / "tee.brep")
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    imported = session.execute("geometry.import", path=str(source), wait=True)
    op = create_and_wait(session, "extract_fluid", [imported["id"]], {"seed": [0.0, 0.0, 0.0]})
    assert op["status"] == "ok", op["error"]
    project = session.require_project()
    out = op["outputs"][0]
    part = project.get(out)
    assert part.params["derived"] == op["id"] and part.params["units"] == "mm"
    assert part.params["bbox"][3] == pytest.approx(LENGTH / 2000.0, abs=1e-6)
    created = list(project.get(op["id"]).meta["created_tags"])
    assert len(created) == 1
    tag = project.get(created[0])
    assert tag.name == "caps"
    members = session.execute("tag.members", tag=created[0])["members"]
    assert len(members) == 1 and len(members[0]["faces"]) == 3
    session.execute("history.undo")
    assert project.find(op["id"]) is None and project.find(out) is None
    assert project.find(created[0]) is None
    GeometryStore.of(session).close()
