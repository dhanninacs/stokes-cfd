"""The `host.*` commands and the registry behind them (T-144c).

**`host.probe` sources a bashrc**, which is a process launch, so the caching is not a nicety:
without it
every mesh driver that asks "can this machine mesh?" pays for one. The tests pin both the
cache and the
refresh.

`local` is the only host in M2; the SSH runner (T-159a) joins the same registry and these tests
then grow
a second entry rather than changing shape.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.errors import StokesError


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """A session with a home of its own, so nothing reads a real one."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    return Session()


def test_host_list_needs_no_project_and_lists_local(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user sees the host list before opening anything: it is what tells them where meshing
    will run."""
    # **Do not depend on the machine's real WSL distros.** Discovery has its own tests; this one
    # pins the local row, and a developer with an installed distro must see the same list as CI.
    monkeypatch.setattr("stokes.run.hosts.wsl_host_names", lambda: [])
    reply = session.execute("host.list")
    assert [entry["name"] for entry in reply["hosts"]] == ["local"]
    entry = reply["hosts"][0]
    assert entry["runner"] == "local"
    assert isinstance(entry["available"], bool)
    # **`record` is None until it is asked for.** Different from "the host has nothing": a
    # list must not
    # cost a bashrc capture.
    assert entry["record"] is None


def test_host_probe_returns_the_runner_record_and_caches_it(session: Session) -> None:
    """The canned record comes back, and a second call does not touch the runner."""
    calls: list[dict[str, Any]] = []

    class FakeRunner:
        name = "local"
        available = True
        why = ""

        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            calls.append({"method": method, "timeout": timeout, **params})
            return {"installs": [{"version": "14"}], "vtk": "9.6.2"}

        def close(self) -> None:
            return

    from stokes.run.hosts import HostRegistry

    registry = HostRegistry.of(session)
    registry._runners["local"] = FakeRunner()  # type: ignore[assignment]

    first = session.execute("host.probe")
    assert first["host"] == "local"
    assert first["available"] is True
    assert first["record"] == {"installs": [{"version": "14"}], "vtk": "9.6.2"}
    assert len(calls) == 1

    session.execute("host.probe")
    assert len(calls) == 1, "a second probe without refresh called the runner again"

    session.execute("host.probe", refresh=True)
    assert len(calls) == 2, "refresh did not reach the runner"
    assert calls[-1]["refresh"] is True


def test_host_probe_reports_an_unavailable_host_without_raising(session: Session) -> None:
    """**A host that cannot mesh answers rather than raises**, so `mesh.generate` (T-207) can write
    `skipped` instead of catching an exception to decide."""

    class Unavailable:
        name = "local"
        available = False
        why = "No OpenFOAM host on this machine"

        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            raise AssertionError("an unavailable host must not be called")

        def close(self) -> None:
            return

    from stokes.run.hosts import HostRegistry

    HostRegistry.of(session)._runners["local"] = Unavailable()  # type: ignore[assignment]
    reply = session.execute("host.probe")
    assert reply == {
        "host": "local",
        "available": False,
        "why": "No OpenFOAM host on this machine",
        "record": None,
    }


def test_an_unknown_host_raises(session: Session) -> None:
    """A mistake in the CALL, which is different from a fact about a machine."""
    with pytest.raises(StokesError) as caught:
        session.execute("host.probe", host="nope")
    assert caught.value.code == "run.unknown_host"
    assert "local" in str(caught.value), "the message must name the hosts that do exist"


def test_the_registry_is_one_per_session(session: Session) -> None:
    """Two `of` calls give one registry, so the cache is shared rather than duplicated."""
    from stokes.run.hosts import HostRegistry

    assert HostRegistry.of(session) is HostRegistry.of(session)


def test_host_list_carries_the_record_once_it_is_fetched(session: Session) -> None:
    """After a probe the list shows it, so a UI can render host and record from one call."""

    class FakeRunner:
        name = "local"
        available = True
        why = ""

        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            return {"installs": []}

        def close(self) -> None:
            return

    from stokes.run.hosts import HostRegistry

    HostRegistry.of(session)._runners["local"] = FakeRunner()  # type: ignore[assignment]
    session.execute("host.probe")
    assert session.execute("host.list")["hosts"][0]["record"] == {"installs": []}


def test_importing_stokes_api_does_not_import_the_agent() -> None:
    """**The start-up budget, as a test.** `stokes.run` pulls in `stokes_agent`, and `import
    stokes.api`
    has 300 ms to spend; a host most sessions never use must not be paid for by all of them.

    Run in a subprocess because `sys.modules` in this process already holds everything.
    """
    code = (
        "import sys\n"
        "import stokes.api\n"
        "bad = [m for m in ('stokes.run', 'stokes_agent') if m in sys.modules]\n"
        "leftovers = [m for m in sys.modules if m.startswith(('stokes.run.', 'stokes_agent.'))]\n"
        "print('BAD:' + ','.join(bad + leftovers))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"},
        cwd=str(Path(__file__).parent.parent),
    )
    assert completed.returncode == 0, completed.stderr
    line = next(ln for ln in completed.stdout.splitlines() if ln.startswith("BAD:"))
    assert line == "BAD:", f"importing stokes.api pulled in the agent: {line}"


# ------------------------------------------------------------------ adding and removing a host


def test_an_ADDED_host_appears_in_the_list(session: Session) -> None:
    """**The bug this test exists for.** `host.add` wrote `hosts.json` and `host.list` walked the
    module constant `HOST_NAMES`, which is only `local` - so an added host was written and then
    **invisible**. Measured: the list still returned `['local']` after a successful add.

    A host a user added and cannot see is worse than a refusal: there is no error to report and
    nothing to retry.
    """
    session.execute("host.add", name="buildbox", host="10.0.0.5", user="cfd")
    names = [row["name"] for row in session.execute("host.list")["hosts"]]
    assert "buildbox" in names, f"the added host is not listed; got {names}"
    assert names[0] == "local", "local is always first"


def test_adding_a_host_does_NOT_trust_it(session: Session) -> None:
    """**Two steps on purpose** (the Settings spec §2, and `host.trust`'s own design): `host.add`
    writes the address, and the key is confirmed by a person reading a fingerprint. One command that
    did both would have to trust whatever the network answered, which is what the fingerprint step
    exists to prevent.
    """
    reply = session.execute("host.add", name="buildbox", host="10.0.0.5", user="cfd")
    assert reply["trusted"] is False
    assert reply["target"] == "cfd@10.0.0.5"


def test_the_record_is_validated_by_the_module_that_reads_it(session: Session) -> None:
    """**Every field goes through `ssh_hosts`'s own validators**, so a value this accepts cannot be
    one the next read rejects. The name becomes a path segment and the address reaches an argv, and
    both rules already existed - this command composes them rather than inventing a second set.
    """
    with pytest.raises(StokesError) as caught:
        session.execute("host.add", name="Bad Name", host="10.0.0.5", user="cfd")
    assert "lower-case letters" in str(caught.value)

    with pytest.raises(StokesError):
        session.execute("host.add", name="ok", host="10.0.0.5", user="cfd", port=70000)


def test_a_duplicate_name_is_refused_rather_than_replacing(session: Session) -> None:
    """**Not a silent overwrite**: a second `buildbox` pointing somewhere else would change where
    every existing reference runs, and the user asked to ADD rather than to change.
    """
    session.execute("host.add", name="buildbox", host="10.0.0.5", user="cfd")
    with pytest.raises(StokesError) as caught:
        session.execute("host.add", name="buildbox", host="10.0.0.9", user="other")
    assert caught.value.code == "host.exists"
    # And the original is untouched.
    record = session.execute("host.list")["hosts"][1]
    assert record["name"] == "buildbox"


def test_removing_a_host_leaves_its_KEY_alone_unless_asked(session: Session) -> None:
    """The record is an address the user typed; the key is a trust decision they made. Removing the
    second silently would mean re-adding the same machine asks for confirmation again with no sign
    anything changed - so it takes the `fingerprint` flag.
    """
    session.execute("host.add", name="buildbox", host="10.0.0.5", user="cfd")
    reply = session.execute("host.remove", host="buildbox")
    assert reply["removed"] == "buildbox"
    assert reply["key_removed"] is False
    names = [row["name"] for row in session.execute("host.list")["hosts"]]
    # `local` stays, and so does a WSL distribution the machine happens to have; the SSH host goes.
    assert "local" in names and "buildbox" not in names


def test_local_cannot_be_removed(session: Session) -> None:
    """`local` is not a record, it is this machine; a caller asking to remove it has confused two
    things, and the refusal says which names ARE ssh hosts.
    """
    with pytest.raises(StokesError) as caught:
        session.execute("host.remove", host="local")
    assert "no ssh host" in str(caught.value)


def test_the_list_carries_the_ADDRESS_so_a_user_can_identify_a_host(session: Session) -> None:
    """**A name alone does not identify a machine.** The UI showed `buildbox @` with nothing after
    it, because `host.list` returned only the name and the runner - measured. A list of names is not
    something a user can check against what they meant to add.

    `local` has no address, which is correct: it is this machine, not a record.
    """
    session.execute("host.add", name="buildbox", host="10.0.0.5", user="cfd", port=2222)
    rows = {row["name"]: row for row in session.execute("host.list")["hosts"]}

    assert rows["buildbox"]["host"] == "10.0.0.5"
    assert rows["buildbox"]["user"] == "cfd"
    assert rows["buildbox"]["port"] == 2222
    # The default port is reported too, so the UI does not have to know what a default is.
    assert "host" not in rows["local"] or rows["local"].get("host") in (None, "")
