"""``shape.apply`` (ADR-0040): applying an optimiser's best point to a part made with Create sets
the variables, rebuilds the part and finds its boundary tags again, so they name the faces of the
rebuilt surface and are not stale. Through ``Session.execute`` and the journal's replay."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

import stokes.geometry.model  # noqa: F401 - the operation node types
from stokes.api import Journal, Session, replay
from stokes.errors import StokesError
from stokes.geometry.retag import faces_of
from stokes.geometry.selection import load_members
from stokes.geometry.store import GeometryStore
from tests.test_implicit_operations import (  # noqa: F401 - the module's workers fixture
    implicit,
    mesh_volume,
    new_session,
    reference_workers,
)
from tests.test_implicit_sketch_faces import needs_mesher

H = 0.02  # the box's height, metres


def _tagged_box(session: Session) -> str:
    """A 60 x H x 20 mm box centred on the origin, H a project parameter, with an inlet (-x), an
    outlet (+x) and the walls tagged: the ends stay in their planes when H changes, but grow."""
    session.execute("parameter.define", name="H", dimension="length", value=f"{H * 1000} [mm]")
    info = implicit(
        session, "implicit_primitive", kind="box", size=[0.06, "H", 0.02], position=[0, 0, 0]
    )
    part = str(info["outputs"][0])
    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None
    faces, _ = faces_of(mesh)
    low = [i for i, f in faces.items() if f.normal[0] < -0.99]
    high = [i for i, f in faces.items() if f.normal[0] > 0.99]
    rest = sorted(set(faces) - set(low) - set(high))
    for name, ids in (("inlet", low), ("outlet", high), ("walls", rest)):
        session.execute(
            "tag.create", name=name, members=[{"part": part, "faces": ids}], kind="boundary"
        )
    return part


def _tag_faces(session: Session, name: str, part: str) -> tuple[list[int], set[Any]]:
    """The tag's face ids on the part and the bases its members were picked on."""
    project = session.require_project()
    tag = next(n for n in project.root.walk() if n.type == "tag" and n.name == name)
    ids: list[int] = []
    bases: set[Any] = set()
    for member in load_members(str(tag.params["members"])):
        if member.part == part:
            ids.extend(int(i) for i in member.ids(None))
            bases.add(member.basis)
    return sorted(ids), bases


def _expected(session: Session, part: str) -> dict[str, list[int]]:
    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None
    faces, _ = faces_of(mesh)
    low = sorted(i for i, f in faces.items() if f.normal[0] < -0.99)
    high = sorted(i for i, f in faces.items() if f.normal[0] > 0.99)
    return {"inlet": low, "outlet": high, "walls": sorted(set(faces) - set(low) - set(high))}


def _assert_current(session: Session, part: str) -> None:
    """Every tag names the rebuilt part's faces, on the part's current version."""
    basis = GeometryStore.of(session).basis(session.require_project().get(part))
    for name, ids in _expected(session, part).items():
        found, bases = _tag_faces(session, name, part)
        assert found == ids, name
        assert bases == {basis}, f"{name} is stale: {bases} against {basis}"


@needs_mesher
def test_apply_rebuilds_the_part_and_finds_its_tags_again(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    part = _tagged_box(session)
    before = mesh_volume(session, part)
    result = session.execute("shape.apply", part=part, values={"H": 0.03}, wait=True)
    assert result["status"] == "done" and result["rebuilt"] == 1
    assert set(result["tags"]) == {"inlet", "outlet", "walls"}
    assert mesh_volume(session, part) / before == pytest.approx(1.5, rel=0.03)
    value = session.require_project().get("/physics/parameters/H").params["value"].value
    assert value == pytest.approx(0.03)
    _assert_current(session, part)
    # The journal records the apply once, and replaying it gives the same tags.
    GeometryStore.of(session).close()
    records = Journal.read(tmp_path / "demo.jsonl")
    names = [r["command"] for r in records]
    assert names.count("shape.apply") == 1 and "tag.rematch" not in names
    assert next(r for r in records if r["command"] == "shape.apply")["params"]["wait"] is True
    again = new_session(tmp_path / "again")
    replay(again, records[1:])
    _assert_current(again, part)
    GeometryStore.of(again).close()


@needs_mesher
def test_apply_in_the_background_reports_when_the_tags_are_found(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    part = _tagged_box(session)
    done = threading.Event()
    heard: list[dict[str, Any]] = []

    def applied(event: str, data: dict[str, Any]) -> None:
        heard.append(data)
        done.set()

    session.events.subscribe("shape.applied", applied)
    reply = session.execute("shape.apply", part=part, values={"H": 0.012})
    assert reply == {"part": part, "started": True, "rebuilding": 1}
    assert done.wait(120), "no shape.applied event"
    assert heard[0]["status"] == "done", heard[0]
    _assert_current(session, part)
    GeometryStore.of(session).close()


def test_apply_refuses_a_part_not_made_with_create_and_empty_values(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    part = _tagged_box(session)
    with pytest.raises(StokesError, match="reference: value"):
        session.execute("shape.apply", part=part, values={}, wait=True)
    with pytest.raises(StokesError, match="not an implicit part"):
        session.execute("shape.apply", part="/physics/parameters/H", values={"H": 0.01})
    GeometryStore.of(session).close()
