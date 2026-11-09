"""The packed mesh cache (``meshes.bin``, T-101): freshness, invalidation, layout and rebuilds.

These pin the defects the T-101 review found: a pack that vouched for the bytes of a source that
had since changed, a pack that outlived the rewrite of a part with no source, no test at all of
``build_pack`` or the reader, and an open that read and rewrote every part every time.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.geometry import cache as cache_mod
from stokes.geometry.cache import (
    PACK_MAGIC,
    build_pack,
    cache_file,
    cached_ids,
    entry_stamp,
    forget_packs,
    pack_file,
    pack_reader,
    read_cache,
    write_cache,
)
from stokes.geometry.files import SourceStamp
from stokes.geometry.mesh import PartMesh, TriMesh
from stokes.geometry.store import GeometryStore
from stokes.geometry.transport import pack_part

PART = "n_0000d001"
OTHER = "n_0000d002"
THIRD = "n_0000d003"


@pytest.fixture(autouse=True)
def _no_open_packs() -> Any:
    forget_packs()
    yield
    forget_packs()


def _payload(colour: float, stamp: SourceStamp | None = None, basis: str | None = None) -> bytes:
    """One part's transport bytes; ``colour`` tells one version of it from another."""
    solid = TriMesh(
        "m",
        "solid",
        (colour, 0.5, 0.5),
        np.zeros((3, 3), np.float32),
        np.zeros((3, 3), np.float32),
        np.zeros((1, 3), np.uint32),
        None,
        0,
        0,
        "fine",
    )
    return pack_part(PartMesh((solid,), "m", stamp, basis))


def _colour(found: tuple[PartMesh, bytes] | None) -> float:
    assert found is not None
    return round(float(found[0].solids[0].color[0]), 3)


def _source(folder: Path, mtime_ns: int) -> Path:
    path = folder / "plate.step"
    path.write_bytes(b"ISO-10303-21;" + str(mtime_ns).encode())
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _project(tmp_path: Path) -> Path:
    folder = tmp_path / "p.stokes"
    folder.mkdir()
    return folder


# ------------------------------------------------------------------ round trip and layout
def test_a_pack_answers_for_a_part_whose_own_file_is_gone(tmp_path: Path) -> None:
    folder = _project(tmp_path)
    write_cache(folder, PART, _payload(0.1))
    write_cache(folder, OTHER, _payload(0.2))
    build_pack(folder, {PART: _payload(0.1), OTHER: _payload(0.2)})
    cache_file(folder, PART).unlink()
    cache_file(folder, OTHER).unlink()
    assert _colour(read_cache(folder, PART, None)) == 0.1
    assert _colour(read_cache(folder, OTHER, None)) == 0.2
    assert read_cache(folder, THIRD, None) is None


def test_an_entry_is_stamped_with_the_stamp_its_bytes_carry(tmp_path: Path) -> None:
    folder = _project(tmp_path)
    old = SourceStamp("plate.step", 11, 1_000)
    build_pack(folder, {PART: _payload(0.1, old), OTHER: _payload(0.2)})
    assert pack_reader(folder).entry(PART).stamp == old  # type: ignore[union-attr]
    assert pack_reader(folder).entry(OTHER).stamp is None  # type: ignore[union-attr]
    assert entry_stamp(_payload(0.1, old)) == old
    assert entry_stamp(b"not a transport buffer") is None


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda b: b[: len(b) // 2], id="truncated"),
        pytest.param(lambda b: b"STKMESH1" + b[len(PACK_MAGIC) :], id="layout-1-magic"),
        pytest.param(lambda b: b"junk" * 40, id="garbage"),
        pytest.param(lambda b: b"", id="empty"),
    ],
)
def test_a_pack_that_cannot_be_trusted_reads_as_absent(tmp_path: Path, damage: Any) -> None:
    folder = _project(tmp_path)
    write_cache(folder, PART, _payload(0.3))
    build_pack(folder, {PART: _payload(0.3)})
    forget_packs(folder)
    target = pack_file(folder)
    target.write_bytes(damage(target.read_bytes()))
    # The per-part file answers: the damaged pack must neither raise nor serve anything.
    assert _colour(read_cache(folder, PART, None)) == 0.3
    cache_file(folder, PART).unlink()
    assert read_cache(folder, PART, None) is None


