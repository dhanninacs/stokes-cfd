"""Staging a job's files on the agent: inline what fits, chunk what does not.

## Why this module exists

`job.create` takes every file as one JSON payload, capped at the agent's `MAX_CREATE_BYTES`
(32 MiB) and carried in one protocol frame (64 MiB). The agent has always had `job.put` for
anything larger - *"Bigger inputs go through `job.put`, chunk by chunk, which is the whole reason
that method exists"* - but **no client ever called it**: measured, `grep -rn "job.put" stokes/`
found only the agent's own registration. So a case over 32 MiB could not be staged at all, and an
ASCII STL over 8 MiB was refused by the linter on the way in.

A finned or holed part is where this bites: an ASCII STL is roughly 260 bytes per triangle, so a
50 k-triangle heat sink is ~15 MiB and the flange in the definition of done is ~59 MiB.

## The rule

* **Everything fits and the case is small** -> one `job.create`, unchanged. The common path pays
  nothing: no extra round trips, no new code on the wire.
* **The files total more than `INLINE_LIMIT_BYTES`** -> `job.create` with the small files only, then
  one `job.put` per large file, in `CHUNK_BYTES` pieces, the last marked `final`.

The threshold is deliberately below the agent's own 32 MiB cap rather than equal to it: a case that
merely *approaches* the cap is one JSON payload the agent must hold whole in memory, and chunking it
costs one round trip per 8 MiB. Choosing the smaller payload is the cheaper side of that trade at
every size worth considering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.log import fields, get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

log = get_logger(__name__)

#: Files totalling no more than this go in the `job.create` payload.
#:
#: **Below the agent's 32 MiB `MAX_CREATE_BYTES`, not equal to it.** The agent holds the whole
#: payload in memory while it parses the frame and writes the files; staying well under that leaves
#: room for the JSON encoding of 8 MiB of text (which grows) and keeps a single slow write from
#: being the thing that fails. 24 MiB is the largest case that still fits one frame comfortably.
INLINE_LIMIT_BYTES = 24 * 1024 * 1024

#: The size of one `job.put` chunk. **The agent's `MAX_PUT_BYTES` is 8 MiB**, so this is that value
#: rather than a second opinion about it: a chunk at the limit is one frame, and the next size up
#: would be refused on arrival.
CHUNK_BYTES = 8 * 1024 * 1024


def stage(
    call: Callable[..., Any],
    files: dict[str, Any],
    *,
    timeout: float = 60.0,
    **params: Any,
) -> Any:
    """Create a job, sending large files through `job.put`.

    ``call`` is the runner's own ``call(method, timeout=..., **params)``, passed in rather than the
    runner itself so this stays a plain function both runners can share: a second implementation in
    `SshRunner` is a second place the chunking rule can drift.

    Every parameter other than ``files`` goes to `job.create` unchanged, so a caller does not have
    to know whether its case was chunked.
    """
    sized = _sizes(files)
    total = sum(sized.values())
    if total <= INLINE_LIMIT_BYTES:
        return call("job.create", files=files, timeout=timeout, **params)

    # **The inline set is capped by its TOTAL, not by each file's size** (Opus F5). Picking every
    # file under `CHUNK_BYTES` let many small files sum past the agent's 32 MiB `MAX_CREATE_BYTES`:
    # measured, five 7 MiB files total 35 MiB and were all chosen for one payload, so `job.create`
    # was refused and the case could not be staged at all. Files go inline smallest-first while they
    # fit; the rest are chunked, which is what `job.put` is for.
    inline: dict[str, Any] = {}
    large: list[str] = []
    inline_total = 0
    # Smallest first, so as many whole files as possible travel inline: a file that does not fit is
    # chunked, and chunking a small file costs the same round trip as chunking a large one.
    for name in sorted(files, key=lambda n: sized[n]):
        if sized[name] >= CHUNK_BYTES or inline_total + sized[name] > INLINE_LIMIT_BYTES:
            large.append(name)
            continue
        inline[name] = files[name]
        inline_total += sized[name]
    large.sort()
    log.info(
        "staging a large case in chunks",
        extra=fields(total=total, inline=len(inline), large=len(large)),
    )
    created = call("job.create", files=inline, timeout=timeout, **params)
    job = str(created["job"])

    # **The chunks go up in order, and the last is marked `final`.** The agent appends at `offset`,
    # so an out-of-order or duplicated chunk is caught by the offset it is given rather than
    # silently corrupting the file.
    try:
        for name in large:
            text = files[name]
            payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
            for start in range(0, len(payload), CHUNK_BYTES):
                piece = payload[start : start + CHUNK_BYTES]
                last = start + CHUNK_BYTES >= len(payload)
                call(
                    "job.put",
                    job=job,
                    path=name,
                    data=piece,
                    offset=start,
                    final=last,
                    timeout=timeout,
                )
    except Exception:
        # **The job is deleted, not left half-staged.** A job folder holding a truncated surface is
        # one a later `job.list` shows and a user could start, and snappy would fail on the fragment
        # with a parse error that names no cause. Removing it makes the failure the copy it is.
        try:
            call("job.delete", job=job, timeout=timeout)
        except Exception as cleanup:
            # **Logged, not swallowed** (Opus F7). The original exception is what the caller needs,
            # so this must not replace it - but a job folder left behind is exactly the state that
            # grows until a disk fills, and silently dropping the failure is how nobody finds out
            # which job it was. The id is in the message so it can be removed by hand.
            log.warning(
                "a half-staged job could not be deleted and was left behind",
                extra=fields(job=job, error=str(cleanup)),
            )
        raise
    return created


def _sizes(files: dict[str, Any]) -> dict[str, int]:
    """Each file's size in bytes, as the agent will count it.

    A `str` is measured as UTF-8, exactly as `_job_create` does: a role where this library counts
    characters and the agent counts bytes is a role where a case just over the line is refused on
    arrival and accepted here.
    """
    out: dict[str, int] = {}
    for name, value in files.items():
        if isinstance(value, bytes):
            out[name] = len(value)
        elif isinstance(value, str):
            out[name] = len(value.encode("utf-8"))
        else:
            raise TypeError(f"file {name!r} must be bytes or str, got {type(value).__name__}")
    return out
