"""Reading and writing workspace and preset files safely.

Reads are size- and depth-limited and never raise for bad content: a file that is
not valid JSON, not an object, too large, too deeply nested or outside the folder
it must stay in becomes an empty document plus a finding. Writes are atomic (a
temporary file in the same folder, then a replace) and raise
:class:`~stokes.workspace.errors.WorkspaceIOError` when the disk refuses.

Standard library only; ``tempfile`` is imported when a write happens.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from stokes.log import fields, get_logger
from stokes.workspace.errors import WorkspaceInvalid, WorkspaceIOError
from stokes.workspace.paths import JSON
from stokes.workspace.rules import Finding

log = get_logger(__name__)

MAX_DOCUMENT_BYTES = 256 * 1024
"""Workspace documents are a few kilobytes; the limit also keeps journal records small."""
MAX_DEPTH = 32

Signature = tuple[int, int]
"""(modification time in ns, size): a file is read again only when this changes."""


@dataclass(frozen=True)
class FileState:
    """What a file held when it was read. ``readable`` is False when it exists but was refused."""

    path: Path
    exists: bool
    readable: bool
    document: dict[str, JSON]
    findings: tuple[Finding, ...]
    signature: Signature | None


def signature(path: Path) -> Signature | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def contained(path: Path, root: Path) -> bool:
    """True when ``path`` resolves (following symlinks) inside ``root``."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def read_file(path: Path, scope: str, *, within: Path | None = None) -> FileState:
    """Read one workspace or preset file; a missing file is an empty, readable document."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return FileState(path, False, True, {}, (), None)
    except OSError as exc:
        return _refused(path, scope, f"cannot be inspected ({exc.strerror or exc})", None)
    sig = (info.st_mtime_ns, info.st_size)
    try:
        document = _load(path, info, within)
    except WorkspaceInvalid as exc:
        return _refused(path, scope, exc.message, sig)
    return FileState(path, True, True, document, (), sig)


def _load(path: Path, info: os.stat_result, within: Path | None) -> dict[str, JSON]:
    """The document a file holds; raises WorkspaceInvalid saying why it is refused."""
    if within is not None and not contained(path, within):
        raise WorkspaceInvalid(f"resolves outside {within}")
    if not stat.S_ISREG(info.st_mode):
        raise WorkspaceInvalid("is not a regular file")
    if info.st_size > MAX_DOCUMENT_BYTES:
        raise WorkspaceInvalid(f"is {info.st_size} bytes; the limit is {MAX_DOCUMENT_BYTES}")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        text = data.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkspaceInvalid(f"cannot be read as UTF-8 text ({exc})") from exc
    if len(data) > MAX_DOCUMENT_BYTES:
        raise WorkspaceInvalid(f"is larger than {MAX_DOCUMENT_BYTES} bytes")
    return parse_document(text)


def _refused(path: Path, scope: str, why: str, sig: Signature | None) -> FileState:
    log.warning("workspace file ignored", extra=fields(path=str(path), reason=why))
    finding = Finding(
        "workspace.unreadable", "invalid", (), f"{path} {why}; it is ignored until fixed", scope
    )
    return FileState(path, True, False, {}, (finding,), sig)


def parse_document(text: str) -> dict[str, JSON]:
    """Parse JSON text that must hold a workspace document; raises WorkspaceInvalid."""
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise WorkspaceInvalid(f"a workspace document has at most {MAX_DOCUMENT_BYTES} bytes")
    try:
        value = json.loads(text, parse_constant=_no_constant)
    except json.JSONDecodeError as exc:
        raise WorkspaceInvalid(
            f"is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInvalid(f"is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise WorkspaceInvalid(f"nests deeper than {MAX_DEPTH} levels") from exc
    if not isinstance(value, dict):
        raise WorkspaceInvalid('must hold a JSON object such as {"version": 1}')
    if not depth_ok(value):
        raise WorkspaceInvalid(f"nests deeper than {MAX_DEPTH} levels")
    return value


def _no_constant(name: str) -> JSON:
    raise ValueError(f"{name} is not a JSON value")


def depth_ok(value: JSON, limit: int = MAX_DEPTH) -> bool:
    """True when ``value`` nests at most ``limit`` containers deep (checked without recursion)."""
    stack: list[tuple[JSON, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict | list):
            if depth > limit:
                return False
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return True


def write_file(path: Path, document: Mapping[str, JSON]) -> Signature | None:
    """Write a document atomically, creating the folder; returns the new signature."""
    import tempfile  # lazy: pulls in shutil, bz2 and lzma, which cost ~250 ms at start-up here

    data = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise WorkspaceInvalid(
            f"the document would be {len(data)} bytes; the limit is {MAX_DOCUMENT_BYTES}"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle_fd, tmp_name = tempfile.mkstemp(
            prefix=".workspace-", suffix=".json", dir=path.parent
        )
    except OSError as exc:
        raise WorkspaceIOError(f"cannot write {path}: {exc}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise WorkspaceIOError(f"cannot write {path}: {exc}") from exc
    return signature(path)


def remove_file(path: Path) -> bool:
    """Delete a file; returns whether there was one."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise WorkspaceIOError(f"cannot remove {path}: {exc}") from exc
    return True


def keep_aside(path: Path) -> Path:
    """Move an unreadable file to ``<name>.bak`` (replacing an older one) before overwriting it."""
    target = path.with_name(path.name + ".bak")
    try:
        path.replace(target)
    except OSError as exc:
        raise WorkspaceIOError(f"cannot move {path} aside to {target}: {exc}") from exc
    return target
