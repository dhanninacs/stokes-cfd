"""A body left out of the simulation cannot be what a case is built from (T-221).

**The mesher's rule at the next layer.** `mesh.part_excluded` refuses an excluded domain part
before anything is meshed; the compile asks the same question later, when it picks the part a
case is built from, and it used to return the excluded body without a word - a case the user
did not ask for, from a body they had told Stokes to ignore.

**A test per consumer, as Claude asked**: the mesh half is in `test_mesh_dispatch.py`, and this
is the compile half. The checks half is `setup.excluded_body`, which is in `test_setup_check.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.run.errors import SolveInvalid, SolvePartExcluded
from stokes.run.snapshot import _domain_part


@pytest.fixture
def region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A project with a fluid region holding one body, no mesh needed.

    **Built without the engine on purpose**: what is under test is which part the compile
    CHOOSES, and that is decided from the model alone - so the test runs everywhere rather than
    only where OpenFOAM is installed.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="duct", path=str(tmp_path / "duct"))
    session.execute("node.add", parent="/geometry", type="group", name="parts")
    part = session.execute("node.add", parent="/geometry/parts", type="part", name="housing")
    session.execute("material.create", name="water", kind="fluid")
    session.execute(
        "region.create",
        name="fluid",
        kind="fluid",
        bodies=[part["id"]],
        material="/physics/materials/water",
    )
    return {"session": session, "part": str(part["id"])}


def test_an_excluded_body_is_refused_as_the_domain_part(region: dict) -> None:
    """**The refusal, and its CODE.** Asserting the code rather than the message is what catches the
    `details={"code": …}` trap: the code is a class attribute, so a subclass is the only way to make
    a caller able to branch on "this body is excluded" instead of reading prose.
    """
    session: Session = region["session"]
    session.execute("geometry.simulate", nodes=[region["part"]], simulate=False)

    with pytest.raises(SolvePartExcluded) as caught:
        _domain_part(session.require_project())
    assert caught.value.code == "solve.part_excluded"
    assert "left out of the simulation" in str(caught.value)
    # **It is a `SolveInvalid` too**, so a caller that already handles "the set-up cannot become a
    # case" keeps working - the subclass narrows the answer without breaking the contract.
    assert isinstance(caught.value, SolveInvalid)


def test_the_same_body_is_accepted_once_it_is_back_in(region: dict) -> None:
    """**The other half**: the check is the FLAG, not a one-way latch. A test that only proved the
    refusal would pass against an implementation that refused every region.
    """
    session: Session = region["session"]
    session.execute("geometry.simulate", nodes=[region["part"]], simulate=False)
    session.execute("geometry.simulate", nodes=[region["part"]], simulate=True)

    assert _domain_part(session.require_project()) == region["part"]
