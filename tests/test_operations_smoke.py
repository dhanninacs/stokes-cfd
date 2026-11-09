"""Every operation type end to end, through the API (M1 manual-pass checklist).

The operations each have their own suite; this is the "open it and click every entry" pass in the
form that can be repeated: one test per type of the `Prepare` menu, each run through
`operation.create` on a real session and judged on what a user would see — the run finished, the
output is a derived part with a mesh, its B-rep checks out, and the command that reads it back
(`operation.info`) reports it.

`test_geometry_check_follows_a_derived_part` is here too because it is the path a user takes after
every operation: the B-rep panel on a derived part.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.model import OPERATION_TYPE_NAMES
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import create_and_wait


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    worker.prewarm(2)
    yield None
    worker.shutdown_pool()


def session_with(root: Path, shapes: list[tuple[str, Any]]) -> tuple[Session, dict[str, str]]:
    """A session holding one part per (name, shape), written as a BREP and imported."""
    session = Session(journal_path=root / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(root / "demo"))
    ids: dict[str, str] = {}
    for name, shape in shapes:
        source = fx.write_brep(shape, root / f"{name}.brep")
        ids[name] = session.execute("geometry.import", path=str(source), wait=True)["id"]
    return session, ids


def opened(root: Path) -> tuple[Session, dict[str, str]]:
    """A box and a cylinder beside each other: enough for every operation type."""
    return session_with(
        root, [("box", fx.box(0.02, 0.02, 0.02)), ("pin", fx.cylinder(0.004, 0.04))]
    )


def check_output(session: Session, info: dict[str, Any], what: str) -> str:
    """The assertions every entry shares: it ran, it made a part, the part is sound."""
    assert info["status"] == "ok", f"{what}: {info.get('error')}"
    assert info["outputs"], f"{what}: no output part"
    output = info["outputs"][0]
    project = session.require_project()
    node = project.get(output)
    derived = node.params.get("derived")
    assert derived == info["id"], f"{what}: output is not derived from the op"
    described = session.execute("geometry.info", node=output)
    solids = described["solids"]
    assert solids, f"{what}: no mesh"
    assert sum(int(solid["faces"]) for solid in solids) > 0, f"{what}: the mesh has no faces"
    return str(output)


def check_closed(session: Session, part: str, what: str) -> None:
    """The B-rep of an output is a closed solid: `geometry.check` reports no free edges.

    There is no `closed` key in a check's `brep` findings — the watertightness shows as
    `free_edges` and `open_shells` both zero, which is what a user reads in the B-rep panel.
    """
    brep = session.execute("geometry.check", part=part, wait=True)["brep"]
    assert brep["checked"] is True, f"{what}: the B-rep was not checked"
    assert brep["valid"] is True, f"{what}: the B-rep is not valid"
    assert (brep["free_edges"], brep["open_shells"]) == (0, 0), (
        f"{what}: not closed: free_edges={brep['free_edges']} open_shells={brep['open_shells']}"
    )


DRY = [0.0, 0.0, 0.0]


def test_the_catalogue_lists_every_type_the_menu_offers() -> None:
    """The Prepare menu is built from `operation.types`: every declared type is there, and built."""
    session = Session()
    session.execute("project.new", name="demo")
    types = session.execute("operation.types")["types"]
    listed = {entry["type"] for entry in types}
    assert listed == set(OPERATION_TYPE_NAMES)
    assert all(entry["built"] for entry in types), [
        entry["type"] for entry in types if not entry["built"]
    ]
    assert all(entry["schema"]["properties"] for entry in types), "a type without a form"


def test_primitive_box_makes_a_part(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    info = create_and_wait(session, "primitive", [], {"shape": "box", "size": [0.01, 0.02, 0.03]})
    check_output(session, info, "primitive")
    GeometryStore.of(session).close()


def test_boolean_cut_uses_its_tool(tmp_path: Path) -> None:
    session, ids = opened(tmp_path)
    info = create_and_wait(session, "boolean", [ids["box"]], {"op": "cut", "tools": [ids["pin"]]})
    check_output(session, info, "boolean")
    GeometryStore.of(session).close()


def test_enclosure_wraps_a_body(tmp_path: Path) -> None:
    session, ids = opened(tmp_path)
    info = create_and_wait(session, "enclosure", [ids["box"]], {})
    output = check_output(session, info, "enclosure")
    tagged = [t["name"] for t in session.execute("tag.list")]
    assert {"inlet", "outlet", "far_field"} <= set(tagged), tagged
    check_closed(session, output, "enclosure")
    GeometryStore.of(session).close()


def test_enclosure_can_skip_the_body(tmp_path: Path) -> None:
    session, ids = opened(tmp_path)
    info = create_and_wait(session, "enclosure", [ids["box"]], {"subtract": False})
    check_output(session, info, "enclosure subtract=false")
    GeometryStore.of(session).close()


def test_extract_fluid_from_a_capped_open_body(tmp_path: Path) -> None:
    """The workflow the gate is named for: cap an open body, then take its fluid."""
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    source = fx.write_brep(fx.pipe_tee(6.0, 40.0), tmp_path / "tee.brep")
    pipe = session.execute("geometry.import", path=str(source), wait=True)["id"]
    capped = create_and_wait(session, "cap", [pipe])
    capped_out = check_output(session, capped, "cap")
    tag = str(session.require_project().get(capped["id"]).meta["created_tags"][0])
    fluid = create_and_wait(
        session, "extract_fluid", [capped_out], {"caps": tag, "seed": [0.0, 0.0, 0.0]}
    )
    output = check_output(session, fluid, "extract_fluid")
    assert fluid["status"] == "ok", fluid.get("error")
    check_closed(session, output, "extract_fluid")
    GeometryStore.of(session).close()


def test_extract_fluid_cavity_mode(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    source = fx.write_brep(
        fx.hollow_box((0.04, 0.04, 0.04), (0.02, 0.02, 0.02)), tmp_path / "h.brep"
    )
    body = session.execute("geometry.import", path=str(source), wait=True)["id"]
    info = create_and_wait(session, "extract_fluid", [body], {"mode": "cavity"})
    check_output(session, info, "extract_fluid cavity")
    GeometryStore.of(session).close()


def test_heal_keeps_the_body(tmp_path: Path) -> None:
    session, ids = opened(tmp_path)
    info = create_and_wait(session, "heal", [ids["box"]], {})
    check_output(session, info, "heal")
    GeometryStore.of(session).close()


def test_sew_closes_a_loose_face_compound(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    # `loose_faces` takes the box's size as a tuple, the way every fixture here does.
    source = fx.write_brep(fx.loose_faces((0.02, 0.02, 0.02)), tmp_path / "loose.brep")
    node = session.execute("geometry.import", path=str(source), wait=True)["id"]
    info = create_and_wait(session, "sew", [node], {})
    output = check_output(session, info, "sew")
    check_closed(session, output, "sew")
    GeometryStore.of(session).close()


def test_orient_and_solidify(tmp_path: Path) -> None:
    session, ids = opened(tmp_path)
    turned = create_and_wait(session, "orient", [ids["box"]], {})
    check_output(session, turned, "orient")
    solid = create_and_wait(session, "solidify", [ids["box"]], {})
    check_output(session, solid, "solidify")
    GeometryStore.of(session).close()


def test_unify_and_slivers_and_defeature(tmp_path: Path) -> None:
    session, ids = opened(tmp_path)
    unified = create_and_wait(session, "unify", [ids["box"]], {})
    check_output(session, unified, "unify")
    dropped = create_and_wait(session, "slivers", [ids["box"]], {})
    check_output(session, dropped, "slivers")
    # Remove faces needs faces: one of the box's own, named the way the UI names a pick.
    went = create_and_wait(
        session, "defeature", [ids["box"]], {"faces": [{"part": ids["box"], "faces": [0]}]}
    )
    check_output(session, went, "defeature")
    GeometryStore.of(session).close()


def test_a_derived_part_can_be_checked_exported_and_opened(tmp_path: Path) -> None:
    """What a user does with an operation's output: check it, export it, reopen the project."""
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    info = create_and_wait(session, "primitive", [], {"shape": "box", "size": [0.01, 0.01, 0.01]})
    output = check_output(session, info, "primitive")
    checked = session.execute("geometry.check", part=output, wait=True)
    assert checked["brep"]["checked"] is True and checked["brep"]["valid"] is True
    stepped = session.execute("geometry.export_step", parts=[output], path="out.step")
    assert stepped["solids"] == 1 and stepped["names"]
    stl = session.execute("geometry.export_stl", path="out.stl", parts=[output])
    assert stl["files"], "no STL written"
    session.execute("project.save")
    GeometryStore.of(session).close()


def test_a_mesh_only_part_refuses_the_operations(tmp_path: Path) -> None:
    """The refusal a user meets after importing an STL: every B-rep operation says why.

    The refusal is `MeshOnly` (code `geometry.mesh_only`), a subclass of `OperationInvalid`: an STL
    part has no B-rep, and the message names the two ways out.
    """
    from stokes.geometry.errors import MeshOnly

    session, ids = opened(tmp_path)
    session.execute("parameter.set", node=ids["box"], name="family", value="mesh")
    with pytest.raises(MeshOnly) as caught:
        session.execute("operation.create", type="enclosure", inputs=[ids["box"]])
    assert caught.value.code == "geometry.mesh_only"
    assert "mesh-only" in caught.value.message
    GeometryStore.of(session).close()
