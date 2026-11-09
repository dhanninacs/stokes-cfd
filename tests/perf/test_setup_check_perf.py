"""Timing for ``setup.check`` on the 4,500-part benchmark tree (ADR-0022 decision 5.4).

No budget: the brief asks for the number, and the harness writes it to ``results.jsonl`` the way
the budgeted tests do. The tree is the one ``tests/perf/test_tree_perf.py`` measures, dressed as a
set-up: one region over every body, 50 boundary tags and 20 boundaries, so the range arithmetic
over the members and the walk of the tree both run at size.
"""

from __future__ import annotations

import json
import time

import pytest

from stokes.api import Session
from tests.perf.harness import RESULTS
from tests.test_tree_patches import many_parts

PARTS = 4500
TAGS = 50
BOUNDARIES = 20
NAME = "setup.check_4500"


def benchmark() -> Session:
    """The benchmark project as a set-up ``setup.check`` can walk."""
    session = Session()
    session.project = many_parts(PARTS)
    bodies = [node.id for node in session.require_project().nodes() if node.type == "part"]
    session.execute("material.create", name="water")
    session.execute("region.create", name="fluid", bodies=bodies, material="water")
    for index in range(TAGS):
        session.execute(
            "tag.create",
            name=f"t{index}",
            members=[{"part": bodies[index], "faces": [index % 6]}],
        )
    for index in range(BOUNDARIES):
        session.execute("boundary.assign", target=f"t{index}", kind="wall", region="fluid")
    return session


@pytest.mark.perf
def test_setup_check_of_4500_parts() -> None:
    session = benchmark()
    session.execute("setup.check")  # warm the type lookups and the catalogue

    def run() -> float:
        start = time.perf_counter()
        result = session.execute("setup.check")
        elapsed = (time.perf_counter() - start) * 1000
        assert result["state"] == "incomplete"  # walls only: no inlet, no outlet
        assert result["counts"] == {
            "regions": 1,
            "materials": 1,
            "boundaries": BOUNDARIES,
            "uncovered_faces": PARTS * 6 - BOUNDARIES,
            "legacy": 0,
        }
        assert [one["code"] for one in result["findings"]] == [
            "setup.no_inlet",
            "setup.no_outlet",
            "setup.no_pressure",
            "setup.uncovered_faces",
        ]
        return elapsed

    ms = min(run() for _ in range(3))  # the best of three, as every figure here is taken
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"name": NAME, "ms": round(ms, 3)}) + "\n")
