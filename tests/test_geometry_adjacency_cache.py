"""The face-adjacency archive (T-005, ADR-0020): the format, and the cached path against the built
path. The store writes one ``adjacency.bin`` per project at the end of the import; the reader uses
an entry whose mesh counts match, and builds adjacency when it is absent, of another version, or for
another mesh."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import adjacency_cache, selection_cache, worker
from stokes.geometry.adjacency_cache import (
    MAGIC,
    archive_file,
    read_archive,
    write_archive,
)
from stokes.geometry.mesh import PartMesh
from stokes.geometry.selection_cache import SelectionCache
from stokes.geometry.store import GeometryStore
from stokes.geometry.topology import Adjacency, adjacency_of, edges_of, facets_of, surface_of
from tests import geometry_fixtures as fx

GROUP = "/geometry/parts/two"


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    worker.prewarm(3)
    yield fx.write_named(
        [
            ("bracket", fx.box(10.0, 20.0, 30.0), fx.RED),
            ("pin", fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), fx.BLUE),
        ],
        tmp_path_factory.mktemp("adj") / "two.step",
    )
    worker.shutdown_pool()


@pytest.fixture(scope="module")
def imported(source: Path, tmp_path_factory: pytest.TempPathFactory) -> Session:
    folder = tmp_path_factory.mktemp("adj-project")
    session = Session(journal_path=folder / "journal.jsonl")
    session.execute("project.new", name="demo", path=str(folder / "demo"))
    session.execute("geometry.import_step", path=str(source), wait=True)
    return session


# ------------------------------------------------------------------ the format


def test_the_archive_round_trips(tmp_path: Path) -> None:
    first = Adjacency(np.array([[0, 1], [1, 2]], dtype=np.int64), np.array([0.5, -1.0]))
    second = Adjacency(np.array([[3, 4]], dtype=np.int64), np.array([0.25]))
    path = write_archive(
        tmp_path / "demo", {"n_00000001": (first, 3, 8), "n_00000002": (second, 5, 12)}
    )
    assert path == archive_file(tmp_path / "demo") and path.read_bytes().startswith(MAGIC)
    found = read_archive(tmp_path / "demo")
    assert set(found) == {"n_00000001", "n_00000002"}
    np.testing.assert_array_equal(found["n_00000001"][0].pairs, first.pairs)
    assert (found["n_00000001"][1], found["n_00000001"][2]) == (3, 8)
    np.testing.assert_allclose(found["n_00000002"][0].cos, second.cos)


def test_a_nan_cosine_round_trips(tmp_path: Path) -> None:
    # `Adjacency.cos` documents NaN as valid when every shared edge borders a triangle without
    # area, so the reader must keep it.
    adjacency = Adjacency(np.array([[0, 1]], dtype=np.int64), np.array([np.nan]))
    folder = tmp_path / "demo"
    write_archive(folder, {"n_00000001": (adjacency, 3, 8)})
    found = read_archive(folder)
    assert np.isnan(found["n_00000001"][0].cos[0])


def test_a_bad_archive_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "demo"
    path = write_archive(
        folder,
        {"n_00000001": (Adjacency(np.array([[0, 1]], dtype=np.int64), np.array([0.5])), 2, 2)},
    )
    good = path.read_bytes()
    assert read_archive(tmp_path / "nothing") == {}  # absent
    path.write_bytes(b"not an adjacency archive")
    assert read_archive(folder) == {}
    path.write_bytes(good[:-1])  # truncated
    assert read_archive(folder) == {}
    path.write_bytes(good.replace(b'"version":1', b'"version":9', 1))
    assert read_archive(folder) == {}
    # A cosine outside [-1, 1] is corrupt; a NaN is not.
    write_archive(
        folder,
        {"n_00000001": (Adjacency(np.array([[0, 1]], dtype=np.int64), np.array([1.5])), 2, 2)},
    )
    assert read_archive(folder) == {}


# ------------------------------------------------------------------ the import writes it


def test_the_import_writes_one_archive_that_matches_a_fresh_build(imported: Session) -> None:
    folder = imported.project_path
    assert folder is not None
    store = GeometryStore.of(imported)
    archive = read_archive(folder)
    for name in ("bracket", "pin"):
        node_id = imported.execute("node.get", node=f"{GROUP}/{name}")["id"]
        mesh = store.mesh(node_id)
        assert mesh is not None
        assert node_id in archive and archive[node_id][1:] == (mesh.faces, mesh.triangles)
        surface = surface_of([mesh])
        fresh = adjacency_of(surface, facets_of(surface), edges_of(surface))
        np.testing.assert_array_equal(archive[node_id][0].pairs, fresh.pairs)
        np.testing.assert_allclose(archive[node_id][0].cos, fresh.cos)


# ------------------------------------------------------------------ the reader


def bracket_id(session: Session) -> str:
    return str(session.execute("node.get", node=f"{GROUP}/bracket")["id"])


def bracket_mesh(session: Session) -> PartMesh:
    mesh = GeometryStore.of(session).mesh(bracket_id(session))
    assert mesh is not None
    return mesh


def built_here(session: Session) -> Adjacency:
    surface = surface_of([bracket_mesh(session)])
    return adjacency_of(surface, facets_of(surface), edges_of(surface))


def from_a_new_cache(session: Session) -> Adjacency:
    """What a selection cache that has derived nothing yet hands out for the bracket."""
    part = bracket_id(session)
    return SelectionCache(session).adjacency({part: bracket_mesh(session)})[part]


def same(found: Adjacency, expected: Adjacency) -> None:
    np.testing.assert_array_equal(found.pairs, expected.pairs)
    np.testing.assert_allclose(found.cos, expected.cos)


def test_the_reader_uses_the_archive_and_skips_the_build(
    imported: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object) -> None:
        raise AssertionError("the adjacency was built although the entry was there")

    expected = built_here(imported)
    monkeypatch.setattr(selection_cache, "edges_of", refuse)
    same(from_a_new_cache(imported), expected)


@pytest.mark.parametrize(
    "damage", ["gone", "another version", "garbage", "another mesh", "ids", "out of range"]
)
def test_an_entry_that_cannot_be_used_falls_back_to_the_build(
    imported: Session, damage: str
) -> None:
    folder = imported.project_path
    assert folder is not None
    path = archive_file(folder)
    good = path.read_bytes()
    entry = read_archive(folder)[bracket_id(imported)]
    mesh = bracket_mesh(imported)
    bad: dict[str, bytes | None] = {
        "gone": None,
        "another version": good.replace(b'"version":1', b'"version":9', 1),
        "garbage": b"not an adjacency archive",
        "another mesh": None,  # written below with the wrong triangle count
        "ids": None,  # written below with a negative face id
    }
    try:
        if damage == "another mesh":
            write_archive(
                folder, {bracket_id(imported): (entry[0], mesh.faces, mesh.triangles + 1)}
            )
        elif damage == "ids":
            beyond = Adjacency(np.array([[-1, 0]], dtype=np.int64), np.array([1.0]))
            write_archive(folder, {bracket_id(imported): (beyond, mesh.faces, mesh.triangles)})
        elif damage == "out of range":
            beyond = Adjacency(np.array([[0, mesh.faces]], dtype=np.int64), np.array([1.0]))
            write_archive(folder, {bracket_id(imported): (beyond, mesh.faces, mesh.triangles)})
        elif bad[damage] is None:
            path.unlink()
        else:
            path.write_bytes(bad[damage])
        same(from_a_new_cache(imported), built_here(imported))
    finally:
        path.write_bytes(good)


def test_the_archive_is_parsed_once(imported: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two grows that each find a new part read and parse the archive once, not once each."""
    folder = imported.project_path
    assert folder is not None
    calls = 0
    read = adjacency_cache.read_archive

    def counted(path: Path) -> dict[str, adjacency_cache.Entry]:
        nonlocal calls
        calls += 1
        return read(path)

    monkeypatch.setattr(adjacency_cache, "read_archive", counted)
    store = GeometryStore.of(imported)
    first, second = (
        bracket_id(imported),
        str(imported.execute("node.get", node=f"{GROUP}/pin")["id"]),
    )
    cache = SelectionCache(imported)
    cache.adjacency({first: bracket_mesh(imported)})
    pin = store.mesh(second)
    assert pin is not None
    cache.adjacency({second: pin})
    assert calls == 1
