"""The extractor against a real `blockMesh` case (T-209).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_agent_extract_engine.py

**What only this test can check.** `tests/test_agent_extract.py` builds a one-hex case by hand,
so it
proves the extractor reads a case *we* wrote. This runs a real `blockMesh` through the real
`JobStore` and
reads the mesh OpenFOAM produced — which is where a VTK version difference or a patch-naming
change would
show up.

The `slow` test is the **size probe**: the first data point for the 10 s budget in
`docs/engineering/performance.md`. It is not a gate.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from stokes_agent.jobs import JobStore, Step

pytestmark = pytest.mark.engine

#: The cavity golden from T-201: 20 x 20 x 1 cells, three patches.
CAVITY = Path(__file__).parent.parent / "golden" / "compile" / "blockMeshDict.cavity"

CONTROL_DICT = """FoamFile
{
    format      ascii;
    class       dictionary;
    object      controlDict;
}
solver incompressibleFluid;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 1;
deltaT 1;
writeControl timeStep;
writeInterval 1;
writeFormat ascii;
writeCompression off;
"""


def mesh_cavity(store: JobStore) -> str:
    """Create a job, run `blockMesh` in it, and return its id."""
    import subprocess

    from stokes_agent.engine_env import engine_id, get_env

    job = store.create(
        files={
            "system/blockMeshDict": CAVITY.read_text(),
            "system/controlDict": CONTROL_DICT,
        },
        steps=[Step(name="blockMesh", argv=["blockMesh"], timeout_s=300)],
        install=engine_id(__import__("os").environ["STOKES_OPENFOAM_BASHRC"]),
        label="cavity",
    )
    store.start(job)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = store.status(job)
        if state.get("state") in ("done", "failed", "cancelled", "interrupted"):
            break
        time.sleep(0.05)
    assert store.status(job)["state"] == "done", store.status(job)
    del subprocess, get_env
    return job


def test_the_cavity_extracts_with_the_patches_the_brief_names(tmp_path: Path) -> None:
    """`movingWall`, `fixedWalls`, `frontAndBack`, and `frontAndBack` has `2 x 400` faces.

    The brief asked whether VTK reports `empty` patches at all. **It does**: `frontAndBack` is
    `type empty`
    and reports 800 polys for a 20 x 20 x 1 mesh. Measured, so the assertion is on all three.
    """
    from stokes_agent import extract

    store = JobStore(tmp_path)
    job = mesh_cavity(store)
    job_dir = store.job_dir(job)

    started = time.monotonic()
    data, header = extract.extract_surfaces(job_dir)
    seconds = time.monotonic() - started

    by_name = {entry["name"]: entry for entry in header["patches"]}
    assert set(by_name) == {"movingWall", "fixedWalls", "frontAndBack"}
    # 20 x 20 = 400 cells: movingWall and fixedWalls are the four sides, frontAndBack is both z
    # planes.
    assert by_name["movingWall"]["faces"] == 20
    assert by_name["fixedWalls"]["faces"] == 60
    assert by_name["frontAndBack"]["faces"] == 800
    assert by_name["frontAndBack"]["patch_type"] == "empty"
    assert by_name["movingWall"]["patch_type"] == "wall"
    # **The order is `constant/polyMesh/boundary`'s**, which is what a client indexes by.
    assert [entry["name"] for entry in header["patches"]] == [
        "movingWall",
        "fixedWalls",
        "frontAndBack",
    ]
    assert data[:8] == extract.MAGIC
    print(f"\nextract.surfaces: {seconds * 1000:.0f} ms, {len(data) / 1024:.1f} KiB")


def test_the_whole_artifact_fetches_in_chunks_and_matches(tmp_path: Path) -> None:
    """The chunked fetch the desktop will do, verified against the digest."""
    from stokes_agent import extract

    store = JobStore(tmp_path)
    job = mesh_cavity(store)
    job_dir = store.job_dir(job)
    data, _ = extract.extract_surfaces(job_dir)
    written = extract.write_artifact(job_dir, "surfaces.stkmsh", data)
    assert written["sha256"] == hashlib.sha256(data).hexdigest()

    # A small chunk size, so the loop really loops.
    chunk = 4096
    assembled = b""
    while len(assembled) < written["size"]:
        reply = extract.read_chunk(job_dir, "surfaces.stkmsh", len(assembled), chunk)
        assert reply["sha256"] == written["sha256"], "the digest is of the whole file"
        assembled += reply["data"]
    assert assembled == data


@pytest.mark.slow
def test_the_size_probe_1m_cells(tmp_path: Path) -> None:
    """**The data point for the 10 s budget**, not a gate.

    Measured on thinkyboy (4 cores, 7.1 GiB): a 100 x 100 x 100 box is **1.26 s** to extract, 2.98
    MiB. Four sizes were measured by hand (`/tmp/stokes_perf`, revision `c039ee3d`):

        1.00 M cells  1.26 s  2.98 MiB     2.74 M  3.63 s  5.83 MiB
        1.73 M cells  2.28 s  4.28 MiB     4.10 M  5.27 s  7.62 MiB

    **Cleanly linear**: `t = 0.017 s + 1.293 us x cells` (R2 = 0.9990), so 10 M cells extrapolates
    to **12.9 s — over the 10 s budget.** The cost is not in this module: `open_case` (the VTK
    reader) is **93%** of it, because VTK parses the whole ASCII `polyMesh` whatever is asked of it.
    Larger sizes could not be built here at all — `blockMesh` at 200^3 (8 M cells) is OOM-killed at
    ~1 KB/cell against 7.28 GiB of RAM. The levers are a binary mesh or reading `polyMesh` directly,
    not faster numpy.

    This test runs the smallest of those sizes so the budget has a check that reproduces.
    """
    from stokes_agent import extract
    from stokes_agent.engine_env import engine_id, get_env  # noqa: F401 - the environment
    from stokes_agent.jobs import JobStore, Step

    store = JobStore(tmp_path)
    job = store.create(
        files={
            "system/blockMeshDict": (
                "FoamFile\n{\n    format ascii;\n    class dictionary;\n"
                "    object blockMeshDict;\n}\n"
                "scale 1.0;\n"
                "vertices\n(\n(0 0 0) (1 0 0) (1 1 0) (0 1 0)\n"
                "(0 0 1) (1 0 1) (1 1 1) (0 1 1)\n);\n"
                "blocks\n(\n    hex (0 1 2 3 4 5 6 7) (100 100 100) simpleGrading (1 1 1)\n);\n"
                "boundary\n(\n    all\n    {\n        type patch;\n"
                "        faces ((0 3 2 1) (4 5 6 7) (0 1 5 4) (1 2 6 5) (2 3 7 6) (3 0 4 7));\n"
                "    }\n);\n"
            ),
            "system/controlDict": CONTROL_DICT,
        },
        steps=[Step(name="blockMesh", argv=["blockMesh"], timeout_s=600)],
        install=engine_id(__import__("os").environ["STOKES_OPENFOAM_BASHRC"]),
        label="probe",
    )
    store.start(job)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if store.status(job).get("state") in ("done", "failed", "cancelled", "interrupted"):
            break
        time.sleep(0.1)
    state = store.status(job)
    assert state["state"] == "done", state
    # **The cell count, from blockMesh's own log**, so the timing is attached to a known size.
    log = (store.job_dir(job) / "log.blockMesh").read_text()
    assert "nCells: 1000000" in log, log[-1500:]

    started = time.monotonic()
    data, header = extract.extract_surfaces(store.job_dir(job))
    seconds = time.monotonic() - started
    faces = sum(entry["faces"] for entry in header["patches"])
    print(
        f"\nSIZE PROBE: 1,000,000 cells -> extract_surfaces {seconds:.2f}s, "
        f"{len(data) / 2**20:.2f} MiB, {faces} faces"
    )
    # The box's surface is 6 x 100 x 100 faces; the counts are what prove the whole mesh was read.
    assert faces == 60_000
    assert len(data) < 8 * 1024 * 1024, "a 1M-cell surface is well under one chunk"
