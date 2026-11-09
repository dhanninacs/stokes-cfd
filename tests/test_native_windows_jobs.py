"""Native supervision, trusted discovery and MPI wire boundaries; no OpenFOAM required."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from stokes_agent.engine_env import EngineEnv, resolve_utility
from stokes_agent.jobs import JobBadParams, JobMissingUtility, JobStore, JobUnsupported, Step
from stokes_agent.server import AgentBadParams, AgentServer


def test_ranks_round_trip_and_wire_validation() -> None:
    server = AgentServer()
    step = Step("solve", ("foamRun",), ranks=4)
    assert server._steps_from([step.to_json()])[0].ranks == 4
    assert server._steps_from([{"name": "serial", "argv": ["blockMesh"]}])[0].ranks == 1
    for invalid in (True, 0, -1, 257, 1.5, "4"):
        with pytest.raises(AgentBadParams):
            server._steps_from([{**step.to_json(), "ranks": invalid}])


def test_windows_feature_reader_runs_from_the_parent_with_a_relative_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snappyHexMesh cannot read an eMesh through an absolute Windows path (measured)."""
    from stokes_agent import jobs

    job_dir = tmp_path / "j_abc123"
    job_dir.mkdir()
    argv = ["C:/OpenFOAM/bin/snappyHexMesh.exe", "-parallel"]
    monkeypatch.setattr(jobs, "NATIVE_WINDOWS", True)
    assert jobs._native_case("snappyHexMesh", argv, job_dir) == (
        [*argv, "-case", "j_abc123"],
        tmp_path,
    )
    # Only the feature reader moves: the other utilities keep the job directory.
    block = ["C:/OpenFOAM/bin/blockMesh.exe"]
    assert jobs._native_case("blockMesh", block, job_dir) == (block, job_dir)
    monkeypatch.setattr(jobs, "NATIVE_WINDOWS", False)
    assert jobs._native_case("snappyHexMesh", argv, job_dir) == (argv, job_dir)


def test_native_tool_resolution_never_falls_back_to_path(tmp_path: Path) -> None:
    tool = tmp_path / "foamRun.exe"
    tool.touch()
    env = EngineEnv(
        "native",
        {"PATH": os.environ["PATH"]},
        "OpenFOAM",
        "14",
        "org",
        0,
        native_tools={"foamRun": str(tool)},
    )
    assert resolve_utility(env, "foamRun") == str(tool)
    assert resolve_utility(env, "python") is None
    tool.unlink()
    assert resolve_utility(env, "foamRun") is None


def test_native_handshake_filters_environment_and_checks_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes_agent import native_env
    from stokes_agent.engine_env import EngineEnvError

    appbin, libbin = tmp_path / "bin", tmp_path / "lib"
    appbin.mkdir()
    libbin.mkdir()
    for name in ("blockMesh", "checkMesh", "foamRun"):
        (appbin / f"{name}.exe").touch()
    script = tmp_path / "of14-env.ps1"
    script.touch()
    data = {
        "schemaVersion": 1,
        "distribution": "FoamWin",
        "openfoam": {"flavour": "org", "version": "14"},
        "environment": {
            "FOAM_APPBIN": str(appbin),
            "FOAM_LIBBIN": str(libbin),
            "PATH": "untrusted",
            "SECRET": "discard",
            "WM_PROJECT": "OpenFOAM",
        },
        "parallel": {"available": False},
    }
    monkeypatch.setattr(
        "stokes_agent.native_env.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b""),
    )
    env = native_env.capture(script, 5)
    assert "SECRET" not in env.env
    assert "untrusted" not in env.env["PATH"]
    assert env.native_tools["foamRun"] == str(appbin / "foamRun.exe")
    assert env.mpi_executable is None
    data["distribution"] = "unknown"
    with pytest.raises(EngineEnvError, match="unsupported handshake"):
        native_env.capture(script, 5)


def test_mpi_requires_allowed_utility_and_agent_selected_launcher(tmp_path: Path) -> None:
    env = EngineEnv(
        "native",
        {},
        "OpenFOAM",
        "14",
        "org",
        0,
        native_tools={"foamRun": sys.executable},
        mpi_executable=sys.executable,
    )
    store = JobStore(tmp_path / "jobs")
    store._check_steps([Step("solve", ("foamRun",), ranks=2)], env)
    with pytest.raises(JobBadParams, match="does not support"):
        store._check_steps([Step("mesh", ("blockMesh",), ranks=2)], env)
    with pytest.raises(JobBadParams, match="not allowed"):
        store._check_steps([Step("solve", ("foamRun", "-parallel"), ranks=2)], env)
    assert (
        store._steps_of("unused", {"steps": [Step("solve", ("foamRun",), ranks=4).to_json()]})[
            0
        ].ranks
        == 4
    )


def test_missing_native_mpi_explains_installation(tmp_path: Path) -> None:
    env = EngineEnv(
        "native", {}, "OpenFOAM", "14", "org", 0, native_tools={"foamRun": sys.executable}
    )
    with pytest.raises(JobMissingUtility, match=r"msmpisetup\.exe") as caught:
        JobStore(tmp_path / "jobs")._check_steps([Step("solve", ("foamRun",), ranks=2)], env)
    assert "microsoft.com" in str(caught.value)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows job directory rule")
