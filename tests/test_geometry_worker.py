"""The geometry worker process: cancellation, a crashing worker and errors raised inside it.

Each test needs its own process, and each process spends seconds importing OCP, so the module
starts them together before the first test.
"""

from __future__ import annotations

import os
import signal
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from stokes.geometry import worker
from stokes.geometry.errors import (
    GeometryFileError,
    GeometryWorkerError,
    ImportCancelled,
)
from stokes.geometry.worker import ReadInfo, SolidResult
from tests import geometry_fixtures as fx


@pytest.fixture(scope="module")
def two_solids(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    worker.prewarm(3)
    root = tmp_path_factory.mktemp("worker")
    yield fx.write_named(
        [
            ("bracket", fx.box(10.0, 20.0, 30.0), fx.RED),
            ("pin", fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), fx.BLUE),
        ],
        root / "two.step",
    )
    worker.shutdown_pool()


def test_cancel_stops_the_worker_mid_stream(two_solids: Path, tmp_path: Path) -> None:
    process = worker.acquire()
    cancel = threading.Event()
    seen: list[tuple[str, int]] = []

    def on_solid(result: SolidResult) -> None:
        seen.append((result.pass_name, result.index))
        cancel.set()

    with pytest.raises(ImportCancelled) as info:
        process.run(two_solids, workdir=tmp_path, on_solid=on_solid, cancel=cancel)
    assert info.value.code == "geometry.cancelled"
    assert seen == [("coarse", 0)]
    assert not process.alive


def test_a_dying_worker_is_a_clear_error(two_solids: Path, tmp_path: Path) -> None:
    process = worker.acquire()
    pid = process.pid
    assert pid is not None

    def on_read(info: ReadInfo) -> None:
        assert [s.name for s in info.solids] == ["bracket", "pin"]
        os.kill(pid, signal.SIGTERM)  # stands in for an OCCT access violation

    with pytest.raises(GeometryWorkerError) as info:
        process.run(two_solids, workdir=tmp_path, on_read=on_read)
    assert info.value.code == "geometry.worker_failed"
    assert "stopped unexpectedly" in info.value.message
    assert info.value.details["exitcode"] not in (None, 0)
    assert not process.alive


def test_errors_inside_the_worker_keep_their_type(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.step"
    garbage.write_text("ISO-10303-21;\nHEADER;\nthis is not STEP\n", encoding="utf-8")
    with pytest.raises(GeometryFileError) as info:
        worker.run_import(garbage, workdir=tmp_path / "work")
    assert info.value.code == "geometry.unreadable" and "garbage.step" in info.value.message
