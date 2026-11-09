"""The field extractor against a real solved duct (T-215).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_agent_fields_engine.py -s

**What only this test can check.** `tests/test_agent_fields.py` builds a one-hex case by hand, so
it proves the extractor reads a case *we* wrote with fields *we* wrote. This runs a real `blockMesh`
and a real `foamRun` inside a real job and reads what OpenFOAM actually produced — which is where a
VTK version difference, a patch-naming change or a time-folder rule would show up. The duct is
T-212's golden case: 250 x 10 x 10 cells, inlet 0.075 m/s, `p` zero at the outlet.

**The three physics checks** the brief names are here, and each is a claim about the *flow* rather
than about the code:

* `U_mag` on `walls` is <= 1e-6 — no-slip.
* `U_mag` on `inlet` is the inlet speed.
* `p.max` is at the inlet and `p.min` at the outlet, because the outlet is the fixed-pressure patch.
* The slice's `U_mag` maximum is above the inlet speed: a developed duct's centre-line is faster
  than its mean. **The ratio is printed and asserted only above 1.8** (correction 2 of the brief):
  the analytic value for a square duct is 2.096, but a 10 x 10 cross-section with cell-to-point
  values gives about 2.0, and the duct is only 500 mm long so the entrance region is developing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes_agent.jobs import JobStore, Step

pytestmark = [pytest.mark.engine, pytest.mark.slow]

#: The duct's written case, from T-212. Used rather than re-derived so the fields this reads are the
#: fields the compiler wrote — a second, hand-typed case would be a second thing to be wrong.
GOLDEN = Path(__file__).parent.parent / "golden" / "compile" / "of14" / "duct"

#: The inlet speed and the side of the square cross-section, from the golden case.
INLET_SPEED = 0.075
SIDE = 0.02
#: The duct's length. **The mid-plane cuts length x side**, which is the area the slice test needs;
#: `SIDE * SIDE` would be a cross-section and is not what a plane through the axis cuts.
LENGTH = 0.5

#: A short solve. **The mesh and the fields, not the converged answer**, is what this test reads, so
#: 200 iterations at `writeInterval` 100 gives two written times to choose between — which is the
#: point: the extractor must take the LATEST one, and a case with one time cannot tell that apart
#: from taking the first.
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
endTime 200;
deltaT 1;
writeControl timeStep;
writeInterval 100;
writeFormat ascii;
writeCompression off;
"""


def duct_block_mesh_dict() -> str:
    """The duct's `blockMeshDict`, **written with the printer** exactly as T-212's engine test
    does.

    A hand-written dict would be a second, unverified way of producing the mesh; this is the same
    printer the case writer uses, so the mesh and the fields come from one place.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_compile_of14_engine import duct_block_mesh_dict as written

    return written()


def _duct_files() -> dict[str, str]:
    """The duct case as a job's files: every golden file plus the meshing dict and a short run."""
    files: dict[str, str] = {}
    for path in sorted(GOLDEN.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(GOLDEN)).replace("\\", "/")] = path.read_text()
    files["system/blockMeshDict"] = duct_block_mesh_dict()
    files["system/controlDict"] = CONTROL_DICT
    return files