def test_native_job_directory_with_whitespace_is_refused_before_staging(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "with spaces")
    with pytest.raises(JobUnsupported, match="STOKES_AGENT_HOME"):
        store.create({}, [Step("mesh", ("blockMesh",))])
    assert not list(store.root.glob("j_*"))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows runner")
def test_native_local_runner_answers_and_shuts_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.run.local import LocalRunner

    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    runner = LocalRunner()
    try:
        assert runner.available
        assert runner.call("ping") == {"pong": True}
    finally:
        runner.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel Job Objects")
def test_windows_process_runs_and_cancellation_kills_descendant(tmp_path: Path) -> None:
    from stokes_agent.windows_process import launch, owns, process_start, release, terminate

    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time, pathlib\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path('child.json').write_text(str(p.pid))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    with (tmp_path / "out.log").open("wb") as output:
        process = launch([sys.executable, str(script)], tmp_path, dict(os.environ), output)
        try:
            deadline = time.monotonic() + 10
            marker = tmp_path / "child.json"
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), (tmp_path / "out.log").read_text()
            child = int(marker.read_text())
            assert process_start(child) is not None
            assert owns(process.pid)
            terminate(process.pid)
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while process_start(child) is not None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert process_start(child) is None
        finally:
            release(process.pid)
            process.wait(timeout=5)
        assert not owns(process.pid)


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel Job Objects")
def test_agent_exit_closes_job_and_kills_child(tmp_path: Path) -> None:
    script = tmp_path / "owner.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "from stokes_agent.windows_process import launch\n"
        "root = pathlib.Path(sys.argv[1])\n"
        "with (root/'out.log').open('wb') as f:\n"
        "    args = [sys.executable, '-c', 'import time; time.sleep(60)']\n"
        "    p = launch(args, root, dict(os.environ), f)\n"
        "    (root/'pid.json').write_text(str(p.pid))\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        check=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    from stokes_agent.windows_process import process_start

    child = json.loads((tmp_path / "pid.json").read_text())
    deadline = time.monotonic() + 5
    while process_start(child) is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert process_start(child) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows MS-MPI process supervision")
def test_msmpi_cancellation_reaches_all_ranks(tmp_path: Path) -> None:
    from stokes_agent.windows_process import launch, process_start, release, terminate

    launcher = Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft MPI/Bin/mpiexec.exe"
    if not launcher.is_file():
        pytest.skip("Microsoft MPI runtime is not installed")
    script = tmp_path / "rank.py"
    script.write_text(
        "import os, pathlib, time\n"
        "pathlib.Path('rank_' + str(os.getpid())).write_text('ready')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    with (tmp_path / "mpi.log").open("wb") as output:
        process = launch(
            [str(launcher), "-n", "2", sys.executable, str(script)],
            tmp_path,
            dict(os.environ),
            output,
        )
        try:
            deadline = time.monotonic() + 15
            while len(list(tmp_path.glob("rank_*"))) < 2 and time.monotonic() < deadline:
                time.sleep(0.03)
            ranks = [int(path.name.removeprefix("rank_")) for path in tmp_path.glob("rank_*")]
            assert len(ranks) == 2, (tmp_path / "mpi.log").read_text()
            assert all(process_start(pid) is not None for pid in ranks)
            terminate(process.pid)
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while (
                any(process_start(pid) is not None for pid in ranks) and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert all(process_start(pid) is None for pid in ranks)
        finally:
            release(process.pid)
            process.wait(timeout=5)


def test_native_tools_include_the_external_list_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0029: an imported tutorial runs `icoFoam` or `topoSet`, so the handshake offers the
    external profile's names too - and still only names on a closed list, from FOAM_APPBIN."""
    from stokes_agent import native_env

    appbin, libbin = tmp_path / "bin", tmp_path / "lib"
    appbin.mkdir()
    libbin.mkdir()
    for name in ("blockMesh", "checkMesh", "foamRun", "icoFoam", "topoSet", "someoneElsesTool"):
        (appbin / f"{name}.exe").touch()
    script = tmp_path / "of14-env.ps1"
    script.touch()
    data = {
        "schemaVersion": 1,
        "distribution": "FoamWin",
        "openfoam": {"flavour": "org", "version": "14"},
        "environment": {"FOAM_APPBIN": str(appbin), "FOAM_LIBBIN": str(libbin)},
        "parallel": {"available": False},
    }
    monkeypatch.setattr(
        "stokes_agent.native_env.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b""),
    )
    env = native_env.capture(script, 5)
    assert env.native_tools["icoFoam"] == str(appbin / "icoFoam.exe")
    assert env.native_tools["topoSet"] == str(appbin / "topoSet.exe")
    assert "someoneElsesTool" not in env.native_tools


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows only")
def test_a_confined_child_dies_when_its_host_is_killed() -> None:
    """The WSL agent's `wsl.exe` outlived every desktop host that crashed or was killed, because
    only a graceful close stopped it. `confine` ties a child to the calling process: a host that
    exits without cleaning up takes the child with it."""
    host = (
        "import subprocess, sys, os\n"
        "from stokes_agent.windows_process import confine\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "confine(child.pid)\n"
        "print(child.pid, flush=True)\n"
        "os._exit(0)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", host], capture_output=True, text=True, timeout=30, check=True
    )
    pid = int(out.stdout.strip())
    deadline = time.monotonic() + 10
    alive = True
    while alive and time.monotonic() < deadline:
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        )
        alive = str(pid) in probe.stdout
        if alive:
            time.sleep(0.2)
    assert not alive, f"child {pid} outlived its host"
