"""The shape cache and the shape provider (ADR-0021 decision 8.3; T-024): the B-rep of every
part on disk, its sidecar as the commit, the provider's cache hits, stale sidecars and source
reads, derived parts without a cache, the import's writes and the copy on save."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.errors import ValidationError
from stokes.geometry import shapes, worker
from stokes.geometry.errors import GeometryFileError, OperationInvalid
from stokes.geometry.files import SourceStamp
from stokes.geometry.fingerprints import basis_for, basis_of
from stokes.geometry.mesh import PartMesh
from stokes.geometry.operations.base import check_shape
from stokes.geometry.step import face_count
from stokes.geometry.store import GeometryStore
from stokes.model import Project
from tests import geometry_fixtures as fx
from tests.selection_support import STAMP, cube

PART = "n_0000000a"
OTHER = "n_0000000b"


@pytest.fixture(scope="module", autouse=True)
def warm() -> object:
    worker.prewarm(2)  # the import and the save tests
    yield None
    worker.shutdown_pool()


def volume_mm3(shape: object) -> float:
    return float(check_shape(shape)["volume"]) * 1e9


# ---------------------------------------------------------------- the cache
def test_a_shape_round_trips_through_the_cache(tmp_path: Path) -> None:
    brep = shapes.brep_path(tmp_path, PART)
    entry = shapes.write_shape(
        brep, fx.box(10.0, 20.0, 30.0), part=PART, basis="abc123", scale=1e-3
    )
    assert brep.is_file() and shapes.sidecar_path(brep).name == f"{PART}.brep.json"
    read = shapes.read_entry(brep)
    assert read == entry
    assert (read.basis, read.units, read.faces, read.format) == ("abc123", "mm", 6, "bin")
    assert read.bytes == brep.stat().st_size
    shape = shapes.read_shape(brep, read)
    assert face_count(shape) == 6
    assert volume_mm3(shape) == pytest.approx(6000.0)
    assert shapes.fresh(brep, "abc123") and not shapes.fresh(brep, "abc124")


def test_an_entry_is_whole_or_absent(tmp_path: Path) -> None:
    """The sidecar commits the entry: no sidecar, a sidecar that is not one, or a B-rep whose
    bytes are not the ones it committed, is no entry."""
    brep = shapes.brep_path(tmp_path, PART)
    entry = shapes.write_shape(brep, fx.box(1.0, 1.0, 1.0), part=PART, basis="abc", scale=1e-3)
    side = shapes.sidecar_path(brep)
    data = brep.read_bytes()
    brep.write_bytes(data[: len(data) // 2])  # a write cut short
    assert shapes.read_shape(brep, entry) is None
    brep.write_bytes(data[:-1] + bytes([data[-1] ^ 0xFF]))  # same size, other bytes
    assert shapes.read_shape(brep, entry) is None
    brep.write_bytes(data)
    assert shapes.read_shape(brep, entry) is not None
    for text in ("not json", json.dumps({**entry.to_json(), "basis": "XYZ"}), "[]"):
        side.write_text(text, encoding="utf-8")
        assert shapes.read_entry(brep) is None
    side.unlink()
    assert shapes.read_entry(brep) is None
    with pytest.raises(GeometryFileError):
        shapes.brep_path(tmp_path, "../escape")


def test_a_shape_comes_back_in_millimetres(tmp_path: Path) -> None:
    """A BREP part imported in inches is cached in inches; the provider scales it to mm."""
    brep = shapes.brep_path(tmp_path, PART)
    shapes.write_shape(brep, fx.box(1.0, 1.0, 1.0), part=PART, basis="abc", scale=0.0254)
    entry = shapes.read_entry(brep)
    assert entry is not None and entry.units == "in"
    shape = shapes.to_mm(shapes.read_shape(brep, entry), entry.scale)
    assert check_shape(shape)["bbox"][3] == pytest.approx(0.0254, rel=1e-6)  # m: 25.4 mm
    assert volume_mm3(shape) == pytest.approx(25.4**3, rel=1e-9)


# ---------------------------------------------------------------- the provider
def spec(source: Path | None, basis: str, folder: Path, **extra: object) -> dict[str, object]:
    return {
        "part": PART,
        "name": "block",
        "basis": basis,
        "source": str(source) if source else "",
        "solid": 0,
        "units": None,
        "brep": str(shapes.brep_path(folder, PART)),
        "derived": "",
        **extra,
    }


def test_the_provider_uses_a_fresh_cache_and_reads_nothing(tmp_path: Path) -> None:
    source = fx.write_plain(fx.box(10.0, 20.0, 30.0), tmp_path / "box.step")
    basis = basis_for(SourceStamp.of(source), 0, 6)
    shapes.write_shape(
        shapes.brep_path(tmp_path, PART), fx.box(1.0, 2.0, 3.0), part=PART, basis=basis, scale=1e-3
    )
    provider = shapes.ShapeProvider()
    shape = provider.load(spec(source, basis, tmp_path))
    assert (provider.cache_hits, provider.source_reads) == (1, 0)
    assert volume_mm3(shape) == pytest.approx(6.0)  # the cached shape, not the file's


def test_a_stale_sidecar_reads_the_source_and_rewrites_the_cache(tmp_path: Path) -> None:
    source = fx.write_plain(fx.box(10.0, 20.0, 30.0), tmp_path / "box.step")
    basis = basis_for(SourceStamp.of(source), 0, 6)
    brep = shapes.brep_path(tmp_path, PART)
    shapes.write_shape(brep, fx.box(1.0, 2.0, 3.0), part=PART, basis="0dd0dd", scale=1e-3)
    provider = shapes.ShapeProvider()
    shape = provider.load(spec(source, basis, tmp_path))
    assert (provider.cache_hits, provider.source_reads, provider.writes) == (0, 1, 1)
    assert volume_mm3(shape) == pytest.approx(6000.0)
    entry = shapes.read_entry(brep)
    assert entry is not None and entry.basis == basis and entry.faces == 6
    again = shapes.ShapeProvider()
    again.load(spec(source, basis, tmp_path))
    assert (again.cache_hits, again.source_reads) == (1, 0)


def test_the_source_is_read_once_for_all_its_parts(tmp_path: Path) -> None:
    source = fx.write_named(
        [("a", fx.box(1.0, 1.0, 1.0), None), ("b", fx.box(2.0, 2.0, 2.0, at=(5, 0, 0)), None)],
        tmp_path / "two.step",
    )
    provider = shapes.ShapeProvider()
    stamp = SourceStamp.of(source)
    first = provider.load(spec(source, basis_for(stamp, 0, 6), tmp_path))
    other = spec(source, basis_for(stamp, 1, 6), tmp_path, solid=1)
    second = provider.load({**other, "part": OTHER, "brep": str(shapes.brep_path(tmp_path, OTHER))})
    assert provider.source_reads == 1 and provider.writes == 2
    assert volume_mm3(first) == pytest.approx(1.0) and volume_mm3(second) == pytest.approx(8.0)


def test_a_changed_source_is_not_read_as_the_part(tmp_path: Path) -> None:
    source = fx.write_plain(fx.box(10.0, 20.0, 30.0), tmp_path / "box.step")
    old = basis_for(SourceStamp.of(source), 0, 6)
    fx.write_plain(fx.box(11.0, 20.0, 30.0), source)  # the file changed since the import
    with pytest.raises(GeometryFileError, match="changed since block was imported"):
        shapes.ShapeProvider().load(spec(source, old, tmp_path))


def test_a_derived_part_without_its_cache_names_its_operation(tmp_path: Path) -> None:
    with pytest.raises(OperationInvalid, match="run operation n_0000ffff again") as caught:
        shapes.ShapeProvider().load(spec(None, "abc", tmp_path, derived="n_0000ffff"))
    assert caught.value.details == {"part": PART, "op": "n_0000ffff"}
    with pytest.raises(GeometryFileError, match="no source file"):
        shapes.ShapeProvider().load(spec(None, "abc", tmp_path))


# ---------------------------------------------------------------- versions and specs
def test_basis_of_prefers_the_part_node() -> None:
    project = Project.new("demo")
    project.add("/geometry", "group", "parts")
    node = project.add("/geometry/parts", "part", "cube")
    mesh = PartMesh((cube(),), "m", STAMP)
    legacy = basis_of(mesh, 0)
    assert legacy is not None and basis_of(mesh, 0, node) == legacy  # no basis yet: the stamp's
    project.set_param(node.id, "basis", "0123456789ab")
    assert basis_of(mesh, 0, node) == "0123456789ab"
    assert basis_of(PartMesh((cube(),)), 0, node) == "0123456789ab"  # a derived part's mesh
    with pytest.raises(ValidationError):
        project.set_param(node.id, "basis", "not hex")


def test_part_spec_names_what_the_provider_needs(tmp_path: Path) -> None:
    project = Project.new("demo")
    project.add("/geometry", "group", "parts")
    params = {"source": "C:/cad/b.brep", "solid": 2, "units": "in", "basis": "abc"}
    node = project.add("/geometry/parts", "part", "b", params)
    found = shapes.part_spec(node, tmp_path, "abc")
    assert found == {
        "part": node.id,
        "name": "b",
        "basis": "abc",
        "source": "C:/cad/b.brep",
        "solid": 2,
        "units": "in",
        "brep": str(tmp_path / f"{node.id}.brep"),
        "derived": "",
    }
    assert shapes.part_spec(node, None, "abc")["brep"] is None


# ---------------------------------------------------------------- the import and the save
def test_the_import_caches_every_solid_at_its_parts_version(tmp_path: Path) -> None:
    source = fx.write_named(
        [("bracket", fx.box(10.0, 20.0, 30.0), None), ("pin", fx.cylinder(5.0, 40.0), None)],
        tmp_path / "two.step",
    )
    session = Session()
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    store = GeometryStore.of(session)
    session.execute("geometry.import", path=str(source), wait=True)
    folder = store.shape_dir()
    assert folder == tmp_path / "demo.stokes" / "cache" / "geometry"
    project = session.require_project()
    parts = [n for n in project.nodes() if n.type == "part"]
    assert [p.name for p in parts] == ["bracket", "pin"]
    provider = shapes.ShapeProvider()
    for index, part in enumerate(parts):
        basis = part.params["basis"]
        assert basis == basis_for(SourceStamp.of(source), index, part.params["faces"])
        assert basis == basis_of(store.mesh(part.id), index)  # the mesh agrees
        entry = shapes.read_entry(shapes.brep_path(folder, part.id))
        assert entry is not None and entry.basis == basis and entry.faces == part.params["faces"]
        provider.load(shapes.part_spec(part, folder, basis))
    assert (provider.source_reads, provider.cache_hits) == (0, 2)
    store.close()


def test_a_part_imported_into_a_NEW_project_has_its_brep_inside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). This was
    `test_saving_brings_the_breps_from_the_scratch_folder`, which imported into a session scratch
    folder, saved, and asserted the B-reps had been **copied out** into the project.

    **There is no scratch and no copy-out now**, so the whole middle of that test has nothing to
    assert. What it was protecting still does, though: a part's B-rep must be on disk and findable,
    and its recorded basis must match the model's. Both are asserted here, at the project's own
    folder, from the moment of the import rather than after a save.

    **The "does it travel" half is not lost**: `test_project_save_as_portability` asserts the B-rep
    travels to a new folder, which is a stronger statement than the copy-out ever made - and it is
    where that question belongs now that there is no intermediate folder for it to travel through.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    source = fx.write_plain(fx.box(10.0, 20.0, 30.0), tmp_path / "box.step")
    session = Session()
    session.execute("project.new", name="demo")  # a folder from the start, which is the change
    store = GeometryStore.of(session)
    folder = store.shape_dir()
    # **Inside the project**, and not a scratch folder beside it.
    assert folder == Path(session.project_path) / "cache" / "geometry", folder
    assert "stokes-shapes-" not in folder.name

    part = session.execute("geometry.import", path=str(source), wait=True)
    entry = shapes.read_entry(shapes.brep_path(folder, part["id"]))
    assert entry is not None, "the B-rep was not written where the project keeps them"
    assert entry.basis == session.require_project().get(part["id"]).params["basis"]
    store.close()
