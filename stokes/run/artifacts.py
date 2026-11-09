"""Fetch an agent artifact in chunks, verify it, and put it in place (T-210, shared with T-216).

The agent keeps what a job produced in the job folder and hands it out through ``artifact.get``:
at most 8 MiB per call, each reply carrying the **whole file's** size and sha256. This module is
the client half: it asks for the chunks one after another into ``<target>.part``, checks the
assembled file against what the caller expected and against what the agent reported, and only then
renames it onto ``target``. A reader of ``target`` therefore sees a complete, verified file or no
file, never a part.

**Any mismatch leaves nothing behind.** Every failure after the ``.part`` file exists removes it
before the error leaves this function, so a failed fetch costs no disk and a retry starts clean.
"""

from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path
from typing import Any

from stokes.run.errors import RunError
from stokes.run.runner import Runner

#: The chunk size one ``artifact.get`` call asks for. The agent refuses more (``MAX_CHUNK``).
CHUNK_BYTES = 8 * 1024 * 1024

#: The most an artifact may be, matching the agent's own limit. A reply that claims more is refused
#: before any of it is written, so a hostile or broken host cannot fill the disk.
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024


class ArtifactError(RunError):
    """An artifact did not arrive intact: a size or digest mismatch, or a malformed chunk."""

    code = "run.artifact"


def fetch_artifact(
    runner: Runner,
    job: str,
    name: str,
    target: Path,
    expected_sha256: str | None,
    expected_size: int | None,
    chunk: int = CHUNK_BYTES,
) -> Path:
    """Fetch ``name`` of ``job`` into ``target`` and return ``target``.

    The bytes are written to ``<target>.part`` while they arrive. The file is renamed onto
    ``target`` (``Path.replace``) only when its size and sha256 equal ``expected_size`` and
    ``expected_sha256`` (each skipped when ``None``) **and** the whole-file digest and size the
    agent reported. Raises :class:`ArtifactError` (code ``run.artifact``) on any mismatch; the
    ``.part`` file is removed on every failure, an error from the runner included, and ``target``
    is never touched until the end.
    """
    if chunk < 1:
        raise ArtifactError(f"chunk must be positive, got {chunk}")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    # A stale part from a crash is removed, and the new one is created exclusively: a path that is
    # already something else (a link planted at the name) is refused instead of followed.
    with contextlib.suppress(FileNotFoundError):
        part.unlink()
    try:
        with part.open("xb") as handle:
            _receive(runner, job, name, handle, expected_sha256, expected_size, chunk)
        part.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    return target


def _receive(
    runner: Runner,
    job: str,
    name: str,
    handle: Any,
    expected_sha256: str | None,
    expected_size: int | None,
    chunk: int,
) -> None:
    """Write every chunk to ``handle`` and verify the whole."""
    hasher = hashlib.sha256()
    offset = 0
    size: int | None = None
    reported: str | None = None
    while size is None or offset < size:
        reply = runner.call("artifact.get", job=job, name=name, offset=offset, length=chunk)
        data = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ArtifactError(f"artifact {name!r}: the reply carries no bytes")
        if int(reply.get("offset", -1)) != offset:
            raise ArtifactError(
                f"artifact {name!r}: asked for offset {offset}, got {reply.get('offset')}"
            )
        total = int(reply.get("size", -1))
        if total < 0 or total > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact {name!r}: the reported size {total} is not usable")
        if size is None:
            size = total
            if expected_size is not None and size != expected_size:
                raise ArtifactError(
                    f"artifact {name!r}: the agent reports {size} bytes, expected {expected_size}"
                )
        elif total != size:
            raise ArtifactError(f"artifact {name!r}: the size changed during the fetch")
        digest = reply.get("sha256")
        reported = str(digest) if digest else reported
        if len(data) > chunk or offset + len(data) > size:
            raise ArtifactError(f"artifact {name!r}: a chunk is larger than asked for")
        if not data and offset < size:
            raise ArtifactError(f"artifact {name!r}: no progress at offset {offset} of {size}")
        handle.write(data)
        hasher.update(data)
        offset += len(data)
    if size is None or offset != size:
        raise ArtifactError(f"artifact {name!r}: received {offset} bytes of {size}")
    actual = hasher.hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ArtifactError(f"artifact {name!r}: the sha256 does not match the one expected")
    if reported is not None and actual != reported:
        raise ArtifactError(f"artifact {name!r}: the sha256 does not match the agent's")
