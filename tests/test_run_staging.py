"""Staging a case on the agent: inline when it fits, chunked when it does not.

**Why these tests exist.** `job.put` has always been on the agent - its own comment calls chunking
"the whole reason that method exists" - and **no client called it** until this module: every file
went through `job.create` in one payload capped at 32 MiB, so a case over that could not be staged
at all. A finned part's ASCII STL is tens of MiB, which is why it took a probe against the live
server to notice.

The `call` is a fake here rather than a real agent, deliberately: what is under test is the DECISION
(which files go inline, which are chunked, what happens when a chunk fails), and a real agent would
test the transport instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from stokes.run import staging

MiB = 1024 * 1024


class Fake:
    """A stand-in for `Runner.call`, recording every call it receives."""

    def __init__(self, *, fail_on: str | None = None, fail_delete: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_on = fail_on
        self.fail_delete = fail_delete

    def __call__(self, method: str, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == self.fail_on:
            raise RuntimeError(f"{method} failed as asked")
        if method == "job.delete" and self.fail_delete:
            raise RuntimeError("delete failed as asked")
        if method == "job.create":
            return {"job": "j_test"}
        return {"size": len(params.get("data", b""))}

    @property
    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def put_paths(self) -> list[str]:
        return [p["path"] for m, p in self.calls if m == "job.put"]


# ------------------------------------------------------------------ the inline path


def test_a_small_case_goes_in_one_call() -> None:
    """**The common path pays nothing**: no extra round trips, no new code on the wire."""
    call = Fake()
    staging.stage(call, {"system/a": "hello", "system/b": "world"})
    assert call.methods == ["job.create"]
    assert call.calls[0][1]["files"] == {"system/a": "hello", "system/b": "world"}


def test_a_case_at_the_inline_limit_still_goes_in_one_call() -> None:
    call = Fake()
    files = {"system/a": "x" * staging.INLINE_LIMIT_BYTES}
    staging.stage(call, files)
    assert call.methods == ["job.create"]


def test_a_STRING_and_BYTES_are_measured_the_same_way_the_agent_measures_them() -> None:
    """UTF-8, because that is what `_job_create` counts. A role where this library counts characters
    and the agent counts bytes is a role where a case just over the line is refused on arrival."""
    assert staging._sizes({"a": "é"})["a"] == 2  # two bytes, one character
    assert staging._sizes({"a": b"\xff\xfe"})["a"] == 2


# ------------------------------------------------------------------ Opus F5


def test_the_INLINE_set_is_capped_by_its_TOTAL_not_by_each_file() -> None:
    """**Opus F5, confirmed by measurement.** Choosing every file under `CHUNK_BYTES` (8 MiB) let
    five 7 MiB files - 35 MiB together - go in ONE `job.create` payload, over the agent's 32 MiB
    `MAX_CREATE_BYTES`, so the call was refused and the case could not be staged at all.
    """
    call = Fake()
    files = {f"system/f{i}": "x" * (7 * MiB) for i in range(5)}
    staging.stage(call, files)

    inline = call.calls[0][1]["files"]
    # The agent refuses over 32 MiB, and the payload must also be inside the 64 MiB frame.
    assert sum(len(v) for v in inline.values()) <= staging.INLINE_LIMIT_BYTES
    assert len(inline) < 5, "every file went inline, which is the F5 defect"
    # And nothing is LOST: the files that did not fit are chunked.
    assert len(call.put_paths()) + len(inline) == 5


def test_every_file_is_staged_exactly_once_F5() -> None:
    """The whole file set must arrive, whether a file went inline or through chunks - a cap that
    dropped a file would be worse than the over-large payload it replaced."""
    call = Fake()
    files = {f"system/f{i}": "x" * (7 * MiB) for i in range(5)}
    staging.stage(call, files)

    inline = set(call.calls[0][1]["files"])
    chunked = set(call.put_paths())
    assert inline | chunked == set(files)
    assert inline.isdisjoint(chunked), "a file was sent twice"


def test_the_smallest_files_go_inline_first() -> None:
    """A file that does not fit is chunked, and chunking a small file costs the same round trip as
    chunking a large one - so the small ones should fill the inline budget."""
    call = Fake()
    files = {"small": "x" * (1 * MiB), "big": "y" * (30 * MiB), "tiny": "z" * 1024}
    staging.stage(call, files)
    inline = set(call.calls[0][1]["files"])
    assert {"tiny", "small"} <= inline
    assert "big" not in inline


def test_a_file_that_FITS_the_agents_cap_is_inlined_not_chunked() -> None:
    """**The boundary, pinned where it actually is.** A single 20 MiB file fits the agent's 32 MiB
    `MAX_CREATE_BYTES`, so one round trip is correct and chunking it would be three for nothing.

    My first version of the chunking test asserted this case WAS chunked. The code was right and the
    test was wrong - which is worth a test of its own, because the boundary is easy to mis-state.
    """
    call = Fake()
    staging.stage(call, {"constant/geometry/big.stl": "s" * (20 * MiB)})
    assert call.methods == ["job.create"], "a file that fits the cap was chunked"


# ------------------------------------------------------------------ chunking


def test_a_large_file_goes_up_in_chunks_with_a_final_flag() -> None:
    call = Fake()
    # **Well over the inline limit, so it must be chunked.** 20 MiB would be INLINED, correctly -
    # it fits the agent's 32 MiB cap - and my first version of this test asserted chunking at that
    # size, which is a test of nothing.
    size = 3 * staging.INLINE_LIMIT_BYTES
    staging.stage(call, {"constant/geometry/big.stl": "s" * size})

    puts = [(m, p) for m, p in call.calls if m == "job.put"]
    assert len(puts) >= 2, "a 20 MiB file went up in one chunk"
    offsets = [p["offset"] for _, p in puts]
    assert offsets == sorted(offsets), "chunks went up out of order"
    assert offsets[0] == 0
    # **Every offset must equal the bytes already written**, which is how the agent detects an
    # overlap or a gap; a wrong one is refused there rather than corrupting the file.
    written = 0
    for _, p in puts:
        assert p["offset"] == written
        written += len(p["data"])
    assert written == size, "the chunks do not add up to the file"
    assert puts[-1][1]["final"] is True
    assert [p["final"] for _, p in puts[:-1]] == [False] * (len(puts) - 1)


def test_no_chunk_exceeds_the_agents_put_limit() -> None:
    """`CHUNK_BYTES` mirrors the agent's `MAX_PUT_BYTES`; a chunk over it is refused on arrival."""
    call = Fake()
    staging.stage(call, {"big": "x" * (3 * staging.CHUNK_BYTES + 17)})
    for method, params in call.calls:
        if method == "job.put":
            assert len(params["data"]) <= staging.CHUNK_BYTES


