"""Function-object monitors (T-311): the table reader, the agent's listing, the commands.

Every table here is written by the test, in the shape OpenFOAM 14's function objects write.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stokes.run.monitors import MAX_ROWS, is_table, monitor_name, parse_table

FORCES = """# Force
# CofR       : (0 0 0)
#
# Time  total_x total_y total_z
# Time  total pressure viscous
0.1  (1 2 3)  (0.5 1 1.5)  (0.5 1 1.5)
0.2  (2 3 4)  (1 1.5 2)    (1 1.5 2)
garbage line
0.3  (3 4 5)  (1.5 2 2.5)  (1.5 2 2.5)
"""


def test_a_table_with_vectors_reads_as_columns() -> None:
    table = parse_table(FORCES).to_json()
    assert table["columns"][:4] == ["Time", "total_x", "total_y", "total_z"]
    assert table["columns"][4] == "pressure_x"
    assert table["values"]["Time"] == [0.1, 0.2, 0.3]
    assert table["values"]["total_z"] == [3.0, 4.0, 5.0]
    assert table["truncated"] is False


def test_scalars_without_a_header_and_rows_that_do_not_fit() -> None:
    table = parse_table("1 2\n2 4\n3 nan\n4 8 9\n5 10\n").to_json()
    assert table["columns"] == ["col0", "col1"]
    assert table["values"]["col1"] == [2.0, 4.0, 10.0]


def test_a_long_table_keeps_the_whole_run_with_fewer_rows() -> None:
    text = "# Time p\n" + "".join(f"{n} {n * 2}\n" for n in range(MAX_ROWS * 3))
    table = parse_table(text)
    assert table.truncated and len(table.values[0]) <= MAX_ROWS
    assert table.values[0][0] == 0.0 and table.values[0][-1] > MAX_ROWS * 2.9


def test_names_and_suffixes() -> None:
    assert is_table("postProcessing/forces/0/forces.dat")
    assert not is_table("postProcessing/surfaces/0/U.vtk")
    assert monitor_name("postProcessing/probes/0/p") == "probes/0/p"


def test_the_agent_lists_what_the_function_objects_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes_agent import jobs
    from stokes_agent.engine_env import EngineEnv
    from stokes_agent.jobs import JobStore, Step

    engine = EngineEnv(
        str(tmp_path / "bashrc"), {"PATH": str(tmp_path)}, "OpenFOAM", "14", "org", 0.0
    )
    monkeypatch.setattr(jobs, "_posix_host", lambda: True)
    monkeypatch.setattr(jobs, "resolve_engine", lambda engine_id=None: engine)
    monkeypatch.setattr(jobs, "resolve_utility", lambda _engine, _name: sys.executable)
    store = JobStore(tmp_path / "jobs")
    job = store.create({"system/controlDict": "x"}, [Step("mesh", ("blockMesh",))])
    out = store.job_dir(job) / "postProcessing" / "forces(patches=(wall))" / "0"
    out.mkdir(parents=True)
    (out / "forces.dat").write_bytes(FORCES.encode())
    (out / "moment.dat").write_text("# Time m\n0.1 1\n")
    listed = store.outputs(job)
    assert [item["path"] for item in listed["files"]] == [
        "postProcessing/forces(patches=(wall))/0/forces.dat",
        "postProcessing/forces(patches=(wall))/0/moment.dat",
    ]
    assert store.read(job, listed["files"][0]["path"])["text"] == FORCES
    assert store.outputs(job)["truncated"] is False
