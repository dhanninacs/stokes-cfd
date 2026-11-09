"""The SSH runner and the host registry's view of `hosts.json` (T-159a).

**What is testable without a host, and what is not.** The transport itself needs a real sshd
(that is `tests/engine/test_ssh_runner_engine.py`); here the questions are about *routing* -
which runner a name resolves to, what an unknown name says, and that closing a runner does
**not** stop the remote agent. Those are our decisions rather than ssh's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.run.errors import RunnerUnavailable, UnknownHost
from stokes.run.hosts import HostRegistry
from stokes.run.local import LocalRunner
from stokes.run.ssh_hosts import SshHost, write_hosts


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", name="p")
    return made


def a_record(name: str = "buildbox") -> SshHost:
    return SshHost(
        name=name,
        host="10.0.0.5",
        user="chandra",
        port=22,
        key="/keys/id_ed25519",
        python="python3",
        added="2026-10-15",
    )


def test_local_is_always_available_and_needs_no_file(session: Session) -> None:
    """**The local runner is unchanged by any of this**, which is what the existing tests pin."""
    registry = HostRegistry.of(session)
    assert "local" in registry.host_names()
    assert isinstance(registry.runner("local"), LocalRunner)


def test_a_name_in_the_file_resolves_to_an_ssh_runner(session: Session, tmp_path: Path) -> None:
    """**The routing decision**: `local` is built in, everything else comes from the file."""
    from stokes.run.ssh_runner import SshRunner

    write_hosts(
        [a_record()],
        Path(session.__dict__["_stokes_home"]) / "hosts.json"
        if "_stokes_home" in session.__dict__
        else None,
    )
    registry = HostRegistry.of(session)
    assert "buildbox" in registry.host_names()
    runner = registry.runner("buildbox")
    assert isinstance(runner, SshRunner)
    assert runner.name == "buildbox"
    # The same name gives the same runner, so a host is not re-attached per call.
    assert registry.runner("buildbox") is runner


def test_an_unknown_name_lists_what_is_known(session: Session) -> None:
    """A refusal that lists the alternatives is one a user can act on."""
    registry = HostRegistry.of(session)
    with pytest.raises(UnknownHost) as caught:
        registry.runner("nope")
    assert caught.value.code == "run.unknown_host"
    assert "local" in str(caught.value)


def test_a_broken_host_file_does_not_stop_a_session(session: Session, tmp_path: Path) -> None:
    """**The failure mode that matters**: a session whose host list is unreadable still opens its
    project and mesh LOCALLY. So the error becomes a log line, not an exception out of a listing."""
    from stokes.run.ssh_hosts import hosts_path

    hosts_path().parent.mkdir(parents=True, exist_ok=True)
    hosts_path().write_text("{not json")
    registry = HostRegistry.of(session)
    names = registry.host_names()
    assert "local" in names, "a broken file must leave `local` usable"
    assert all(name == "local" or name.startswith("wsl:") for name in names), "and no SSH host"
    assert isinstance(registry.runner("local"), LocalRunner)


def test_refresh_hosts_replaces_a_runner_whose_record_changed(
    session: Session, tmp_path: Path
) -> None:
    """**A record can change under a name**: a different user, key or port for the same `buildbox`
    must not keep talking through a connection built from the previous record."""
    from stokes.run.ssh_hosts import hosts_path

    write_hosts([a_record()], hosts_path())
    registry = HostRegistry.of(session)
    first = registry.runner("buildbox")

    from dataclasses import replace

    write_hosts([replace(a_record(), user="someone-else")], hosts_path())
    assert registry.runner("buildbox") is first, "the cache should hold until asked to refresh"
    registry.refresh_hosts()
    second = registry.runner("buildbox")
    assert second is not first, "a changed record kept the old connection"
    assert second.host_record.user == "someone-else"


def test_an_ssh_runner_that_cannot_start_reports_why_and_stays_unavailable(
    session: Session, tmp_path: Path
) -> None:
    """**A host that cannot be reached is a normal answer, not a crash.** `available`/`why` are
    what the UI reads, and the reason has to survive so a redraw does not retry a refused one."""
    # **A key that does not exist**, so the first call fails - and the message is kept rather than
    # retried on every call. The address is 192.0.2.1, which is TEST-NET-1 and never routable, so
    # this does not depend on the machine having an sshd at all.
    from dataclasses import replace

    from stokes.run.ssh_runner import SshRunner

    record = replace(a_record("nowhere"), host="192.0.2.1", key="/nonexistent/key")
    runner = SshRunner(record)
    assert runner.available is True, "before any call, a host is not yet known to be bad"
    with pytest.raises(RunnerUnavailable):
        runner.call("host.probe", timeout=5.0)
    assert runner.available is False
    assert "nowhere" in runner.why
    # And a second call refuses at once rather than attempting ssh again.
    with pytest.raises(RunnerUnavailable):
        runner.call("host.probe", timeout=5.0)
