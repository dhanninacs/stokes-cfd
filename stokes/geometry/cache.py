"""The mesh cache beside the model: ``<name>.stokes/cache/geometry/`` (ADR-0014, T-101).

Two stores hold the same thing, the transport bytes (version 2) served for a part, so a reopened
project shows its parts without OCP:

``meshes.bin``
    **the pack**, one file for the whole project. A magic, a length, a JSON header and then every
    part's bytes, laid out like the ``adjacency.bin`` archive beside it. Reading a part is one
    ``seek`` and one ``read`` on a handle the project holds open, and that is the point: a cold
    project load is dominated by the *number of file opens*, not by the bytes. Measured on a
    4,500-part project (T-101): one ``open()`` per part costs about 1.2 ms, the same 25.71 MB
    through one handle costs about 0.08 ms a part - a 14x difference that does not move when
    decompression or thread count does.

``<node_id>.mesh``
    **the per-part file**, exactly the transport bytes. Still written, for two reasons: it is what
    a project written by an older build holds (so an existing project opens with no migration),
    and it is what answers when the pack is missing, stale or refuses a part. ``write_cache``
    writes both; ``read_cache`` prefers the pack.

The cache lives in the project directory and is therefore untrusted on read: both stores are
size-limited and every part's bytes are fully validated by :func:`stokes.geometry.transport.unpack`.
A pack that is truncated, carries another version's magic or names an impossible slice reads as
*absent* (:class:`MeshPackError`), never as valid: the per-part file then answers, and if it cannot,
the part is simply not cached.

Concurrency: :class:`MeshPack` holds its handle and its header in a small map of readers, and a
writer replaces the whole file atomically (temporary file, then replace), so a reader either sees
the pack as it was or the pack as it is, never a half-written one. The header is re-read by
:meth:`MeshPack.refresh` when the file's size or modification time changed, which the store asks
for once per restore: that is how a second process' write is noticed. Between restores a part is
read from the handle held.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from stokes.geometry.errors import MeshFormatError
from stokes.geometry.files import SourceStamp
from stokes.geometry.mesh import PartMesh
from stokes.geometry.transport import read_header, unpack
from stokes.log import fields, get_logger
from stokes.model.ids import is_id

log = get_logger(__name__)

MAX_CACHE_BYTES = 2 * 1024**3
SUBDIR = ("cache", "geometry")

PACK_NAME = "meshes.bin"
"""One project's packed mesh cache, beside the per-part files and the other archives."""
PACK_MAGIC = b"STKMESH2"
"""Magic and format version in one: a pack written by another layout is not read as this one."""
PACK_VERSION = 2
"""Layout 2 stamps an entry with what its *bytes* were made from. Layout 1 stamped it with the
source as it stood when the pack was written, so a part packed before its file was re-imported
claimed to be fresh for good; a layout-1 pack is read as absent and written again."""
MAX_PACK_BYTES = 4 * 1024**3
MAX_PACK_HEADER_BYTES = 64 * 1024 * 1024
_HEADER_FIELDS = ("part", "offset", "length", "name", "mtime_ns", "size", "basis")


class MeshPackError(MeshFormatError):
    """A pack that cannot be trusted: wrong magic, bad header, or a slice out of bounds."""

    code = "geometry.mesh_pack"


def cache_file(project_dir: Path, node_id: str) -> Path:
    if not is_id(node_id):
        raise MeshFormatError(f"not a node id: {node_id!r}")
    return project_dir.joinpath(*SUBDIR, f"{node_id}.mesh")


def pack_file(project_dir: Path) -> Path:
    """The project's packed mesh cache."""
    return project_dir.joinpath(*SUBDIR, PACK_NAME)


@dataclass(frozen=True)
class PackEntry:
    """Where one part's bytes are in the pack, and what source they were made from.

    ``name``, ``mtime_ns`` and ``size`` are the source stamp *carried by the bytes themselves* (the
    ``source`` of their transport header), or ``""`` and ``-1`` for bytes that carry none. Not the
    source as it stands when the pack is written: that would vouch for bytes made from an older
    file. :func:`_fresh_against` compares them with the source now, so a packed part is judged
    stale without opening a second file, and the verdict equals the per-part file's.
    """

    offset: int
    length: int
    name: str
    mtime_ns: int
    size: int
    basis: str = ""

    @property
    def stamp(self) -> SourceStamp | None:
        return None if self.mtime_ns < 0 else SourceStamp(self.name, self.size, self.mtime_ns)


