"""The ``heal``, ``sew``, ``orient`` and ``solidify`` operations (ADR-0021 decision 5; T-027).

The four run in this process with ``run_op``, so the geometry is tested without a session or a
worker; the last test sews a tagged body through a session and checks the tag follows the seam.
The free-edge counts are the operations' own, which walk the faces of a compound (see
``repair_common``): the batch 4 review found that ``geometry.check`` reports an unsewn compound of
loose faces as closed, and the first ``sew`` test pins that gap so a fix here is noticed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.errors import GeometryError
from stokes.geometry.operations import repair_common
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import box_part, create_and_wait, run_op

MM3 = 1e-9  # m^3 per mm^3
BOX_MM3 = 8000.0  # the 20 mm fixture box, in mm^3


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    """One worker kept ahead for the session test, as the service does."""
    worker.prewarm(1)
    yield None
    worker.shutdown_pool()


def check_report(report: dict[str, Any], op_type: str) -> None:  # Any: JSON
    """The keys every repair report carries: the framework's two checks and the op's own counts."""
    assert {"check_before", "check_after"} <= set(report)
    assert report["check_before"], "check_before names the inputs"
    assert report["check_after"]["valid"]


# --------------------------------------------------------------------- heal
def test_heal_clamps_the_largest_tolerance_and_keeps_the_body() -> None:
    shape = fx.tolerant_box(size=(20.0, 20.0, 20.0), tolerance=5.0)
    result = run_op("heal", [("block", shape)])
    output = result.outputs[0]
    assert output.name == "block_healed"
    after = output.report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    report = result.report
    check_report(report, "heal")
    for key in ("wires_fixed", "faces_fixed", "shells_fixed", "small_edges_dropped"):
        assert report[key] in (0, 1), key
    assert report["max_tolerance_before"] == pytest.approx(5.0 / 1000.0)
    assert report["max_tolerance_after"] <= report["max_tolerance"] + 1e-12
    assert report["max_tolerance_after"] < report["max_tolerance_before"]
    assert report["changed"] is True


def test_heal_takes_no_tolerance_off_a_body_that_has_none_to_take() -> None:
    result = run_op("heal", [("block", fx.box(20.0, 20.0, 20.0))])
    report = result.report
    check_report(report, "heal")
    assert report["max_tolerance_after"] <= report["max_tolerance"]
    assert report["small_edges"] == 0.0 and report["small_edges_dropped"] == 0
    assert result.outputs[0].report["check_after"]["volume"] == pytest.approx(BOX_MM3 * MM3)


# --------------------------------------------------------------------- sew
def test_sew_closes_a_compound_of_loose_faces() -> None:
    result = run_op("sew", [("block", fx.loose_faces())])
    output = result.outputs[0]
    assert output.name == "block_sewn"
    after = output.report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6 and after["solids"] == 1
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    report = result.report
    check_report(report, "sew")
    assert (report["shells"], report["solids"]) == (1, 1)
    assert report["degenerated"] == 0 and report["multiple_edges"] == 0
    # geometry.check's own count misses every one of the 24 free edges of an unsewn compound (the
    # gap the batch 4 review found); the op's count over the compound's faces sees them, and sees
    # none left once the faces are sewn into a shell.
    assert report["check_before"][0]["free_edges"] == 0
    assert report["free_edges_before"] == 24
    assert report["free_edges_after"] == 0


def test_sew_without_make_solid_leaves_a_shell() -> None:
    result = run_op("sew", [("block", fx.loose_faces())], {"make_solid": False})
    after = result.outputs[0].report["check_after"]
    assert after["valid"] and after["faces"] == 6 and after["shells"] == 1 and after["solids"] == 0
    assert result.report["make_solid"] is False
    assert result.report["free_edges_after"] == 0


def test_sew_keeps_an_open_shell_open() -> None:
    result = run_op("sew", [("block", fx.open_box())], {"non_manifold": True})
    output = result.outputs[0]
    assert output.report["check_after"]["shells"] == 1
    assert result.report["solids"] == 0 and result.report["free_edges_after"] == 4
    assert result.report["non_manifold"] is True


# --------------------------------------------------------------------- orient
def test_orient_turns_an_inside_out_box_around() -> None:
    shape = fx.reversed_box()
    assert repair_common.volume(shape) < 0.0
    result = run_op("orient", [("block", shape)])
    output = result.outputs[0]
    assert output.name == "block_oriented"
    after = output.report["check_after"]
    assert after["valid"] and after["solids"] == 1
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    check_report(result.report, "orient")
    assert result.report["solids_flipped"] == 1
    assert result.report["faces_reoriented"] == 6  # its shell flipped: all six faces with it


def test_orient_leaves_a_solid_that_is_already_right_alone() -> None:
    result = run_op("orient", [("block", fx.box(20.0, 20.0, 20.0))])
    assert result.report["solids_flipped"] == 0 and result.report["faces_reoriented"] == 0
    assert result.report["check_after"]["volume"] == pytest.approx(BOX_MM3 * MM3)


def test_orient_keeps_the_faces_no_shell_holds() -> None:
    result = run_op("orient", [("block", fx.loose_faces())])
    after = result.outputs[0].report["check_after"]
    assert after["valid"] and after["faces"] == 6 and after["shells"] == 0 and after["solids"] == 0
    assert result.report["solids_flipped"] == 0 and result.report["faces_reoriented"] == 0


# --------------------------------------------------------------------- solidify
def test_solidify_makes_one_solid_of_a_box_of_loose_faces() -> None:
    result = run_op("solidify", [("block", fx.loose_faces())])
    assert [output.name for output in result.outputs] == ["block_solid"]
    after = result.outputs[0].report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6 and after["solids"] == 1
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    check_report(result.report, "solidify")
    assert result.report["solids"] == 1


def test_solidify_makes_two_solids_of_two_separate_boxes() -> None:
    apart = fx.compound([fx.loose_faces(), fx.moved(fx.loose_faces(), 50.0, 0.0, 0.0)])
    result = run_op("solidify", [("pair", apart)])
    assert [output.name for output in result.outputs] == ["pair_solid", "pair_solid_2"]
    assert result.report["solids"] == 2
    for output in result.outputs:
        check = output.report["check_after"]
        assert check["valid"] and check["solids"] == 1 and check["faces"] == 6
        assert check["volume"] == pytest.approx(BOX_MM3 * MM3)


def test_solidify_refuses_faces_that_bound_no_volume() -> None:
    with pytest.raises(GeometryError, match="no closed volume"):
        run_op("solidify", [("block", fx.open_box())])


# --------------------------------------------------------------------- session
def test_a_tag_follows_a_sew_through_a_session(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    block = box_part(session)
    session.execute("tag.create", name="top", members=[{"part": block, "faces": [5]}])
    op = create_and_wait(session, "sew", [block])
    assert op["status"] == "ok", op["error"]
    out = op["outputs"][0]
    project = session.require_project()
    assert project.get(out).path == "/geometry/parts/box_sewn"
    assert op["report"]["free_edges_after"] == 0
    members = session.execute("tag.members", tag="top")["members"]
    followed = [member for member in members if member["part"] == out]
    assert len(followed) == 1 and len(followed[0]["faces"]) == 1
    GeometryStore.of(session).close()