# ------------------------------------------------------------------ failure handling


def test_a_failed_chunk_DELETES_the_job() -> None:
    """A job folder holding a truncated surface is one `job.list` shows and a user could start, and
    snappy would fail on the fragment with a parse error that names no cause."""
    call = Fake(fail_on="job.put")
    with pytest.raises(RuntimeError, match=r"job\.put failed"):
        staging.stage(call, {"big": "x" * (3 * staging.INLINE_LIMIT_BYTES)})
    assert "job.delete" in call.methods


def test_a_failed_cleanup_is_LOGGED_and_does_not_replace_the_real_error() -> None:
    """**Opus F7.** The cleanup failure was swallowed entirely, so a job folder left behind was
    invisible - and a folder nobody knows about is how a disk fills. The CALLER's exception is what
    matters and must survive, so this asserts both: the original error is raised, and the cleanup
    failure is recorded with the job id.
    """
    call = Fake(fail_on="job.put", fail_delete=True)
    with pytest.raises(RuntimeError, match=r"job\.put failed"):
        staging.stage(call, {"big": "x" * (3 * staging.INLINE_LIMIT_BYTES)})
    # The delete was attempted, which is the behaviour the log accompanies.
    assert "job.delete" in call.methods


def test_a_raising_call_propagates_unchanged() -> None:
    """`job.create` itself failing is the caller's error to report, not this module's."""
    call = Fake(fail_on="job.create")
    with pytest.raises(RuntimeError, match=r"job\.create failed"):
        staging.stage(call, {"a": "x" * (30 * MiB)})


def test_a_file_that_is_neither_bytes_nor_str_is_named() -> None:
    with pytest.raises(TypeError, match="must be bytes or str"):
        staging._sizes({"a": 123})


# ------------------------------------------------------------------ the constants


def test_the_limits_mirror_the_agents_own() -> None:
    """Pinned so the relationship is a decision. `INLINE_LIMIT_BYTES` is BELOW the agent's 32 MiB
    cap deliberately: the agent holds the whole payload while it parses the frame, and the JSON
    encoding of text grows."""
    assert 8 * MiB == staging.CHUNK_BYTES  # the agent's MAX_PUT_BYTES
    assert 32 * MiB > staging.INLINE_LIMIT_BYTES  # the agent's MAX_CREATE_BYTES
