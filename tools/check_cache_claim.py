"""The T-096 fix: two threads wanting one part's cached mesh open its file once, not twice.

`_load_cache` claimed a part under the store lock so a second caller waits for the first reader.
This checks the claim directly, at the level the fix lives at, without a server or a browser:

  * N threads asking for the same part at the same time -> exactly 1 cache read, N correct results
  * threads asking for different parts -> one read each, nothing serialised behind the others
  * a part whose cache file is absent -> every caller gets None, and the next call tries again
    (the failure is not cached)

Run with the venv python:
    python tools/check_cache_claim.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from stokes.model.nodes import Node

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import numpy as np

    from stokes.geometry import cache as cache_mod
    from stokes.geometry.mesh import PartMesh, TriMesh
    from stokes.geometry.store import GeometryStore
    from stokes.geometry.transport import pack_part
    from stokes.model.ids import new_id

    mesh = TriMesh(
        "m",
        "solid",
        (0.5, 0.5, 0.5),
        np.zeros((3, 3), np.float32),
        np.zeros((3, 3), np.float32),
        np.zeros((1, 3), np.uint32),
        None,
        0,
        0,
        "fine",
    )
    payload = pack_part(PartMesh((mesh,), "m", None))

    folder = Path(tempfile.mkdtemp(prefix="t096-claim-"))
    node_id = new_id()

    failures: list[str] = []

    # --- the store, with just enough of a session for _load_cache -------------------------------
    class FakeNode:
        def __init__(self, node_id: str) -> None:
            self.id = node_id
            self.type = "part"
            self.status = ""
            self.params: dict[str, object] = {}

    class FakeProject:
        def __init__(self, nodes: dict[str, FakeNode]) -> None:
            self._nodes = nodes

        def find(self, ref: str) -> FakeNode | None:
            return self._nodes.get(ref)

    class FakeSession:
        def __init__(self, project: FakeProject, path: Path) -> None:
            self.project = project
            self.project_path = path

    store = GeometryStore.__new__(GeometryStore)
    node = FakeNode(node_id)
    project = FakeProject({node_id: node})
    store._session = FakeSession(project, folder)  # type: ignore[assignment]
    store._lock = threading.RLock()
    store._entries = {}
    store._reading = {}
    store._errors = {}
    store._cached = set()

    # --- 1. many threads, one part: one read ----------------------------------------------------
    reads = [0]
    real_read = cache_mod.read_cache

    def counted(project_dir: Path, wanted: str, source: Path | None) -> object:
        reads[0] += 1
        threading.Event().wait(0.02)  # hold the claim open so the others must wait
        return real_read(project_dir, wanted, source)

    cache_mod.read_cache = counted  # type: ignore[assignment]

    def load(wanted: FakeNode) -> object:
        """`_load_cache` through a cast: the fakes carry only what it touches."""
        return store._load_cache(cast("Node", wanted))

    try:
        cache_mod.write_cache(folder, node_id, payload)
        results: list[object] = []
        gate = threading.Barrier(8)

        def ask() -> None:
            gate.wait()
            results.append(load(node))

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if reads[0] != 1:
            failures.append(f"8 threads on one part made {reads[0]} cache reads, expected 1")
        if len(results) != 8 or any(r is None for r in results):
            failures.append(f"a caller got no mesh: {[r is None for r in results]}")
        if len({id(r) for r in results}) != 1:
            failures.append("the callers did not share one MeshEntry")
        print(f"1. eight threads, one part: {reads[0]} cache read(s), {len(results)} results")

        # --- 2. different parts are not serialised behind each other ----------------------------
        reads[0] = 0
        other = new_id()
        cache_mod.write_cache(folder, other, payload)
        other_node = FakeNode(other)
        project._nodes[other] = other_node
        store._entries.clear()
        store._reading.clear()

        started = threading.Event()
        release = threading.Event()

        def slow_read(project_dir: Path, wanted: str, source: Path | None) -> object:
            # Only the part under test is held open; the other part must go straight through, or
            # this would be measuring the test's own block rather than the claim.
            if wanted == node_id:
                reads[0] += 1
                started.set()
                release.wait(2.0)
            return real_read(project_dir, wanted, source)

        cache_mod.read_cache = slow_read  # type: ignore[assignment]
        t1 = threading.Thread(target=lambda: load(node))
        t1.start()
        started.wait(2.0)
        # While one part is being read, another part must not block on it.
        done = threading.Event()

        def read_other() -> None:
            load(other_node)
            done.set()

        t2 = threading.Thread(target=read_other)
        t2.start()
        blocked = not done.wait(0.5)
        release.set()
        t1.join()
        t2.join()
        if blocked:
            failures.append("a read of one part blocked a read of a different part")
        print(f"2. a different part is not blocked by a claim: {'no' if blocked else 'yes'}")

        # --- 3. an absent cache is not a cached failure -----------------------------------------
        cache_mod.read_cache = counted  # type: ignore[assignment]  # check 2's hold is done with
        reads[0] = 0
        missing = new_id()
        missing_node = FakeNode(missing)
        project._nodes[missing] = missing_node
        if load(missing_node) is not None:
            failures.append("a part with no cache file returned a mesh")
        if missing in store._reading:
            failures.append("the claim was left behind after a miss")
        first = reads[0]
        load(missing_node)
        if reads[0] != first + 1:
            failures.append("a missing cache was not retried on the next call")
        print(f"3. a miss is retried: {first} then {reads[0]} read(s)")
    finally:
        cache_mod.read_cache = real_read

    if failures:
        print("\nFAIL")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nPASS: 3/3 checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
