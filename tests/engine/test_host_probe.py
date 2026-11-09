"""The host record on a machine that actually has OpenFOAM (T-140).

Marked `engine`, so `tests/conftest.py` skips it unless `STOKES_ENGINE_TESTS=1`. On the mesh host:

    STOKES_OPENFOAM_BASHRC=<the install's env script> PYTHONPATH=.:agent \\
        STOKES_ENGINE_TESTS=1 python3 -m pytest -q tests/engine/test_host_probe.py

**What only this test can check.** `tests/test_agent_engine_env.py` builds its own bashrc, so it
proves the capture, the filtering and the refusal paths — but nothing there proves the real
install is
*found*, that its environment survives the filter, or that the utilities the slice drives resolve
through the captured PATH. Those are facts about a machine, and this is the test that asserts them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stokes_agent.engine_env import UTILITIES, get_env, host_record, utilities

pytestmark = pytest.mark.engine


def test_the_host_record_finds_a_supported_install() -> None:
    """One supported install, with the paths the mesh slice needs, on this host."""
    record = host_record(refresh=True)
    assert record["system"] == "Linux", f"expected the mesh host, got {record['system']}"
    assert record["installs"], "no OpenFOAM install found on a host that has one"
    supported = [entry for entry in record["installs"] if entry["supported"]]
    assert len(supported) >= 1, f"no SUPPORTED install: {json.dumps(record['installs'], indent=1)}"
    install = supported[0]
    assert install["flavour"] == "org" and install["version"] == "14", install
    assert record["default_install"] == install["bashrc"]
    assert record["why"] == "", "a host with a supported install must not carry a reason"

    # **The three the first slice actually drives.** `blockMesh` and `checkMesh` are the brief's own
    # pair; `foamRun` is what makes it a skeleton rather than a mesher.
    for name in ("blockMesh", "checkMesh", "foamRun"):
        path = install["utilities"].get(name)
        assert path, f"{name} did not resolve through the captured PATH"
        assert Path(path).is_file(), f"{name} resolves to {path}, which is not a file"


def test_the_captured_environment_is_enough_to_run_a_utility() -> None:
    """**The brief's `[VERIFY-OF14]` item, as a test.** The filtered environment must be enough
    a
    utility: if a key were missing, it would fail to find a library and every later task would
    be debugging the wrong thing."""
    env = get_env()
    completed = subprocess.run(
        ["blockMesh", "-help"],
        capture_output=True,
        timeout=60,
        check=False,
        env=env.env,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0, (
        f"blockMesh -help exited {completed.returncode} with only the filtered environment: "
        f"{completed.stderr[-400:].decode(errors='replace')}"
    )
    assert b"Usage" in completed.stdout or b"Usage" in completed.stderr, (
        "blockMesh ran but printed no usage, so it did not get as far as argument parsing"
    )


def test_the_utility_census_matches_the_record() -> None:
    """Every name in `UTILITIES` is answered, and each answer is true.

    A path in the record that does not exist would have T-204 report a host as able to do
    something it cannot. **`cartesianMesh` is expected to be absent on this host** (it is
    so the test asserts the answer is HONEST rather than that everything is present.
    """
    env = get_env()
    found = utilities(env)
    assert set(found) == set(UTILITIES)
    for name, path in found.items():
        if path is None:
            continue
        assert Path(path).is_file(), f"{name} is recorded at {path}, which does not exist"
    assert found["cartesianMesh"] is None, (
        "cartesianMesh resolved, but the inventory says it is quarantined and unusable — if this "
        "has gained a working one, the record and MESH-ENV.md both need updating"
    )
    # And the OpenFOAM core is definitely there: a host where none of these resolve is not a mesh
    # host.
    for name in ("blockMesh", "checkMesh", "snappyHexMesh"):
        assert found[name] is not None, f"{name} missing from a supported install"


def test_no_utility_was_left_running() -> None:
    """**The brief's rule after every engine run**: no strays.

    A utility left behind holds the case directory open and makes the next run fail with a lock
    which reads as a bug in our code.
    """
    completed = subprocess.run(
        ["pgrep", "-u", str(__import__("os").getuid()), "-a", "blockMesh|checkMesh|foamRun"],
        capture_output=True,
        timeout=20,
        check=False,
    )
    # `pgrep` exits 1 when nothing matches, which is the success case here.
    if completed.returncode != 0:
        return
    # **Only processes from the real install count.**
    #
    # The pattern matches by NAME, and `tests/test_agent_jobs.py` deliberately creates shell scripts
    # The pattern matches by NAME, and `tests/test_agent_jobs.py` deliberately creates shell
    # scripts called `blockMesh` in its own temp directories. Matching on the name alone
    # therefore reported a test fake as a leaked OpenFOAM utility — measured:
    # `pytest tests/engine/ tests/test_agent_jobs.py` failed with a path under
    # failed with a path under `/tmp/pytest-of-...`, which is not an install at all.
    #
    # The install root comes from the captured environment, so this also survives the
    # install moving.
    root = Path(get_env().env["WM_PROJECT_DIR"])
    strays = [
        line for line in completed.stdout.decode(errors="replace").splitlines() if str(root) in line
    ]
    assert not strays, "a real utility was left running:\n" + "\n".join(strays)


@pytest.mark.skipif(sys.platform == "win32", reason="the capture needs bash")
def test_the_environment_script_is_the_one_named() -> None:
    """`STOKES_OPENFOAM_BASHRC` is what the record sourced, when it is set.

    It is the dispatcher's seam: this host's environment script is not called ``bashrc``, so
    the install would not be found at all.
    """
    import os

    named = os.environ.get("STOKES_OPENFOAM_BASHRC")
    if not named:
        pytest.skip("STOKES_OPENFOAM_BASHRC is not set")
    record = host_record(refresh=True)
    assert record["default_install"] == named, (
        f"the record defaulted to {record['default_install']}, not the named {named}"
    )