# ------------------------------------------------------------------ the stale-source defect
def test_a_pack_does_not_vouch_for_bytes_made_from_an_older_source(tmp_path: Path) -> None:
    """The defect: stamped with the source as it is *now*, old bytes read as fresh for good."""
    folder = _project(tmp_path)
    source = _source(folder, 1_000_000_000)
    old = _payload(0.1, SourceStamp.of(source))
    write_cache(folder, PART, old)
    source = _source(folder, 2_000_000_000)  # the file changes after the bytes were made
    build_pack(folder, {PART: old})  # a pack written now, from the old per-part file
    assert read_cache(folder, PART, source) is None


def test_a_reimport_replaces_what_a_stale_pack_held(tmp_path: Path) -> None:
    folder = _project(tmp_path)
    source = _source(folder, 1_000_000_000)
    write_cache(folder, PART, _payload(0.1, SourceStamp.of(source)))
    build_pack(folder, {PART: _payload(0.1, SourceStamp.of(source))})
    assert _colour(read_cache(folder, PART, source)) == 0.1

    source = _source(folder, 2_000_000_000)
    assert read_cache(folder, PART, source) is None  # stale: the part must be imported again
    write_cache(folder, PART, _payload(0.9, SourceStamp.of(source)))  # the re-import's write
    assert _colour(read_cache(folder, PART, source)) == 0.9
    forget_packs(folder)  # as after a reopen: nothing carried over in memory
    assert _colour(read_cache(folder, PART, source)) == 0.9


def test_the_pack_and_the_part_file_agree_on_freshness(tmp_path: Path) -> None:
    """One verdict from both stores, including a source that is gone (the cache stands)."""
    folder = _project(tmp_path)
    source = _source(folder, 1_000_000_000)
    stamped = _payload(0.4, SourceStamp.of(source))
    write_cache(folder, PART, stamped)
    build_pack(folder, {PART: stamped})
    assert read_cache(folder, PART, source) is not None
    renamed = folder / "other.step"
    renamed.write_bytes(source.read_bytes())
    os.utime(renamed, ns=(1_000_000_000, 1_000_000_000))
    assert read_cache(folder, PART, renamed) is None  # same time and size, another name: stale
    source.unlink()
    assert read_cache(folder, PART, source) is not None  # gone: the cache is the only copy


# ------------------------------------------------------------------ the part with no source
def test_rewriting_a_part_with_no_source_drops_the_pack_that_held_the_old_bytes(
    tmp_path: Path,
) -> None:
    """An operation's output has no source to compare, so the pack cannot tell it changed."""
    folder = _project(tmp_path)
    write_cache(folder, PART, _payload(0.1))
    write_cache(folder, OTHER, _payload(0.2))
    build_pack(folder, {PART: _payload(0.1), OTHER: _payload(0.2)})
    assert pack_file(folder).exists()

    write_cache(folder, PART, _payload(0.1))  # same bytes: the pack still describes it
    assert pack_file(folder).exists()
    write_cache(folder, THIRD, _payload(0.3))  # a part the pack does not hold: unaffected
    assert pack_file(folder).exists()

    write_cache(folder, PART, _payload(0.8))  # the operation ran again
    assert not pack_file(folder).exists()
    assert _colour(read_cache(folder, PART, None)) == 0.8
    assert _colour(read_cache(folder, OTHER, None)) == 0.2  # the rest still answers


def test_a_write_that_cannot_drop_the_pack_fails_instead_of_leaving_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _project(tmp_path)
    write_cache(folder, PART, _payload(0.1))
    build_pack(folder, {PART: _payload(0.1)})

    def refuse(target: Path) -> None:
        raise PermissionError("held by a scanner")

    monkeypatch.setattr(cache_mod, "_unlink_target", refuse)
    with pytest.raises(PermissionError):
        write_cache(folder, PART, _payload(0.8))
    assert cache_file(folder, PART).read_bytes() == _payload(0.1)  # nothing half-changed


# ------------------------------------------------------------------ the reader
def test_a_pack_reads_as_absent_while_a_writer_replaces_it(tmp_path: Path) -> None:
    folder = _project(tmp_path)
    build_pack(folder, {PART: _payload(0.1)})
    pack = pack_reader(folder)
    assert pack.read(PART) is not None
    with pack.suspended():
        assert pack.read(PART) is None  # nobody reopens the handle the rename needs gone
        with pack.suspended():
            assert pack.read(PART) is None
        assert pack.read(PART) is None
    assert pack.read(PART) is not None


