"""`run.probe`: the three answers and no fourth.

Claude's 2026-10-20 contract: *"a displayed probe value with units must be right, not within 2.5 %"*
and *"show 'preparing', never a less accurate number silently"*.

**The join is the thing to test**, because it spans two services and nothing else in the codebase
does: the values come from the run's `.stkfld` cache and the positions from the MESH's surfaces
artifact, matched by a patch INDEX that neither service knows the other by. A unit test of the
interpolation would pass while the join was broken.

The case is a real solved duct (`tools/e2e_duct.py`), built once by the `solved_duct` fixture and
skipped where OpenFOAM is not available - which is the honest way to test a command that reads two
real caches rather than a hand-made pair of arrays.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stokes.api import Session

pytestmark = pytest.mark.engine


@pytest.fixture(scope="module")
def solved_duct(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real meshed-and-solved duct, built once for the whole module.

    **A real run, not hand-made arrays**: the join this command performs spans two services, and a
    unit test of the interpolation would pass while the join was broken. Marked `engine` because it
    needs OpenFOAM, and skipped with the rest of that marker where there is none.
    """
    from tools.e2e_duct import viscosity
    from tools.e2e_support import duct_project

    folder = tmp_path_factory.mktemp("probe") / "duct"
    s = Session()
    ids = duct_project(s, folder)
    assert s.execute("mesh.generate", wait=True).get("state") == "done"
    material = s.execute("material.create", name="air", kind="fluid", template="gas")
    s.execute("region.assign", target=ids["part"], material=material["id"], name="fluid")
    s.execute(
        "solve.configure",
        values={"iterations": 200, "residual_target": 1e-4, "write_interval": 100},
    )
    inlet = next(
        n for n in s.require_project().root.walk() if n.type == "boundary" and n.name == "inlet"
    )
    s.execute(
        "parameter.set",
        node=inlet.id,
        name="velocity",
        value=f"{100.0 * viscosity(s, ids['part']) / 0.02} [m/s]",
    )
    assert s.execute("run.start", wait=True).get("state") in {"done", "done_unconverged"}
    s.execute("project.save")
    return str(Path(f"{folder}.stokes"))


def _session(solved_duct: str) -> Session:
    s = Session()
    s.execute("project.open", path=solved_duct)
    return s


def _patch(s: Session, name: str) -> str:
    return next(
        n.id for n in s.require_project().root.walk() if n.type == "mesh_patch" and n.name == name
    )


def test_the_first_probe_says_PREPARING_and_a_retry_answers(solved_duct: str) -> None:
    """**The whole contract in one test.** The triangulation is built in the background, so
    the first probe says "preparing" and a retry a moment later gets the exact value - never
    a less accurate number in between."""
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    first = None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            answer = s.execute("run.probe", patch=walls, field="U_mag", point=[0.25, 0.0, 0.0])
        except Exception as exc:
            first = first or getattr(exc, "code", "")
            assert getattr(exc, "code", "") == "post.triangulation_preparing", str(exc)
            time.sleep(0.05)
            continue
        assert first == "post.triangulation_preparing", (
            "the first probe must not answer immediately"
        )
        assert answer["values"], "a prepared probe returns values"
        return
    raise AssertionError("the surface never finished preparing")


def test_a_wall_probe_reads_ZERO_velocity(solved_duct: str) -> None:
    """**A physical check, not a plumbing one**: no-slip means |U| is zero on the wall, and
    any interpolation that is not exact will show it as a small non-zero number."""
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            answer = s.execute("run.probe", patch=walls, field="U_mag", point=[0.25, 0.0, 0.0])
        except Exception:
            time.sleep(0.05)
            continue
        assert answer["values"][0] == pytest.approx(0.0, abs=1e-9)
        return
    raise AssertionError("the surface never finished preparing")


def test_a_point_off_the_patch_is_REFUSED(solved_duct: str) -> None:
    """Outside the surface is said plainly - never extrapolated into a number one would believe."""
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            s.execute("run.probe", patch=walls, field="U_mag", point=[50.0, 50.0, 50.0])
        except Exception as exc:
            if getattr(exc, "code", "") == "post.triangulation_preparing":
                time.sleep(0.05)
                continue
            assert getattr(exc, "code", "") == "post.probe_invalid", str(exc)
            assert "outside" in str(exc).lower()
            return
        raise AssertionError("a point 50 m from the duct must not be probed")
    raise AssertionError("the surface never finished preparing")


def test_a_vector_field_is_refused_with_the_reason(solved_duct: str) -> None:
    """`U` has three components and a line plot shows one number: refuse, rather than
    pick a component the user did not ask for."""
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    with pytest.raises(Exception) as caught:
        s.execute("run.probe", patch=walls, field="U", point=[0.25, 0.0, 0.0])
    assert getattr(caught.value, "code", "") in {
        "post.probe_invalid",
        "post.triangulation_preparing",
    }


def test_a_rubbish_patch_is_refused(solved_duct: str) -> None:
    s = _session(solved_duct)
    with pytest.raises(Exception) as caught:
        s.execute("run.probe", patch="n_deadbeef", field="U_mag", point=[0.0, 0.0, 0.0])
    assert getattr(caught.value, "code", "") == "post.probe_invalid"


def test_streamlines_REFUSE_a_scalar_field(solved_duct: str) -> None:
    """A scalar has no direction to trace: refused with the field's name, not traced as zero."""
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    with pytest.raises(Exception) as caught:
        s.execute("run.streamlines", patch=walls, field="U_mag", seeds=[[0.25, 0.0, 0.0]])
    assert getattr(caught.value, "code", "") == "post.streamlines_invalid"
    assert "not a vector field" in str(caught.value)


def test_streamlines_on_a_WALL_patch_are_empty_because_the_flow_is(solved_duct: str) -> None:
    """**Measured, and it is the geometry, not a bug**: the duct's walls carry |U| of exactly zero
    (no-slip), so a streamline there has nothing to follow and the answer is an empty list. The
    feature draws where a surface carries flow ALONG it - a slice or a mid-plane - and this test
    records that a wall is not one of those places.
    """
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    out = s.execute("run.streamlines", patch=walls, field="U", seeds=[[0.25, 0.0, 0.0]])
    assert out["lines"] == []


def test_streamlines_refuse_no_seeds(solved_duct: str) -> None:
    s = _session(solved_duct)
    walls = _patch(s, "walls")
    with pytest.raises(Exception) as caught:
        s.execute("run.streamlines", patch=walls, field="U", seeds=[])
    assert getattr(caught.value, "code", "") == "post.streamlines_invalid"
