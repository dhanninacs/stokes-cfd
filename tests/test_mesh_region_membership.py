"""A tag a region holds is cell membership; a tag a BOUNDARY holds is a patch (Opus item 15).

**These are unit tests, and that is the point.** The region-first fix was covered by an engine test
only, and `tests/engine/` is skipped unless `STOKES_ENGINE_TESTS=1` — so on every ordinary run the
rule that decides whether an inlet exists was untested. The distinction is pure model
reasoning, so it belongs here.

**The rule**: a tag a boundary points at IS a boundary patch, whatever else holds it. Only a tag no
boundary points at is pure cell membership and left out of the side mapping.

**Why it matters**: the first version asked only "does a region hold it?", so a tag held by both was
skipped — and its side fell to the unassigned wall, **so an inlet could vanish with no
error at all**.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.mesh.snapshot import _is_region_membership

PART = "/geometry/parts"


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))
    from tools.e2e_support import duct_project

    made = Session()
    duct_project(made, tmp_path / "duct.stokes")
    return made


def _fluid_region_tag(session: Session) -> str:
    """The tag `region.assign` makes: whole-body members, and no boundary points at it."""
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    part = _the_part(session)
    session.execute("region.assign", target=part, material=material["id"], name="fluid")
    project = session.require_project()
    return str(next(n for n in project.root.walk() if n.type == "tag" and n.name == "fluid").id)


def _the_part(session: Session) -> str:
    project = session.require_project()
    return str(next(n for n in project.root.walk() if n.type == "part").id)


def test_a_tag_only_a_region_holds_is_cell_membership(session: Session) -> None:
    """**The original fix, kept.** A whole-part region tag takes every face, so counting it as a
    patch made it compete with `outlet` for the `xmax` side — the GUI's own order failed with
    `the xmax side holds faces of fluid and outlet`."""
    tag = _fluid_region_tag(session)
    assert [n.type for n, _ in session.require_project().referrers(tag)] == ["region"]
    assert _is_region_membership(session.require_project(), tag) is True


def test_a_tag_a_boundary_also_holds_is_not_cell_membership(session: Session) -> None:
    """**Opus item 15: the boundary wins.**

    A tag held by BOTH was filtered out, so the side it named fell to the unassigned wall and **an
    inlet vanished with no error**. Measured before the fix: pointing a region at an existing
    boundary's tag — which `region.assign` accepts through `tag_node_id`, and which a hand-edited or
    older project file can hold — gave `[('region', 'tag'), ('boundary', 'tag')]` and the tag was
    skipped.

    A tag a boundary points at IS a boundary patch, whatever else holds it. The state is built by
    pointing the region at the inlet's tag, because `region.assign` makes its own tag and
    that is not the state under test.
    """
    project = session.require_project()
    inlet = str(next(n for n in project.root.walk() if n.type == "tag" and n.name == "inlet").id)
    _fluid_region_tag(session)  # a region exists, holding its own tag
    region = next(n for n in project.root.walk() if n.type == "region")

    # **The state a user reaches, or a file can hold**: the region names the inlet's tag too.
    region.params["tag"] = inlet
    project.drain_touched()

    kinds = sorted(n.type for n, param in project.referrers(inlet) if param == "tag")
    assert kinds == ["boundary", "region"], f"the fixture is not the state under test: {kinds}"
    assert _is_region_membership(project, inlet) is False, (
        "a tag a boundary uses was treated as cell membership, so the boundary's side would fall "
        "to the unassigned wall and the patch would vanish with no error"
    )


def test_a_tag_nothing_points_at_is_not_cell_membership(session: Session) -> None:
    """**The other edge.** A tag no node references is not membership either — it is simply unused,
    and calling it membership would be as wrong as calling a boundary one."""
    project = session.require_project()
    tag = session.execute(
        "tag.create", name="orphan", members=[{"part": _the_part(session), "faces": "all"}]
    )
    assert [n.type for n, _ in project.referrers(str(tag["id"]))] == []
    assert _is_region_membership(project, str(tag["id"])) is False


def test_the_unmapped_list_does_not_offer_a_region_tag_as_a_patch(session: Session) -> None:
    """**The completeness half of item 15.** `_tag_names` listed every tag, so a project with a
    region reported `fluid` among the sides the margin left unassigned — a side that cannot exist,
    since the region tag describes cells.

    The reader of `unmapped` is deciding which patches to look at, so a name that can never
    be one is noise. The same predicate the side mapping uses decides here, so the two cannot
    disagree.
    """
    from stokes.mesh.snapshot import _tag_names

    project = session.require_project()
    _fluid_region_tag(session)
    names = _tag_names(project)
    assert "fluid" not in names, f"the region's tag was offered as a patch: {names}"
    # The real boundaries are still there.
    for expected in ("inlet", "outlet", "walls"):
        assert expected in names, names
