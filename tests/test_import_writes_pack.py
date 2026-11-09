"""An import leaves the project's pack, so the first cold open reads one file, not one per part.

Measured on the 4,500-solid pin grid, 2026-11-09: opening a saved project cold took 49.5 s when
the import had written only per-part files (4,500 file opens, then the pack was built), and 1.5 s
once the import wrote the pack itself.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry.cache import SUBDIR, forget_packs, pack_file, pack_reader
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx


def part_ids(session: Session) -> list[str]:
    from stokes.geometry.store import parts_under

    return [str(n.id) for n in parts_under(session.require_project().root)]


def test_an_import_writes_the_pack_and_a_project_opens_from_it_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    source = fx.write_named(
        [
            ("bracket", fx.box(10.0, 20.0, 30.0), fx.RED),
            ("pin", fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), fx.BLUE),
            ("block", fx.moved(fx.box(5.0, 5.0, 5.0), 0.0, 30.0, 0.0), None),
        ],
        tmp_path / "three.step",
    )
    folder = tmp_path / "three.stokes"
    first = Session()
    first.execute("project.new", name="three", path=str(folder))
    first.execute("geometry.import", path=str(source), name="three", wait=True)
    first.execute("project.save")
    ids = part_ids(first)
    assert len(ids) == 3

    # The import itself wrote the pack, holding every part.
    assert pack_file(folder).is_file()
    assert sorted(pack_reader(folder).ids()) == sorted(ids)

    # Without a single per-part file, a fresh session still gets every part, from the pack.
    forget_packs(folder)
    for per_part in folder.joinpath(*SUBDIR).glob("*.mesh"):
        per_part.unlink()
    second = Session()
    ready: set[str] = set()
    done = threading.Event()

    def on_event(name: str, data: dict[str, object]) -> None:
        if name == "geometry.part_ready" and data.get("pass") == "cache":
            ready.add(str(data["node_id"]))
            if len(ready) == len(ids):
                done.set()

    second.events.subscribe("*", on_event)
    store = GeometryStore.of(second)
    second.execute("project.open", path=str(folder))
    store.activate()
    assert done.wait(60), f"only {len(ready)} of {len(ids)} parts loaded from the pack"
    assert ready == set(ids)
    forget_packs(folder)
