"""Budgets for the tree of a many-part project (ADR-0019 steps 7 and 8, measured at 4,500 parts).

``node.tree`` of the 4,500-part benchmark project used to take 51 ms to build and another 26 ms to
write, all of it in one piece on the server's event loop, and in the running application (an import
loading the CPU) it was seen at 150 to 220 ms. Now it is built in fragments, and what a budget
guards is the longest stretch of work between two of them: that is how late the loop can run
anything else behind a refresh.

Each figure is the best of three runs: the minimum is the stable estimate of the cost that is ours
to control, a busy CPU only adds to the others.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import pytest

from stokes.api import Session
from stokes.api.summaries import tree_dict, tree_stream
from stokes.model.project import SKELETON
from tests.perf.harness import HEADROOM, RESULTS, machine_factor
from tests.test_tree_patches import many_parts

PARTS = 4500


def best_of_three(work: Callable[[], float]) -> float:
    """The smallest of three measurements, in milliseconds."""
    return min(work() for _ in range(3))


def record(name: str, ms: float, target_ms: float) -> float:
    limit = target_ms * machine_factor() * HEADROOM
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"name": name, "ms": round(ms, 3), "limit_ms": round(limit, 3)}) + "\n"
        )
    return limit


@pytest.mark.perf
def test_node_tree_of_4500_parts() -> None:
    session = Session()
    session.project = many_parts(PARTS)
    session.execute("node.tree")  # warm the display-text cache and the type lookups

    def run() -> float:
        start = time.perf_counter()
        tree = session.execute("node.tree")
        elapsed = (time.perf_counter() - start) * 1000
        assert len(tree["children"]) == len(SKELETON)
        return elapsed

    ms = best_of_three(run)
    limit = record("tree.node_tree_4500", ms, target_ms=25.0)
    assert ms <= limit, f"node.tree of {PARTS} parts took {ms:.1f} ms (budget {limit:.0f} ms)"


@pytest.mark.perf
def test_streamed_tree_never_holds_the_loop_for_long() -> None:
    project = many_parts(PARTS)
    list(tree_stream(project, project.root))  # warm

    def run() -> tuple[float, float, int]:
        """(longest stretch between two fragments, total, fragment count), in ms."""
        longest = total = 0.0
        count = 0
        last = time.perf_counter()
        for _ in tree_stream(project, project.root):
            now = time.perf_counter()
            longest = max(longest, now - last)
            total += now - last
            count += 1
            last = time.perf_counter()
        return longest * 1000, total * 1000, count

    runs = [run() for _ in range(3)]
    longest = min(r[0] for r in runs)
    total = min(r[1] for r in runs)
    count = runs[0][2]
    slice_limit = record("tree.stream_longest_fragment_4500", longest, target_ms=6.0)
    total_limit = record("tree.stream_total_4500", total, target_ms=60.0)
    assert count >= PARTS // 64, f"the tree came in {count} fragments"
    assert longest <= slice_limit, (
        f"a fragment of the streamed tree took {longest:.1f} ms (budget {slice_limit:.0f} ms): "
        "the event loop would be held that long behind every refresh"
    )
    assert total <= total_limit, (
        f"the streamed tree took {total:.0f} ms (budget {total_limit:.0f} ms)"
    )


@pytest.mark.perf
def test_announcing_4500_changes() -> None:
    """The geometry store emits a node.changed per part, from its import thread, as parts finish."""
    session = Session()
    session.project = many_parts(PARTS)
    project = session.require_project()
    parts = [n for n in project.nodes() if n.type == "part"]
    seen: list[dict[str, object]] = []
    session.events.subscribe("node.changed", lambda e, d: seen.append(d))

    def run() -> float:
        seen.clear()
        start = time.perf_counter()
        for index, part in enumerate(parts):
            project.set_param(part.id, "triangles", 100 + index)
            session.emit("node.changed", node_id=part.id, path=part.path)
        elapsed = (time.perf_counter() - start) * 1000
        assert len(seen) == PARTS and all("node" in d for d in seen)
        return elapsed

    # Measured: 94 to 165 ms alone, 465 ms with three test runs sharing the machine. The budget
    # guards against a cost that grows with the tree (each event walking its siblings, the marks
    # of a thread piling up), which would be ten times this, so it is three times the quiet figure.
    ms = best_of_three(run)
    limit = record("tree.announce_4500", ms, target_ms=200.0)
    assert ms <= limit, f"announcing {PARTS} changes took {ms:.0f} ms (budget {limit:.0f} ms)"


def test_the_budgeted_tree_is_the_whole_tree() -> None:
    """Not a budget: the figures above are about this tree."""
    project = many_parts(200)
    streamed = json.loads("".join(tree_stream(project, project.root)))
    assert streamed == tree_dict(project, project.root)