class MeshPack:
    """One project's pack: the header in memory, one handle open, slices read on demand.

    The handle is opened lazily and only when a part is actually asked for, so a project that is
    never rendered costs no file handle. Every method is safe to call from any thread: the handle
    and the entry map are guarded, and a read holds the lock only for its own seek+read.
    """

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._path = pack_file(folder)
        self._lock = threading.RLock()
        self._handle: IO[bytes] | None = None
        self._stamp: tuple[int, int] | None = None
        """(size, mtime_ns) of the file the header and handle describe."""
        self._entries: dict[str, PackEntry] = {}
        self._loaded = False
        self._suspended = 0
        """Writers inside :meth:`suspended`: while any is, the pack reads as absent."""

    # ------------------------------------------------------------------ lifecycle
    @contextmanager
    def suspended(self) -> Iterator[None]:
        """The pack reads as absent, and holds no handle, while a writer replaces the file.

        On Windows a rename over a file a reader holds open fails, and a reader that reopened the
        handle between "drop it" and "replace" would make it fail again. Inside this block no
        reader reopens it (a part is answered from its own file instead), so the replace is not
        racing this process. It does not stop another process: the replace still retries.
        """
        with self._lock:
            self._suspended += 1
        self.close()
        try:
            yield
        finally:
            with self._lock:
                self._suspended -= 1
                self._loaded = False

    def close(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
            self._entries = {}
            self._loaded = False
            self._stamp = None
        if handle is not None:
            with suppress(OSError):  # a handle that refuses to close costs only the next open
                handle.close()

    def _changed(self) -> bool:
        """Whether the file on disk is not the one the header and handle describe.

        This is a `stat` per call, so the hot read path does not use it: it is the check that
        :meth:`refresh` makes when a reader wants to notice another process' rewrite.
        """
        try:
            info = self._path.stat()
        except OSError:
            return self._loaded and bool(self._entries)
        return self._stamp != (info.st_size, info.st_mtime_ns)

    def refresh(self) -> None:
        """Re-read the header if the file changed under us (another process wrote it).

        Not called per part: `stat`ing the pack for every part of a batch is itself the kind of
        per-item cost this module exists to remove. The store calls it once when it restores a
        project (:meth:`GeometryStore.restore`), which is where a rewrite by another process
        since the last read is noticed; a writer in this process calls :func:`forget_packs`.
        """
        with self._lock:
            if self._handle is not None and not self._changed():
                return
            self._reopen()

    def _reopen(self) -> IO[bytes] | None:
        """Drop the handle and header and open them again. Caller holds the lock."""
        if self._handle is not None:
            with suppress(OSError):
                self._handle.close()
            self._handle = None
        self._entries = {}
        return self._open()

    def _open(self) -> IO[bytes] | None:
        """The handle, opening it and reading the header once. Caller holds the lock."""
        if self._handle is not None:
            return self._handle
        if self._suspended:
            return None
        try:
            info = self._path.stat()
        except OSError:
            self._loaded = True
            self._stamp = None
            return None
        if info.st_size > MAX_PACK_BYTES:
            log.warning("mesh pack too large; ignored", extra=fields(path=str(self._path)))
            self._loaded = True
            self._stamp = (info.st_size, info.st_mtime_ns)
            return None
        handle: IO[bytes] | None = None
        try:
            handle = self._path.open("rb", buffering=0)
            entries = _read_header(handle, info.st_size)
        except (OSError, MeshPackError) as exc:
            log.warning(
                "mesh pack unreadable; ignored", extra=fields(path=str(self._path), error=str(exc))
            )
            self._loaded = True
            self._stamp = (info.st_size, info.st_mtime_ns)
            if handle is not None:
                with suppress(OSError):
                    handle.close()
            return None
        self._handle = handle
        self._entries = entries
        self._loaded = True
        self._stamp = (info.st_size, info.st_mtime_ns)
        return handle

    # ------------------------------------------------------------------ reading
    def read(self, node_id: str) -> bytes | None:
        """One part's bytes, or None when the pack does not hold them."""
        got = self.read_with_entry(node_id)
        return None if got is None else got[1]

    def read_with_entry(self, node_id: str) -> tuple[PackEntry, bytes] | None:
        """One part's entry and bytes in one lookup: the hot path, one lock and no `stat`."""
        with self._lock:
            handle = self._open()
            if handle is None:
                return None
            entry = self._entries.get(node_id)
            if entry is None:
                return None
            try:
                handle.seek(entry.offset)
                data = handle.read(entry.length)
            except OSError as exc:
                log.warning(
                    "mesh pack read failed; ignored",
                    extra=fields(path=str(self._path), node_id=node_id, error=str(exc)),
                )
                return None
            if len(data) != entry.length:
                return None
            return entry, data

    def entry(self, node_id: str) -> PackEntry | None:
        with self._lock:
            self._open()
            return self._entries.get(node_id)

    def ids(self) -> list[str]:
        with self._lock:
            self._open()
            return sorted(self._entries)

    def __contains__(self, node_id: object) -> bool:
        return isinstance(node_id, str) and self.entry(node_id) is not None


def _read_header(handle: IO[bytes], size: int) -> dict[str, PackEntry]:
    """The pack's entry map from a handle at the start: checked here, never trusted."""
    if size < len(PACK_MAGIC) + 4:
        raise MeshPackError("too short to hold a header")
    head = handle.read(len(PACK_MAGIC) + 4)
    if len(head) < len(PACK_MAGIC) + 4 or head[: len(PACK_MAGIC)] != PACK_MAGIC:
        raise MeshPackError("not a mesh pack")
    (length,) = struct.unpack_from("<I", head, len(PACK_MAGIC))
    if length > MAX_PACK_HEADER_BYTES or len(PACK_MAGIC) + 4 + length > size:
        raise MeshPackError("bad header length")
    raw = handle.read(length)
    if len(raw) != length:
        raise MeshPackError("the header is cut short")
    try:
        header = json.loads(raw)
    except ValueError as exc:
        raise MeshPackError(f"the header is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("version") != PACK_VERSION:
        raise MeshPackError("unknown pack layout")
    entries = header.get("entries")
    if not isinstance(entries, list):
        raise MeshPackError("no entries")
    base = len(PACK_MAGIC) + 4 + length
    limit = _data_limit(header, base, size)
    found: dict[str, PackEntry] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise MeshPackError("a malformed entry")
        part, offset, length, name, mtime, stamp_size, basis = [item.get(k) for k in _HEADER_FIELDS]
        # Every field is checked on its own: a header from the project folder is untrusted, and a
        # `bool` is an `int` in Python, which would let a JSON `true` through as a length.
        for value in (offset, length, mtime, stamp_size):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MeshPackError("a malformed entry")
        assert isinstance(offset, int) and isinstance(length, int)
        assert isinstance(mtime, int) and isinstance(stamp_size, int)
        if (
            not (isinstance(part, str) and is_id(part))
            or not isinstance(name, str)
            or length < 0
            or offset < 0
            # Offsets are relative to the data section, which starts at `base`; an entry that
            # reaches past the end of the file is truncated and must not be read.
            or base + offset + length > limit
        ):
            raise MeshPackError("a malformed entry")
        found[part] = PackEntry(
            base + offset, length, name, mtime, stamp_size, basis if isinstance(basis, str) else ""
        )
    return found


def _data_limit(header: Mapping[str, Any], base: int, file_size: int) -> int:
    """Where the packed data section ends: the file, or the header's own idea of it, whichever is
    smaller. A header that claims more than the file holds is truncated and must not be read."""
    stated = header.get("bytes")
    if not isinstance(stated, int) or isinstance(stated, bool) or stated < 0:
        return file_size
    return min(file_size, base + stated)


class PackReader:
    """The packs of the projects this process has open, one :class:`MeshPack` each.

    A session reads the same project's pack for every part of every request, so the handle is
    cached here rather than opened a part at a time. Keyed by the resolved folder so two paths to
    one project share the handle, and the resolution is itself cached: it is a filesystem call per
    lookup, which is the very cost this module exists to remove.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._packs: dict[str, MeshPack] = {}
        self._keys: dict[str, str] = {}

    def of(self, project_dir: Path) -> MeshPack:
        with self._lock:
            key = self._keys.get(str(project_dir))
            if key is None:
                key = _key(project_dir)
                self._keys[str(project_dir)] = key
            pack = self._packs.get(key)
            if pack is None:
                pack = MeshPack(project_dir)
                self._packs[key] = pack
            return pack

    def forget(self, project_dir: Path) -> None:
        with self._lock:
            key = self._keys.pop(str(project_dir), None) or _key(project_dir)
            pack = self._packs.pop(key, None)
        if pack is not None:
            pack.close()

    def close(self) -> None:
        with self._lock:
            packs, self._packs = list(self._packs.values()), {}
            self._keys = {}
        for pack in packs:
            pack.close()


def _key(project_dir: Path) -> str:
    try:
        return str(project_dir.resolve()).casefold()
    except OSError:  # a path that cannot be resolved is still a usable key
        return str(project_dir).casefold()


_readers = PackReader()


def pack_reader(project_dir: Path) -> MeshPack:
    """The pack of the project at ``project_dir`` (opened on first use, kept for the process)."""
    return _readers.of(project_dir)


def forget_packs(project_dir: Path | None = None) -> None:
    """Drop a project's pack handle (a save to a new folder, a close), or every one (tests)."""
    if project_dir is None:
        _readers.close()
    else:
        _readers.forget(project_dir)


# ---------------------------------------------------------------------- writing


def write_cache(project_dir: Path, node_id: str, data: bytes) -> Path:
    """Write the part's per-part cache file atomically (temporary file, then replace).

    The pack is derived from these files, and a part whose bytes change here is no longer what the
    pack holds. A source-backed part is told stale by the stamp inside its own bytes, but a part
    with no source (an operation's output) has nothing to compare, so the pack is dropped *before*
    the file is written: a crash in between leaves no pack and the per-part files answer, never a
    pack that vouches for old bytes. The next open writes the pack again.
    """
    target = cache_file(project_dir, node_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    _supersede_pack(project_dir, node_id, data)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _supersede_pack(project_dir: Path, node_id: str, data: bytes) -> None:
    """Drop the project's pack when it holds a different copy of this part (see write_cache).

    A part the pack does not hold is not affected: it reads from its own file. Raises ``OSError``
    when the pack cannot be removed, so the write reports itself failed rather than leave a pack
    that vouches for bytes it no longer matches.
    """
    target = pack_file(project_dir)
    if not target.exists():
        return
    held = pack_reader(project_dir).read(node_id)
    if held is None or held == data:
        return
    with pack_reader(project_dir).suspended():
        _unlink_target(target)


def entry_stamp(data: bytes) -> SourceStamp | None:
    """The source stamp the transport ``data`` carries, or None when it names none.

    What the bytes were made from, read from their own header: it is what a per-part file is
    judged against (:func:`is_fresh`), so a packed copy is judged the same way.
    """
    try:
        header, _ = read_header(data)
    except MeshFormatError:
        return None
    return SourceStamp.from_json(header.get("source"))


def build_pack(folder: Path, payloads: Mapping[str, bytes]) -> Path:
    """Lay ``payloads`` out as the project's pack, atomically (temporary file, then replace).

    ``payloads`` maps a part id to the transport bytes to store. Each entry is stamped with the
    source stamp *the bytes carry* (:func:`entry_stamp`), never with the file as it is now, so a
    part whose source changed since the bytes were made stays stale in the pack. The write
    is one temp file and one replace, so a reader sees the whole old pack or the whole new one;
    :func:`read_cache` falls back to the per-part files if the pack is absent.
    """
    head: list[dict[str, object]] = []
    offset = 0
    chunks: list[bytes] = []
    for part in sorted(payloads):
        data = payloads[part]
        stamp = entry_stamp(data)
        head.append(
            {
                "part": part,
                "offset": offset,
                "length": len(data),
                "name": "" if stamp is None else stamp.name,
                "mtime_ns": -1 if stamp is None else stamp.mtime_ns,
                "size": -1 if stamp is None else stamp.size,
                # The B-rep version the bytes carry, so the pack path can reject a stale one without
                # decoding (Claude's defect 1). Empty for bytes that name none.
                "basis": entry_basis(data) or "",
            }
        )
        chunks.append(data)
        offset += len(data)
    # Offsets are relative to the start of the data section, so they do not depend on the header's
    # own length (which is only known once the header is serialised). The reader adds the base.
    header = json.dumps(
        {"version": PACK_VERSION, "bytes": offset, "entries": head}, separators=(",", ":")
    ).encode("utf-8")
    header += b" " * (-len(header) % 8)

    target = pack_file(folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A handle this process holds on the pack being replaced keeps the *new* file open on Windows
    # after `os.replace`, so the next save could not replace it. The reader lets go of it and stays
    # away for the whole write (a part is answered from its own file meanwhile), then reopens the
    # new pack, header and all, on its next read.
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(handle)
    temporary = Path(name)
    with pack_reader(folder).suspended():
        try:
            with temporary.open("wb") as out:
                out.write(PACK_MAGIC)
                out.write(struct.pack("<I", len(header)))
                out.write(header)
                for chunk in chunks:
                    out.write(chunk)
            _replace_target(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    return target


_REPLACE_ATTEMPTS = 6
"""Tries for the final atomic rename; the same guard :mod:`stokes.model.persist` uses."""


def _replace_target(source: Path, target: Path) -> None:
    """``source.replace(target)``, retrying a transient Windows ``PermissionError``.

    A pack is one large file that a reader keeps open on purpose, and on Windows a rename over a
    file something holds open fails with ``PermissionError`` even when the holder is about to
    close it - the same trap :func:`stokes.model.persist._replace_with_retry` documents for the
    project file, with a scanner or the indexer in the reader's place. The write is atomic, so
    retrying the rename keeps that promise rather than failing a save the user asked for.
    """
    import time

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            source.replace(target)
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
        else:
            return


def _unlink_target(target: Path) -> None:
    """``target.unlink()``, retrying a transient Windows ``PermissionError`` like the rename."""
    import time

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            target.unlink(missing_ok=True)
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
        else:
            return


def is_fresh(path: Path, stamp: SourceStamp | None, source: Path | None) -> bool:
    """Whether a cache file with header ``stamp`` still describes ``source``.

    **This is the STAMP question only.** It used to take a ``basis`` it never read (Claude's minor
    item), which read as though the basis were judged here — it is not. The basis is compared by
    `_same_basis` on the decoded part, one place rather than two; a caller that skipped it would
    serve a mesh from another B-rep version on a stamp match alone.
    """
    if source is None or not source.is_file():
        return True
    current = SourceStamp.of(source)
    if stamp is not None:
        return stamp == current
    return path.stat().st_mtime_ns >= current.mtime_ns


def entry_basis(data: bytes) -> str | None:
    """The B-rep version the transport ``data`` was made from, or None when it names none.

    The companion of :func:`entry_stamp`, and it exists for the same reason: the bytes are their own
    account of what they are, so a packed copy is judged without a second file read.
    """
    try:
        header, _ = read_header(data)
    except MeshFormatError:
        return None
    basis = header.get("basis")
    return basis if isinstance(basis, str) and basis else None


def _fresh_against(stamp: SourceStamp | None, source: Path | None) -> bool:
    """Whether bytes made from ``stamp`` still describe ``source`` (:func:`is_fresh`, no file read).

    A part with no source is always fresh (nothing to compare against) and that case makes no file
    call: it is the common one for a derived part, on the hot path of every cold part. Bytes that
    carry no stamp cannot be vouched for against a source that exists now, so they are stale here
    and the per-part path, which also has the file's own time to go by, gets to decide.
    """
    if source is None:
        return True
    try:
        current = SourceStamp.of(source)
    except OSError:  # the source is gone: the cache is the only copy, so it stands
        return True
    return stamp is not None and stamp == current


def read_cache(
    project_dir: Path, node_id: str, source: Path | None, basis: str | None = None
) -> tuple[PartMesh, bytes] | None:
    """The cached mesh and its bytes, or None when absent, stale or malformed.

    The pack answers first (one seek and one read on a handle the project keeps open); the part's
    own ``<node_id>.mesh`` answers when the pack does not hold it, which is what an older project
    is. Both paths return the same bytes, and both are validated by ``unpack``.

    **``basis`` is the part's current B-rep version, and a cache made from another one is stale**
    (Claude's defect 1). The source stamp cannot carry this: a units change leaves the file
    identical
    and changes the geometry, so the stamp comparison alone called the old bytes fresh and the part
    was drawn at millimetres while being measured at inches. Checked on the DECODED part, which is
    one
    place for both the pack and the per-part path rather than one in each.

    ``basis`` None means the caller does not know it (a derived part before its first operation, a
    project that has never stored one): the bytes are then accepted on the stamp alone, as before,
    so
    this change cannot make an existing project miss every cache on open.
    """
    data = _read_packed(project_dir, node_id, source, basis)
    if data is not None:
        part = _decode(data)
        if part is not None and _same_basis(part, basis):
            return part, data
    return _read_per_part(project_dir, node_id, source, basis)


def _same_basis(part: PartMesh, basis: str | None) -> bool:
    """Whether a decoded part's own basis answers a part whose basis is now ``basis``.

    **The bytes carry their own basis** (`PartMesh.basis`, read back by `unpack`), so there is
    nothing to pass in: a ``file_basis`` parameter existed here and no caller ever set it (Claude's
    minor item), one more way to read the function as judging something it does not.

    An **unknown** basis on either side is accepted: a caller that cannot say is not a part that has
    changed, and treating it as changed would make every project saved before this field existed
    re-import its geometry.
    """
    if basis is None:
        return True
    recorded = part.basis
    if recorded is None:
        # **Bytes written before the basis field existed.** Accepted, not rejected (defect 1 of the
        # second review): the field is NEW, so every cache on disk from the previous build carries
        # none — and the part's own record does. Treating that as stale re-imported every part of
        # every saved project on first open and re-ran its operations, and a part whose source file
        # had gone failed with "source file missing and no mesh is cached" while its cache sat on
        # disk. The stamp has already been checked by the caller, so the geometry is the geometry
        # the
        # file describes; the units are the case this branch cannot see, and the store answers that
        # with `basis=None` when they are not the default (see `GeometryStore.basis`).
        return True
    return recorded == basis


def _read_packed(
    project_dir: Path, node_id: str, source: Path | None, basis: str | None = None
) -> bytes | None:
    try:
        pack = pack_reader(project_dir)
        found = pack.read_with_entry(node_id)
        if found is None:
            return None
        entry, data = found
        if not _fresh_against(entry.stamp, source):
            return None
        # The pack entry's own basis, so a units change rejects a packed mesh without decoding it.
        # **An entry with NO basis is accepted**, because the field is new: a pack written by the
        # previous build stores an empty string, and treating that as stale missed every existing
        # project's cache. Only a basis that is recorded and DIFFERENT is stale — the same rule
        # `_same_basis` applies to the per-part file, stated there at length.
        if basis is not None and entry.basis not in ("", basis):
            return None
        return data
    except (OSError, MeshFormatError) as exc:
        log.warning("mesh pack read failed; ignored", extra=fields(node_id=node_id, error=str(exc)))
        return None


def _decode(data: bytes) -> PartMesh | None:
    try:
        return unpack(data)
    except MeshFormatError as exc:
        log.warning("mesh cache unreadable; ignored", extra=fields(error=str(exc)))
        return None


def _read_per_part(
    project_dir: Path, node_id: str, source: Path | None, basis: str | None = None
) -> tuple[PartMesh, bytes] | None:
    """The part's own file, exactly as the cache worked before the pack existed."""
    path = cache_file(project_dir, node_id)
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_CACHE_BYTES:
        log.warning("mesh cache too large; ignored", extra=fields(path=str(path), size=size))
        return None
    try:
        data = path.read_bytes()
        part = unpack(data)
    except (OSError, MeshFormatError) as exc:
        log.warning("mesh cache unreadable; ignored", extra=fields(path=str(path), error=str(exc)))
        return None
    if not is_fresh(path, part.source, source):
        return None
    if not _same_basis(part, basis):
        return None
    return part, data


def cached_ids(folder: Path) -> set[str]:
    """The ids that have a per-part file in ``folder``, from one listing (no file is opened)."""
    directory = folder.joinpath(*SUBDIR)
    try:
        return {p.stem for p in directory.iterdir() if p.suffix == ".mesh" and is_id(p.stem)}
    except OSError:
        return set()


def iter_payloads(folder: Path, ids: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    """The bytes of the per-part files for ``ids`` (used to build a pack); a missing one is skipped.

    Only the ids asked for are opened: a file is an open, and the opens are the cost the pack
    removes, so the caller says which parts it does not already hold.
    """
    for node_id in ids:
        try:
            yield node_id, cache_file(folder, node_id).read_bytes()
        except (OSError, MeshFormatError):
            continue
