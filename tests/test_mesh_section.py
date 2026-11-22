"""`mesh.section`: the volume mesh at a section plane, cut by the host off the session thread."""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

from stokes.mesh import section


class _Runner:
    def __init__(self, reply: Any, gate: threading.Event | None = None) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.gate = gate

    def call(self, method: str, **params: Any) -> Any:
        self.calls.append({"method": method, **params})
        if self.gate is not None:
            self.gate.wait(5)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _reply() -> dict[str, Any]:
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype="<f4")
    triangles = np.array([[0, 1, 2], [0, 2, 3]], dtype="<u4")
    edges = np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="<u4")
    return {
        "positions": positions.tobytes(),
        "triangles": triangles.tobytes(),
        "edges": edges.tobytes(),
        "regions": bytes([0, 1]),
        "names": ["hot", "plate"],
        "cells": 1,
    }


def _wait_ready(sections: section.MeshSections, runner: Any) -> dict[str, Any]:
    for _ in range(200):
        found = sections.section(runner, "j_1", "z", 0.002)
        if found["state"] != "cutting":
            return found
        time.sleep(0.01)
    raise AssertionError("the cut never arrived")


def test_the_plane_is_normal_to_its_axis_at_the_offset() -> None:
    assert section.plane_of("y", 0.5) == {"origin": [0.0, 0.5, 0.0], "normal": [0.0, 1.0, 0.0]}


def test_a_first_ask_answers_cutting_and_the_cut_is_held_once_in() -> None:
    gate = threading.Event()
    runner = _Runner(_reply(), gate)
    sections = section.MeshSections()
    assert sections.section(runner, "j_1", "z", 0.002)["state"] == "cutting"
    # A second ask while it cuts starts no second cut.
    assert sections.section(runner, "j_1", "z", 0.002)["state"] == "cutting"
    gate.set()
    found = _wait_ready(sections, runner)
    assert found["state"] == "ready" and found["names"] == ["hot", "plate"]
    assert found["cells"] == 1 and len(runner.calls) == 1
    assert runner.calls[0]["method"] == "mesh.cut"
    assert runner.calls[0]["plane"] == section.plane_of("z", 0.002)


def test_a_host_failure_is_answered_once_as_failed() -> None:
    runner = _Runner(RuntimeError("the job is gone"))
    sections = section.MeshSections()
    sections.section(runner, "j_1", "z", 0.002)
    found = _wait_ready(sections, runner)
    assert found == {"state": "failed", "message": "the job is gone"}


def test_a_malformed_reply_is_refused() -> None:
    bad = {**_reply(), "regions": bytes([0])}
    with pytest.raises(ValueError, match="region count"):
        section._answer(bad, "z", 0.0)
    with pytest.raises(ValueError, match="positions"):
        section._answer({**_reply(), "positions": b"\x00" * 5}, "z", 0.0)
