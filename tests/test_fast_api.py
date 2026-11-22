"""``fast.*`` and the Fast routes (ADR-0047), through ``Session.execute`` on a real project: an
implicit box with the default internal set-up (water, an inlet, an outlet, walls).

The reading of the case runs without a GPU; starting the preview, the routes and a live edit are
``gpu`` tests.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

import stokes.geometry.model  # noqa: F401 - the operation node types
from stokes.api import Session
from stokes.errors import StokesError
from stokes.geometry.store import GeometryStore
from tests.test_implicit_operations import (  # noqa: F401 - the module's workers fixture
    implicit,
    new_session,
    reference_workers,
)

TOKEN = "t" * 32
SPEED = 0.05


def _duct(session: Session, speed: float = SPEED) -> str:
    """A 60 x 20 x 20 mm box along x with the internal defaults: inlet -x, outlet +x, walls."""
    from stokes.geometry.retag import faces_of

    info = implicit(
        session, "implicit_primitive", kind="box", size=[0.06, 0.02, 0.02], position=[0, 0, 0]
    )
    part = str(info["outputs"][0])
    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None
    faces, _ = faces_of(mesh)
    low = [i for i, f in faces.items() if f.normal[0] < -0.99]
    high = [i for i, f in faces.items() if f.normal[0] > 0.99]
    session.execute(
        "setup.defaults",
        target=part,
        template="internal",
        inlet=[{"part": part, "faces": low}],
        outlet=[{"part": part, "faces": high}],
        velocity=f"{speed} [m/s]",
    )
    return part


def _boundary(session: Session, kind: str) -> Any:  # Any: a model Node
    return next(
        n
        for n in session.require_project().root.walk()
        if n.type == "boundary" and n.params.get("kind") == kind
    )


def test_the_case_reads_the_fluid_its_ports_and_its_material(tmp_path: Path) -> None:
    from stokes.api.fast_inputs import case_from_session
    from stokes.fast.wgsl import compile_field

    session = new_session(tmp_path)
    _duct(session)
    case, density = case_from_session(session)
    assert {p.name: p.kind for p in case.ports} == {"inlet": "inlet", "outlet": "outlet"}
    inlet = case.inlets[0]
    # Against the outward -x. The direction is the tessellated face's normal, square to the axes
    # to about 1e-7 only (measured: 1.3e-8 m/s across at 0.05 m/s), so the tolerance is a share
    # of the speed; the lattice's own error is four orders above it.
    assert inlet.velocity == pytest.approx((SPEED, 0.0, 0.0), abs=1e-6 * SPEED)
    assert inlet.normal == pytest.approx((-1.0, 0.0, 0.0), abs=1e-3)
    assert case.outlets[0].normal == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)
    assert case.low == pytest.approx((-0.03, -0.01, -0.01))
    assert case.high == pytest.approx((0.03, 0.01, 0.01))
    assert 900.0 < density < 1100.0
    assert 5e-7 < case.nu < 2e-6  # water
    assert compile_field(case.field_text).startswith("fn phi")
    GeometryStore.of(session).close()


def test_a_boundary_kind_fast_mode_does_not_model_is_refused_by_name(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    _duct(session)
    walls = _boundary(session, "wall")
    session.execute("boundary.set", boundary=walls.id, kind="symmetry")
    with pytest.raises(StokesError) as caught:
        session.execute("fast.start")
    assert caught.value.code == "fast.unsupported_boundary"
    assert "symmetry" in caught.value.message
    GeometryStore.of(session).close()


def test_the_commands_answer_when_nothing_runs() -> None:
    session = Session()
    assert session.execute("fast.status") == {"state": "off"}
    assert session.execute("fast.stop") == {"stopped": False}
    session.execute("project.new", name="empty")
    with pytest.raises(StokesError) as caught:
        session.execute("fast.configure", fidelity=3)
    assert caught.value.code == "fast.not_running"
    with pytest.raises(StokesError) as started:
        session.execute("fast.start")
    assert started.value.code == "fast.invalid"  # no fluid region yet
    checked = session.execute("fast.check")
    assert isinstance(checked["available"], bool)


def test_the_plane_routes_answer_no_content_when_nothing_runs() -> None:
    """A client that asks for the plane as the preview stops is not at fault: 204, not an error
    (a 404 there put an error in the browser's console on every Stop)."""
    from fastapi.testclient import TestClient

    from stokes.server.app import create_app

    client = TestClient(create_app(Session(), token=TOKEN, dist_dir=None))
    for route in ("/api/fast/slice?", "/api/fast/field?field=U&"):
        answer = client.get(f"{route}token={TOKEN}")
        assert answer.status_code == 204 and answer.content == b""
    assert client.get(f"/api/fast/field?field=Q&token={TOKEN}").status_code == 400
    assert client.get("/api/fast/slice").status_code == 401


def _wait(
    session: Session, condition: Any, seconds: float = 60.0
) -> dict[str, Any]:  # Any: a predicate; JSON
    deadline = time.monotonic() + seconds
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = session.execute("fast.status")
        assert status["state"] != "failed", status
        if condition(status):
            return status
        time.sleep(0.25)
    raise AssertionError(f"timed out: {status}")


@pytest.mark.gpu
def test_the_preview_runs_serves_its_plane_and_follows_an_edit(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from stokes.server.app import create_app

    session = new_session(tmp_path)
    _duct(session)
    started = session.execute("fast.start", fidelity=1)
    assert started["ports"] == ["inlet", "outlet"]
    status = _wait(session, lambda s: s["frame"]["vertices"] > 0 and s["frame"]["step"] > 5000)
    assert status["grid"]["fidelity"] >= 1
    client = TestClient(create_app(session, token=TOKEN, dist_dir=None))
    assert client.get("/api/fast/slice").status_code == 401  # the token, as every route
    mesh = client.get(f"/api/fast/slice?token={TOKEN}")
    field = client.get(f"/api/fast/field?field=U&token={TOKEN}")
    assert mesh.status_code == 200 and field.status_code == 200
    assert mesh.headers["X-Fast-Key"] and mesh.headers["X-Fast-Key"].count(":") == 3
    assert client.get(f"/api/fast/field?field=Q&token={TOKEN}").status_code == 400
    assert status["density"] == pytest.approx(998.0, rel=0.02) and status["automatic_plane"]
    assert 0 < status["grid"]["fluid_cells"] < status["grid"]["cells"]
    assert abs(status["frame"]["imbalance"]) < 0.05

    # The plane normal to x, a quarter of the way along, then the automatic one again.
    moved = session.execute("fast.configure", axis=0, offset="-15 [mm]", turbulence="smagorinsky")
    assert moved["turbulence"] == "smagorinsky" and moved["zones"] == [
        {"name": "fluid", "kind": "fluid"}
    ]
    assert not moved["thermal"] and status["frame"]["streams"][0]["flow"] > 0.0
    assert not moved["automatic_plane"]
    quarter = _wait(session, lambda s: s["frame"]["axis"] == 0)
    assert quarter["frame"]["plane"] == pytest.approx(0.25 * quarter["grid"]["nx"], abs=2)
    assert session.execute("fast.configure", axis=-1)["automatic_plane"]
    _wait(session, lambda s: s["frame"]["axis"] != 0)

    # Double the inlet speed through the model: the running preview picks it up on its own.
    before = status["frame"]["inlet_speed"]
    session.execute(
        "parameter.set",
        node=_boundary(session, "velocity_inlet").id,
        name="velocity",
        value="0.1 [m/s]",
    )
    after = _wait(session, lambda s: s["frame"]["inlet_speed"] > 1.8 * before)
    assert after["state"] == "running"

    assert session.execute("fast.stop") == {"stopped": True}
    assert session.execute("fast.status") == {"state": "off"}
    assert client.get(f"/api/fast/slice?token={TOKEN}").status_code == 204
    GeometryStore.of(session).close()
