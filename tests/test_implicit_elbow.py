"""A mixing elbow built from scratch with the implicit tools (ADR-0035), through the same commands
the Create menu and the build ribbon send.

The elbow is the classic two-inlet mixing elbow, as a half model on its symmetry plane z = 0 (all
lengths in mm here, metres in the commands):

- the main inlet, a pipe of radius 50 along x from x = -200 to 0 at y = -150;
- the bend, a circle of radius 50 revolved a quarter turn about z on a radius of 150, from
  (0, -150) to (150, 0): sketched on the yz plane 150 from the axis, revolved 90 degrees (from +y
  towards +x), then rotated -90 degrees about z into place;
- the outlet, a pipe of radius 50 along y from y = 0 to 200 at x = 150;
- the small inlet, a pipe of radius 12.5 along y at x = 137.5 from y = -225 into the bend;
- the union of the four, cut by the half-space z >= 0 (a 1 m block on that side of the plane).

Its volume is checked against the published geometry's (2.5158e-3 m^3, the half model), and its
flat ends come out as faces of their own, so inlets and outlet can be tagged.
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

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.store import GeometryStore
from stokes.implicit.runner import ENV_OVERRIDE
from tests.operations_support import create_and_wait
from tests.test_implicit_operations import REFERENCE, _mesher

ELBOW_VOLUME = 2.515844864e-3
"""m^3: the half model's volume, from the published STEP (OCCT's volume properties)."""


@pytest.fixture(scope="module", autouse=True)
def workers() -> Iterator[None]:
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


def op(
    session: Session, op_type: str, inputs: list[str] | None = None, **params: Any
) -> str:  # Any: JSON
    info = create_and_wait(session, op_type, inputs or [], params)
    assert info["status"] == "ok", info["error"]
    outputs: list[str] = info["outputs"]
    return outputs[0]


def build_elbow(session: Session) -> str:
    """The elbow, step by step as a user builds it; its part id."""
    inlet = op(
        session,
        "implicit_primitive",
        kind="cylinder",
        radius=0.05,
        length=0.2,
        axis="x",
        position=[-0.1, -0.15, 0.0],
    )
    bend = op(
        session,
        "implicit_sketch",
        profile="circle",
        radius=0.05,
        plane="yz",
        method="revolve",
        axis_distance=0.15,
        angle=math.pi / 2,
    )
    bend = op(session, "implicit_transform", [bend], kind="rotate", axis="z", angle=-math.pi / 2)
    outlet = op(
        session,
        "implicit_primitive",
        kind="cylinder",
        radius=0.05,
        length=0.2,
        axis="y",
        position=[0.15, 0.1, 0.0],
    )
    small = op(
        session,
        "implicit_primitive",
        kind="cylinder",
        radius=0.0125,
        length=0.1,
        axis="y",
        position=[0.1375, -0.175, 0.0],
    )
    joined = op(session, "implicit_boolean", [inlet], op="union", tools=[bend, outlet, small])
    half = op(
        session,
        "implicit_primitive",
        kind="half_space",
        normal="-z",
        position=[0.0, 0.0, 0.0],
        size=[1.0, 1.0, 1.0],
    )
    return op(session, "implicit_boolean", [joined], op="intersection", tools=[half])


def test_the_mixing_elbow_is_built_from_scratch(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "elbow.jsonl")
    session.execute("project.new", name="elbow", path=str(tmp_path / "elbow"))
    elbow = build_elbow(session)
    part = session.require_project().get(elbow)
    low = part.params["bbox"][:3]
    high = part.params["bbox"][3:]
    real = _mesher() != REFERENCE
    tolerance = 0.01 if real else 0.06  # the reference mesher is coarse
    assert low == pytest.approx([-0.2, -0.225, 0.0], abs=0.004 if real else 0.01)
    assert high == pytest.approx([0.2, 0.2, 0.05], abs=0.004 if real else 0.01)
    mesh = GeometryStore.of(session).mesh(elbow)
    assert mesh is not None
    volume = 0.0
    for solid in mesh.solids:
        p = solid.positions.astype(np.float64)
        t = solid.indices.astype(np.int64)
        volume += float(
            np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6
        )
    assert volume == pytest.approx(ELBOW_VOLUME, rel=tolerance)
    if real:
        # The three flat ends, the symmetry plane and the curved walls are faces of their own.
        assert part.params["faces"] >= 5
    GeometryStore.of(session).close()
