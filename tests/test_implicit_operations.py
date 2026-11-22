"""The slice 1 implicit operations through a session (ADR-0035 decisions 7 to 9): primitives,
transforms and sharp booleans make ``implicit`` parts with a display mesh; implicit and B-rep
operations refuse each other's parts; a journal replays to the same parts and meshes.

The worker meshes with the built ``stokes-implicit-mesh`` when there is one, else with the
reference mesher (``tests/implicit/reference_mesher.py``), set through ``STOKES_IMPLICIT_MESHER``
before the workers start; so these tests need OCP (the worker) but not the Rust program.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("OCP")

from stokes.api import Journal, Session, replay
from stokes.geometry import worker
from stokes.geometry.errors import MeshOnly, OperationInvalid
from stokes.geometry.operations import is_built
from stokes.geometry.store import GeometryStore
from stokes.implicit.runner import ENV_OVERRIDE
from tests.operations_support import box_part, create_and_wait

REFERENCE = Path(__file__).parent / "implicit" / "reference_mesher.py"


def _mesher() -> Path:
    """The built ``stokes-implicit-mesh`` when there is one, else the reference mesher."""
    from stokes.implicit.errors import MesherMissing
    from stokes.implicit.runner import find_mesher

    previous = os.environ.pop(ENV_OVERRIDE, None)
    try:
        return find_mesher()
    except MesherMissing:
        return REFERENCE
    finally:
        if previous is not None:
            os.environ[ENV_OVERRIDE] = previous


@pytest.fixture(scope="module", autouse=True)
def reference_workers() -> Iterator[None]:
    """Workers started after the variable is set, so they inherit it."""
    worker.shutdown_pool()
    previous = os.environ.get(ENV_OVERRIDE)
    os.environ[ENV_OVERRIDE] = str(_mesher())
    worker.prewarm(2)
    yield None
    worker.shutdown_pool()
    if previous is None:
        os.environ.pop(ENV_OVERRIDE, None)
    else:
        os.environ[ENV_OVERRIDE] = previous


def new_session(root: Path, name: str = "demo") -> Session:
    session = Session(journal_path=root / f"{name}.jsonl")
    session.execute("project.new", name=name, path=str(root / name))
    return session


def implicit(
    session: Session, op_type: str, inputs: list[str] = (), **params: Any
) -> dict[str, Any]:  # type: ignore[assignment]
    info = create_and_wait(session, op_type, list(inputs), params)
    assert info["status"] == "ok", info["error"]
    return info


def mesh_volume(session: Session, part: str) -> float:
    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None
    total = 0.0
    for solid in mesh.solids:
        p = solid.positions.astype(np.float64)
        t = solid.indices.astype(np.int64)
        total += float(
            np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6
        )
    return total


def test_the_implicit_types_are_built() -> None:
    assert all(
        is_built(t) for t in ("implicit_primitive", "implicit_transform", "implicit_boolean")
    )


def test_a_primitive_makes_an_implicit_part_with_a_display_mesh(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    info = implicit(
        session,
        "implicit_primitive",
        kind="sphere",
        radius=0.01,
        position=[0.1, 0.0, 0.0],
    )
    project = session.require_project()
    part = project.get(info["outputs"][0])
    params = part.params
    assert (params["family"], params["units"], params["faces"]) == ("implicit", "mm", 1)
    # The part keeps the display mesh's box, which is within a cell of the field's.
    assert params["bbox"][0] == pytest.approx(0.09, abs=2e-4)
    assert params["bbox"][3] == pytest.approx(0.11, abs=2e-4)
    assert params["triangles"] > 0
    assert mesh_volume(session, part.id) == pytest.approx(4 / 3 * math.pi * 1e-6, rel=0.05)
    report = session.execute("operation.info", op=info["id"])["report"]
    assert report["family"] == "implicit" and report["metric"] == "exact"
    GeometryStore.of(session).close()


def test_a_transform_and_a_difference_chain(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    block = implicit(
        session, "implicit_primitive", kind="box", size=[0.02, 0.02, 0.02], position=[0.0, 0.0, 0.0]
    )["outputs"][0]
    pin = implicit(
        session,
        "implicit_primitive",
        kind="cylinder",
        radius=0.004,
        length=0.03,
        position=[0.0, 0.0, 0.0],
    )["outputs"][0]
    moved = implicit(
        session, "implicit_transform", [pin], kind="translate", offset=[0.005, 0.0, 0.0]
    )
    cut = implicit(
        session, "implicit_boolean", [block], op="difference", tools=[moved["outputs"][0]]
    )
    out = cut["outputs"][0]
    expected = 8e-6 - math.pi * 0.004**2 * 0.02
    assert mesh_volume(session, out) == pytest.approx(expected, rel=0.08)
    # Changing the pin's radius re-runs the chain: the hole grows.
    before = mesh_volume(session, out)
    pin_op = session.require_project().get(pin).params["derived"]
    session.execute("parameter.set", node=pin_op, name="radius", value="6 [mm]")
    from stokes.geometry.opstore import OperationStore

    OperationStore.of(session).wait()
    assert mesh_volume(session, out) < before
    GeometryStore.of(session).close()


def test_a_difference_that_keeps_its_tools_leaves_them_bodies(tmp_path: Path) -> None:
    """Keep tools: the block is cut around the pin, and the pin stays a body in the simulation
    (the fluid a solid is cut around, ADR-0042); without it the pin is a hidden step."""
    session = new_session(tmp_path)
    project = session.require_project()

    def cut(keep: bool) -> tuple[Any, Any]:
        block = implicit(
            session, "implicit_primitive", kind="box", size=[0.02] * 3, position=[0.0] * 3
        )["outputs"][0]
        pin = implicit(
            session,
            "implicit_primitive",
            kind="cylinder",
            radius=0.004,
            length=0.03,
            position=[0.0] * 3,
        )["outputs"][0]
        implicit(
            session, "implicit_boolean", [block], op="difference", tools=[pin], keep_tools=keep
        )
        return project.get(block).params, project.get(pin).params

    block, pin = cut(True)
    assert (block["visible"], block["simulate"]) == (False, False)
    assert pin.get("visible", True) is True and pin.get("simulate", True) is True
    _block, pin = cut(False)
    assert (pin["visible"], pin["simulate"]) == (False, False)
    GeometryStore.of(session).close()


def test_implicit_and_brep_operations_refuse_each_others_parts(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    ball = implicit(session, "implicit_primitive", kind="sphere", radius=0.01)["outputs"][0]
    block = box_part(session)
    with pytest.raises(MeshOnly, match="implicit part"):
        session.execute(
            "operation.create",
            type="boolean",
            inputs=[block],
            params={"op": "cut", "tools": [ball]},
        )
    with pytest.raises(OperationInvalid, match="not an implicit part"):
        session.execute("operation.create", type="implicit_transform", inputs=[block], params={})
    GeometryStore.of(session).close()


def test_a_split_makes_two_parts_that_add_up_to_the_body(tmp_path: Path) -> None:
    """ADR-0036 decision 5: an implicit operation with two outputs."""
    session = new_session(tmp_path)
    block = implicit(session, "implicit_primitive", kind="box", size=[0.02, 0.02, 0.02])["outputs"][
        0
    ]
    info = implicit(session, "implicit_split", [block], normal="+x", position=[0.005, 0.0, 0.0])
    assert len(info["outputs"]) == 2
    project = session.require_project()
    below, above = (project.get(part) for part in info["outputs"])
    # The halves are named after the body they split (ADR-0036 decision 5, review finding B8).
    assert (below.name, above.name) == ("box_below", "box_above")
    assert below.params["family"] == above.params["family"] == "implicit"
    volumes = [mesh_volume(session, part) for part in info["outputs"]]
    assert volumes[0] == pytest.approx(8e-6 * 0.75, rel=0.06)
    assert volumes[1] == pytest.approx(8e-6 * 0.25, rel=0.1)
    assert sum(volumes) == pytest.approx(8e-6, rel=0.05)
    GeometryStore.of(session).close()


def test_the_journal_replays_to_the_same_parts_and_meshes(tmp_path: Path) -> None:
    first = new_session(tmp_path / "first")
    a = implicit(first, "implicit_primitive", kind="box", size=[0.01, 0.01, 0.01])["outputs"][0]
    b = implicit(
        first,
        "implicit_primitive",
        kind="sphere",
        radius=0.006,
        position=[0.005, 0.0, 0.0],
    )["outputs"][0]
    joined = implicit(first, "implicit_boolean", [a], op="union", tools=[b])["outputs"][0]
    records = Journal.read(tmp_path / "first" / "demo.jsonl")
    fresh = new_session(tmp_path / "second")
    replay(fresh, records[1:])
    project, original = fresh.require_project(), first.require_project()
    assert project.get(joined).params["basis"] == original.get(joined).params["basis"]
    left = GeometryStore.of(first).mesh(joined)
    right = GeometryStore.of(fresh).mesh(joined)
    assert left is not None and right is not None
    assert np.array_equal(left.solids[0].positions, right.solids[0].positions)
    assert np.array_equal(left.solids[0].indices, right.solids[0].indices)
    GeometryStore.of(first).close()
    GeometryStore.of(fresh).close()


def test_implicit_outputs_are_named_for_what_they_are() -> None:
    """Review finding B8: a body is called what it is, or after the body it changes, never an
    enum value such as ``custom``, ``translate`` or ``difference``."""
    from stokes.geometry.model import implicit_output_name as named

    assert named("implicit_primitive", {"kind": "cylinder"}, None, "cylinder") == "cylinder"
    assert named("implicit_sketch", {"profile": "custom"}, None, "custom") == "extrusion"
    revolve = {"profile": "custom", "method": "revolve"}
    assert named("implicit_sketch", revolve, None, "custom") == "revolution"
    assert named("implicit_sketch", {"profile": "circle"}, None, "circle") == "circle"
    assert named("implicit_transform", {"kind": "translate"}, "pin", "translate") == "pin"
    assert named("implicit_boolean", {"op": "difference"}, "plate", "difference") == "plate"
    assert named("implicit_split", {}, "plate", "below") == "plate_below"
    assert named("implicit_transform", {"kind": "rotate"}, None, "rotate") == "rotate"


def test_a_first_coarse_look_comes_before_the_display_mesh(tmp_path: Path) -> None:
    """ADR-0039: an implicit output is shown first at a coarse cell, then at the display cell;
    the final pass's shape is the one the run reports, and a run whose display mesh is cached
    sends no coarse look."""
    session = new_session(tmp_path)
    passes: list[tuple[str, bool]] = []
    session.events.subscribe(
        "geometry.part_ready",
        lambda _e, data: passes.append((str(data.get("pass")), bool(data.get("complete")))),
    )
    info = implicit(session, "implicit_primitive", kind="box", size=[0.02, 0.02, 0.02])
    assert passes == [("coarse", False), ("fine", True)]
    report = session.execute("operation.info", op=info["id"])["report"]
    assert report["family"] == "implicit"
    part = session.require_project().get(info["outputs"][0])
    assert mesh_volume(session, part.id) == pytest.approx(8e-6, rel=0.02)
    # The same value again: the display mesh is in the cache, so it is shown at once.
    passes.clear()
    session.execute("operation.run", op=info["id"], wait=True)
    assert passes == [("fine", True)]
    GeometryStore.of(session).close()


def test_a_box_is_meshed_finely_enough_to_want_a_first_look() -> None:
    """The display cell of a plain box is well over the ratio finer than the coarse one."""
    from stokes.geometry.worker_main import (
        COARSE_DISPLAY_CELLS,
        COARSE_RATIO,
        DISPLAY_CELLS,
        FINEST_DISPLAY_CELLS,
    )
    from stokes.implicit.bodymesh import display_request
    from stokes.implicit.features import box

    block = box((20.0, 20.0, 20.0), (0.0, 0.0, 0.0))
    fine = display_request(block, cells=DISPLAY_CELLS, finest=FINEST_DISPLAY_CELLS)
    coarse = display_request(block, cells=COARSE_DISPLAY_CELLS, finest=COARSE_DISPLAY_CELLS)
    assert coarse.request.cube.cell >= COARSE_RATIO * fine.request.cube.cell