def test_a_writer_replaces_a_pack_a_reader_is_holding(tmp_path: Path) -> None:
    """Reads in flight on other threads must not make the replace fail (Windows)."""
    folder = _project(tmp_path)
    build_pack(folder, {PART: _payload(0.1)})
    stop = threading.Event()
    failures: list[BaseException] = []

    def hammer() -> None:
        while not stop.is_set():
            try:
                pack_reader(folder).read(PART)
            except BaseException as exc:
                failures.append(exc)
                return

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    try:
        for colour in range(10):
            build_pack(folder, {PART: _payload(colour / 10)})
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
    assert failures == []
    assert pack_reader(folder).read(PART) == _payload(0.9)


def test_refresh_reopens_a_pack_that_changed_on_disk(tmp_path: Path) -> None:
    folder = _project(tmp_path)
    build_pack(folder, {PART: _payload(0.1)})
    pack = pack_reader(folder)
    assert pack.read(PART) is not None
    first = pack._handle
    pack.refresh()
    assert pack._handle is first  # unchanged: no reopen
    info = pack_file(folder).stat()
    os.utime(pack_file(folder), ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
    pack.refresh()
    assert pack._handle is not None and pack._handle is not first  # changed: header read again


# ------------------------------------------------------------------ the store's rebuild
class _Node:
    def __init__(self, node_id: str) -> None:
        self.id = node_id
        self.type = "part"
        self.status = ""
        self.params: dict[str, object] = {}


class _Project:
    def __init__(self, nodes: dict[str, _Node]) -> None:
        self._nodes = nodes

    def find(self, ref: str) -> _Node | None:
        return self._nodes.get(ref)


class _Session:
    def __init__(self, project: _Project, path: Path) -> None:
        self.project = project
        self.project_path = path


class _Entry:
    def __init__(self, packed: bytes) -> None:
        self.complete = True
        self.packed = packed


def _store(folder: Path, ids: list[str]) -> GeometryStore:
    store = GeometryStore.__new__(GeometryStore)
    store._session = _Session(_Project({i: _Node(i) for i in ids}), folder)  # type: ignore[assignment]
    store._lock = threading.RLock()
    store._closed = False
    store._entries = {}
    store._reading = {}
    store._errors = {}
    store._cached = set()
    return store


def _count_payload_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []
    real = cache_mod.iter_payloads

    def counted(folder: Path, ids: Any) -> Any:
        ids = list(ids)
        asked.extend(ids)
        return real(folder, ids)

    monkeypatch.setattr(cache_mod, "iter_payloads", counted)
    return asked


def test_a_project_that_has_its_pack_is_not_read_or_rewritten_on_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _project(tmp_path)
    ids = [PART, OTHER]
    for index, node_id in enumerate(ids):
        write_cache(folder, node_id, _payload(0.1 * (index + 1)))
    store = _store(folder, ids)
    asked = _count_payload_reads(monkeypatch)

    assert store._pack_project(folder, store._session.project) is True  # type: ignore[arg-type]
    assert sorted(asked) == sorted(ids)  # a project from before the pack: one pass, once
    written = pack_file(folder).stat().st_mtime_ns

    asked.clear()
    store = _store(folder, ids)
    assert store._pack_project(folder, store._session.project) is False  # type: ignore[arg-type]
    assert asked == []
    assert pack_file(folder).stat().st_mtime_ns == written


def test_only_the_parts_the_pack_lacks_are_read_from_their_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _project(tmp_path)
    for node_id, colour in ((PART, 0.1), (OTHER, 0.2)):
        write_cache(folder, node_id, _payload(colour))
    build_pack(folder, {PART: _payload(0.1), OTHER: _payload(0.2)})
    write_cache(folder, THIRD, _payload(0.3))  # a part added after the pack was written
    store = _store(folder, [PART, OTHER, THIRD])
    asked = _count_payload_reads(monkeypatch)

    assert store._pack_project(folder, store._session.project) is True  # type: ignore[arg-type]
    assert asked == [THIRD]  # the two the pack holds were not opened again
    for node_id, colour in ((PART, 0.1), (OTHER, 0.2), (THIRD, 0.3)):
        cache_file(folder, node_id).unlink()
        assert _colour(read_cache(folder, node_id, None)) == colour


def test_a_part_held_in_memory_is_not_read_from_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _project(tmp_path)
    write_cache(folder, PART, _payload(0.1))
    store = _store(folder, [PART])
    store._entries[PART] = _Entry(_payload(0.1))  # type: ignore[assignment]
    asked = _count_payload_reads(monkeypatch)
    assert store._pack_project(folder, store._session.project) is True  # type: ignore[arg-type]
    assert asked == []


def test_the_pack_a_changed_source_leaves_is_rebuilt_with_the_new_bytes(tmp_path: Path) -> None:
    """The whole sequence of the review's reproduction: edit, reopen, reopen again."""
    folder = _project(tmp_path)
    source = _source(folder, 1_000_000_000)
    node = _Node(PART)
    node.params["source"] = str(source)
    store = _store(folder, [PART])
    store._session.project._nodes[PART] = node  # type: ignore[attr-defined]

    write_cache(folder, PART, _payload(0.1, SourceStamp.of(source)))
    assert store._pack_project(folder, store._session.project) is True  # type: ignore[arg-type]
    assert store._load_cache(node) is not None  # type: ignore[arg-type]

    source = _source(folder, 2_000_000_000)  # the owner edits the STEP file
    node.params["source"] = str(source)
    forget_packs(folder)
    store = _store(folder, [PART])
    store._session.project._nodes[PART] = node  # type: ignore[attr-defined]
    assert store._load_cache(node) is None  # open 1: stale, so it is imported again
    write_cache(folder, PART, _payload(0.9, SourceStamp.of(source)))  # ... and cached again
    assert not pack_file(folder).exists()  # the old pack held the old bytes, so it went

    forget_packs(folder)
    store = _store(folder, [PART])
    store._session.project._nodes[PART] = node  # type: ignore[attr-defined]
    assert store._load_cache(node) is not None  # open 2: the new bytes, from the per-part file
    assert store._pack_project(folder, store._session.project) is True  # type: ignore[arg-type]
    forget_packs(folder)
    store = _store(folder, [PART])
    store._session.project._nodes[PART] = node  # type: ignore[attr-defined]
    found = read_cache(folder, PART, source)
    assert _colour(found) == 0.9  # open 3: from the rebuilt pack, and it is the new mesh
    assert cached_ids(folder) == {PART}


# ------------------------------------- a cache written before the basis field existed (Claude's
# defect 1)
def test_bytes_written_before_the_basis_field_still_hit(tmp_path: Path) -> None:
    """**Every existing project's cache missed** (defect 1).

    The basis field is new, so a cache on disk from the previous build carries **no** basis in its
    header. The part, meanwhile, has a stored basis — its own record is not new. Comparing the two
    said "the bytes cannot be vouched for", so every saved project re-imported and re-ran its
    operations on first open, and a part whose source file had gone failed outright with "source
    file
    missing and no mesh is cached" while its cache sat on disk.

    The rule Claude set: **bytes with no basis are accepted when the stamp still matches and the
    part's units are default** — the old behaviour. Only bytes carrying a *different* basis are
    stale.
    """
    folder = _project(tmp_path)
    source = _source(folder, 1_000)
    # The size must be the file's OWN:  writes 13 bytes of prologue plus the digits of the
    # mtime, so declaring 11 made the stamp comparison fail and the test measured nothing. It looked
    # like the basis rule was still rejecting the bytes.
    stamp = SourceStamp("plate.step", source.stat().st_size, 1_000)
    # The cache as the previous build wrote it: a payload with no basis at all.
    old_bytes = _payload(0.1, stamp)
    write_cache(folder, PART, old_bytes)
    build_pack(folder, {PART: old_bytes})

    # The part's own record HAS a basis, because that is not a new field for it.
    part = PartMesh((), "m", stamp, "basis-with-default-units")

    # The stamp matches and the bytes carry no basis, so this is a HIT: nothing changed about the
    # geometry on disk, and the file that made it is still the same file.
    assert read_cache(folder, PART, source, part.basis) is not None
    assert _colour(read_cache(folder, PART, source, part.basis)) == 0.1


def test_bytes_carrying_a_DIFFERENT_basis_are_stale(tmp_path: Path) -> None:
    """The other half, so the fix above cannot be "accept everything".

    A cache written at inches is wrong for a part whose units are now millimetres — that is the
    whole
    point of the field, and the reason defect 1 of the previous review was fixed at all.
    """
    folder = _project(tmp_path)
    source = _source(folder, 1_000)
    stamp = SourceStamp("plate.step", source.stat().st_size, 1_000)
    write_cache(folder, PART, _payload(0.1, stamp, basis="inch"))
    build_pack(folder, {PART: _payload(0.1, stamp, basis="inch")})
    part = PartMesh((), "m", stamp, "mm")
    assert read_cache(folder, PART, source, part.basis) is None
