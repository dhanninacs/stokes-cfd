"""`fetch_artifact`: chunked, verified, and never half there (T-210; T-216 fetches fields with it).

The property worth naming is the negative one: **a fetch that does not verify leaves no file
behind**, neither the `.part` nor a target, and a target that already existed is not touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.run.artifacts import ArtifactError, fetch_artifact
from tests.mesh_surface_support import digest

DATA = bytes(range(256)) * 5  # 1280 bytes, so a chunk of 300 takes five calls


class Served:
    """A runner answering `artifact.get` from memory, with the faults a test asks for."""

    def __init__(self, data: bytes = DATA, **faults: Any) -> None:
        self.data = data
        self.faults = faults
        self.calls: list[dict[str, Any]] = []

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        assert method == "artifact.get"
        self.calls.append(params)
        if self.faults.get("raise_at") == len(self.calls):
            raise ConnectionError("the host went away")
        offset, length = params["offset"], params["length"]
        chunk = self.data[offset : offset + length]
        reply = {
            "size": len(self.data) + self.faults.get("size_shift", 0),
            "sha256": self.faults.get("sha256", digest(self.data)),
            "offset": offset + self.faults.get("offset_shift", 0),
            "data": b"" if self.faults.get("stall") else chunk,
        }
        if "garbage" in self.faults:
            return self.faults["garbage"]
        return reply


def left(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_the_chunks_are_assembled_verified_and_renamed(tmp_path: Path) -> None:
    served = Served()
    target = tmp_path / "cache" / "a.bin"
    out = fetch_artifact(served, "j", "surfaces.stkmsh", target, digest(DATA), len(DATA), chunk=300)
    assert out == target
    assert target.read_bytes() == DATA
    assert left(target.parent) == ["a.bin"], "no .part is left"
    assert [c["offset"] for c in served.calls] == [0, 300, 600, 900, 1200]
    assert all(c["length"] == 300 and c["job"] == "j" for c in served.calls)
    assert served.calls[0]["name"] == "surfaces.stkmsh"


def test_unchecked_expectations_and_an_empty_artifact_are_accepted(tmp_path: Path) -> None:
    fetch_artifact(Served(), "j", "n", tmp_path / "a.bin", None, None)
    assert (tmp_path / "a.bin").read_bytes() == DATA
    fetch_artifact(Served(b""), "j", "n", tmp_path / "empty.bin", digest(b""), 0)
    assert (tmp_path / "empty.bin").read_bytes() == b""


@pytest.mark.parametrize(
    ("faults", "sha", "size"),
    [
        ({}, "0" * 64, len(DATA)),  # the digest is not the one expected
        ({}, None, len(DATA) + 1),  # the size is not the one expected
        ({"sha256": "f" * 64}, None, None),  # the agent's own digest disagrees with the bytes
        ({"offset_shift": 1}, None, None),  # a chunk from the wrong place
        ({"size_shift": 5}, digest(DATA), None),  # the agent claims more than it sends
        ({"stall": True}, None, None),  # no bytes, no progress
        ({"garbage": {"data": "text"}}, None, None),  # not bytes
    ],
)
def test_a_fetch_that_does_not_verify_leaves_no_file(
    tmp_path: Path, faults: dict[str, Any], sha: str | None, size: int | None
) -> None:
    folder = tmp_path / "cache"
    with pytest.raises(ArtifactError) as raised:
        fetch_artifact(Served(**faults), "j", "n", folder / "a.bin", sha, size, chunk=300)
    assert raised.value.code == "run.artifact"
    assert left(folder) == [], "neither the .part nor the target may be left behind"


def test_a_failed_fetch_does_not_touch_the_file_already_there(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    target.write_bytes(b"the old, good file")
    with pytest.raises(ArtifactError):
        fetch_artifact(Served(), "j", "n", target, "0" * 64, len(DATA))
    assert target.read_bytes() == b"the old, good file"
    assert left(tmp_path) == ["a.bin"]


def test_an_error_from_the_runner_removes_the_part_and_is_not_hidden(tmp_path: Path) -> None:
    with pytest.raises(ConnectionError):
        fetch_artifact(Served(raise_at=3), "j", "n", tmp_path / "a.bin", None, None, chunk=300)
    assert left(tmp_path) == []


def test_a_stale_part_from_a_crash_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "a.bin.part").write_bytes(b"left by a crash")
    fetch_artifact(Served(), "j", "n", tmp_path / "a.bin", digest(DATA), len(DATA))
    assert left(tmp_path) == ["a.bin"]


def test_a_size_over_the_limit_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError):
        fetch_artifact(Served(size_shift=3 * 2**30), "j", "n", tmp_path / "a.bin", None, None)
    assert left(tmp_path) == []