@pytest.fixture(scope="module")
def solved(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The duct, meshed and solved inside one job. Module-scoped: a solve is about a minute.

    **`extract.surfaces` first, then `extract.fields`**, because the field extractor is given the
    surfaces header and must agree with it. Both artifacts live in the same job, which is the
    ordinary single-job case.
    """
    from stokes_agent.engine_env import engine_id, get_env
    from stokes_agent.extract import extract_fields, extract_surfaces

    root = tmp_path_factory.mktemp("fields")
    store = JobStore(root / "jobs")
    job = store.create(
        files=_duct_files(),
        steps=[
            Step(name="blockMesh", argv=["blockMesh"], timeout_s=300),
            Step(
                name="foamRun",
                argv=["foamRun", "-solver", "incompressibleFluid"],
                timeout_s=1200,
            ),
        ],
        install=engine_id(get_env(None).bashrc),
        label="duct-fields",
    )
    store.start(job)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        state = store.status(job)
        if state.get("state") in ("done", "failed", "cancelled", "interrupted"):
            break
        time.sleep(0.25)
    state = store.status(job)
    assert state["state"] == "done", state

    job_dir = store.job_dir(job)
    surfaces_data, surfaces_header = extract_surfaces(job_dir)
    started = time.monotonic()
    data, header = extract_fields(job_dir, surfaces_header, None)
    elapsed = time.monotonic() - started
    print(
        f"\nfields extraction: {elapsed:.2f}s, artifact {len(data)} bytes "
        f"({len(data) / 1024:.0f} KiB), time {header['time']}"
    )
    return {
        "job": job,
        "job_dir": job_dir,
        "surfaces": surfaces_header,
        "surfaces_data": surfaces_data,
        "header": header,
        "data": data,
        "seconds": elapsed,
    }


def _fields_of(header: dict[str, Any], data: bytes, name: str) -> dict[str, np.ndarray]:
    """The named patch's arrays, read back through the test's own reader."""
    from stokes_agent.extract import unpack_fields

    _, arrays = unpack_fields(data)
    return arrays[name]


def test_the_patch_points_are_the_surfaces_points(solved: dict[str, Any]) -> None:
    """**The invariant.** Every patch's `points` equals the surfaces artifact's, in the same order.

    This is the whole reason the task exists: the desktop tints the vertices it draws from the
    surfaces artifact, so a field array indexed by different vertices is a wrong picture rather than
    a visible failure.
    """
    surface = solved["surfaces"]["patches"]
    field = solved["header"]["patches"]
    assert [entry["name"] for entry in field] == [entry["name"] for entry in surface]
    assert [entry["points"] for entry in field] == [entry["points"] for entry in surface]
    assert [entry["index"] for entry in field] == [entry["index"] for entry in surface]


def test_the_reader_reports_the_latest_time(solved: dict[str, Any]) -> None:
    """**A written time later than 0, not time 0.** A reader that took the first value would be
    reading the initial condition, where `U` and `p` are both zero.

    **Not `endTime`.** The controlDict asks for 200 iterations with `writeInterval 100`, but
    `residualControl` stops the solve as soon as `p` and `U` reach 1e-5 — measured at **64** on this
    case — so a time folder later than 0 exists and 100 never does. Asserting `== 200` failed for a
    reason that has nothing to do with the reader.
    """
    written = solved["header"]["time"]
    assert written > 0, f"the reader took time 0, the initial condition: {written}"
    assert (solved["job_dir"] / f"{int(written)}" / "U").is_file(), "no U at the reported time"
    # **And it is the LATEST**, which is the property under test: no time folder with a larger name
    # holds a U.
    times = sorted(
        int(p.name) for p in solved["job_dir"].iterdir() if p.is_dir() and p.name.isdigit()
    )
    assert int(written) == times[-1], f"reported {written}, latest is {times[-1]}"


def test_the_walls_are_at_rest(solved: dict[str, Any]) -> None:
    """**No-slip**: the wall's own `U` is zero, which is what patch point values must give.

    If this fails with the *volume's* values at the wall, the patch arrays are being read from the
    neighbouring cells instead of the patch's own faces, and the brief's correction 3 says to assert
    on the cell data instead — so the failure is reported with the observed maximum, which is what
    tells the two apart.
    """
    arrays = _fields_of(solved["header"], solved["data"], "walls")
    highest = float(np.max(arrays["U_mag"]))
    print(f"\nwalls U_mag max: {highest:.6e}")
    assert highest <= 1e-6, f"the walls are not at rest: U_mag reaches {highest}"


def test_the_inlet_carries_the_inlet_speed(solved: dict[str, Any]) -> None:
    """**`U_mag` on `inlet` is the inlet speed** — 0.075 m/s, from the golden `0/U`."""
    arrays = _fields_of(solved["header"], solved["data"], "inlet")
    values = np.asarray(arrays["U_mag"])
    print(f"\ninlet U_mag: min {values.min():.6f} max {values.max():.6f} mean {values.mean():.6f}")
    assert float(values.min()) == pytest.approx(INLET_SPEED, abs=1e-6)
    assert float(values.max()) == pytest.approx(INLET_SPEED, abs=1e-6)


def test_the_pressure_is_highest_at_the_inlet_and_lowest_at_the_outlet(
    solved: dict[str, Any],
) -> None:
    """`p` is zero at the `outlet`, which is the fixed-pressure patch, so the inlet is the highest.

    Checked over the patch arrays rather than a single value: a sign error or a swapped patch would
    make this the other way round.
    """
    inlet = np.asarray(_fields_of(solved["header"], solved["data"], "inlet")["p"])
    outlet = np.asarray(_fields_of(solved["header"], solved["data"], "outlet")["p"])
    print(
        f"\np inlet {inlet.min():.6e}..{inlet.max():.6e}, "
        f"outlet {outlet.min():.6e}..{outlet.max():.6e}"
    )
    assert float(inlet.max()) > 0.0, "the inlet pressure is not positive"
    assert float(outlet.max()) == pytest.approx(0.0, abs=1e-9)
    assert float(inlet.max()) > float(outlet.max())


def test_the_slice_has_geometry_and_no_duplicate_triangles(solved: dict[str, Any]) -> None:
    """The default plane cuts the duct once: 250 x 10 cells give 2,500 quads, hence 5,000 triangles.

    **No duplicate index triples** (sorted, ignoring winding) and **the area is
    0.5 x 0.02 m2 within 1 %** — corrections 1 and 4 of the brief.

    **What this does NOT prove, measured rather than assumed.** Removing the default plane's offset
    and cutting exactly on the 2 mm face plane z = 0.010 also gives 2,761 points, 5,000 triangles
    and zero duplicates on this fixture with VTK 9.6.2, so this test does *not* fail when the
    offset is removed: the two planes agree on this fixture. The offset follows the brief's
    correction 1 and costs nothing, but here it is not what makes the assertions true, and saying
    otherwise would be a test passing for a reason it does not have. The duplicates are a
    `vtkCutter` property that would show on a mesh whose cut plane lands on a face with differing
    values either side; this case does not produce them.

    **The offset is not untested, though** — `test_the_default_plane_does_not_lie_on_a_face` in
    `tests/test_agent_fields.py` asserts the origin's z exactly, and **removing the offset fails
    that test** (verified). So correction 1 is mutation-covered at the unit level; what this engine
    test cannot do is show that the offset changes the *geometry* on this fixture.
    """
    from stokes_agent.extract import duplicate_triangles, unpack_fields

    slice_ = solved["header"]["slice"]
    assert slice_ is not None, "the default plane produced no slice section"
    assert slice_["triangles"] > 0, "the default plane cut nothing"
    _, arrays = unpack_fields(solved["data"])
    triangles = arrays["__slice__"]["triangles"]
    print(
        f"\nslice: {slice_['points']} points, {slice_['triangles']} triangles, "
        f"{slice_['edges']} edges"
    )
    assert duplicate_triangles(triangles) == 0, "the slice repeats triangles"

    positions = arrays["__slice__"]["positions"].astype("float64")
    corners = positions[triangles.astype("int64")]
    area = float(
        np.linalg.norm(
            np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
        ).sum()
        / 2.0
    )
    # **The mid-plane of the duct, not a cross-section.** The plane cuts length x width, so
    # `LENGTH * SIDE = 0.5 * 0.02 = 0.01 m2`. The first version used `SIDE * 0.02 = 0.0004`,
    # which is
    # the INLET FACE area — a plausible-looking formula that made the check fail on a correct slice.
    expected = LENGTH * SIDE
    print(f"\nslice area: {area:.6e} m2 against {expected:.6e} m2")
    assert area == pytest.approx(expected, rel=0.01), f"the slice area is {area}, not {expected}"


def test_the_centre_line_is_faster_than_the_mean(solved: dict[str, Any]) -> None:
    """**A developed duct's centre-line is faster than its mean**, which is the physics check that
    the slice carries real values and not the boundary's.

    Correction 2 of the brief: the analytic square-duct ratio is 2.096, a 10 x 10 cell-to-point
    cross-section gives about 2.0, and this duct is only 500 mm long so it is still developing. The
    ratio is **printed** and only asserted above 1.8, which is the honest bound.
    """
    from stokes_agent.extract import unpack_fields

    _, arrays = unpack_fields(solved["data"])
    magnitude = np.asarray(arrays["__slice__"]["U_mag"]).ravel()
    assert magnitude.size > 0
    ratio = float(magnitude.max()) / INLET_SPEED
    # Measured on this host: 1.987. The analytic developed square-duct value is 2.096 and the brief
    # says to assert only above 1.8, which is what this does.
    print(f"\ncentre-line ratio u_max/u_inlet: {ratio:.3f}")
    assert ratio > 1.8, f"the centre line is only {ratio:.3f} times the inlet speed"
    assert ratio < 2.5, f"the centre line is {ratio:.3f} times the inlet speed, which is too fast"


def test_the_artifact_is_written_through_the_store(solved: dict[str, Any]) -> None:
    """The size, the digest and the index entry, as `extract.surfaces` does it.

    A cheap check, and the one a client relies on: the artifact the header describes is the file the
    protocol will serve, with a digest that matches it.
    """
    import hashlib

    from stokes_agent.extract import cached_artifact, write_artifact

    written = write_artifact(solved["job_dir"], "fields.stkfld", solved["data"])
    assert written["size"] == len(solved["data"])
    assert written["sha256"] == hashlib.sha256(solved["data"]).hexdigest()
    cached = cached_artifact(solved["job_dir"], "fields.stkfld")
    assert cached is not None and cached["size"] == len(solved["data"])
    index = json.loads((solved["job_dir"] / "artifacts" / "index.json").read_text())
    assert index["fields.stkfld"]["size"] == len(solved["data"])
