"""WSL as a host: discovered, and **runnable** (the Settings spec §5, ADR-0024 §3.4).

The spec's rule is a shape, not a wording: *"Show a WSL row as runnable only when the engine test
for it passes; until then show 'detected' with the reason, never a button that fails."* Step (a)
was detection; this file now covers step (b), the runner.

**The lifecycle is tested against a real agent, not a fake protocol.** A fake `wsl.exe` would prove
that the argv is assembled and prove nothing about the part that breaks: reading the record from a
pipe, connecting to the loopback port it names, shutting the child down. The injected `spawn` runs
`python -m stokes_agent --serve` locally, which is the same program the distro runs, so the test
needs no WSL and still crosses a frame, a token and a process boundary.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from stokes.run import hosts as hosts_module
from stokes.run.errors import RunnerUnavailable
from stokes.run.hosts import WSL_PREFIX, distro_of, wsl_host_names
from stokes.run.wsl_runner import (
    DISTRO_PATTERN,
    NO_WSL_WHY,
    WslRunner,
    _to_wsl_path,
)


def test_a_distribution_is_named_with_its_PREFIX() -> None:
    """The prefix is what makes a WSL row distinguishable from an SSH host a user typed, which the
    spec lists as different kinds.
    """
    assert WSL_PREFIX == "wsl:"
    assert distro_of("wsl:Ubuntu") == "Ubuntu"
    # Anything else is not a distribution, including a name that merely starts the same way.
    assert distro_of("local") is None
    assert distro_of("buildbox") is None
    assert distro_of("wslx") is None


def test_a_distribution_name_may_contain_capitals_and_spaces() -> None:
    """**The SSH host-name rule rejects a real distribution.** `Ubuntu-24.04` is mixed case, so the
    old lowercase pattern would have made every WSL host unusable from the settings forms.
    """
    assert DISTRO_PATTERN.fullmatch("Ubuntu-24.04")
    assert DISTRO_PATTERN.fullmatch("Ubuntu 24.04")
    assert DISTRO_PATTERN.fullmatch("docker-desktop")
    # A leading dash would be read by `wsl.exe` as a flag, not as the value of `-d`.
    assert DISTRO_PATTERN.fullmatch("-Ubuntu") is None
    assert DISTRO_PATTERN.fullmatch("") is None


def test_the_mesh_and_solver_host_fields_accept_the_wsl_form() -> None:
    """One pattern for both forms, so a host that Compute offers is a host a user can type.

    A mesh on WSL is selected by the project's `host` setting (the Compute default chooses the
    install, and the result records the host that ran), so this pattern is the gate between the
    host list and a run.
    """
    from stokes.engines.model import solver_specs
    from stokes.hostnames import HOST_NAME_PATTERN
    from stokes.mesh.model import mesh_specs

    pattern = re.compile(f"^{HOST_NAME_PATTERN}$")
    for name in ("local", "buildbox", "wsl:Ubuntu-24.04", "wsl:Ubuntu 24.04"):
        assert pattern.fullmatch(name), name
    # **Empty is the Compute default** (ADR-0024): a project that does not name a host uses the
    # one chosen in Settings, so the field has to accept the empty value the model defaults to.
    assert pattern.fullmatch("")
    for name in ("wsl:-bad", "Upper Case"):
        assert pattern.fullmatch(name) is None, name

    mesh = next(spec for spec in mesh_specs() if spec.type == "mesh_settings")
    mesh_host = mesh.param("host")
    assert mesh_host.default == ""
    assert re.fullmatch(f"^{mesh_host.pattern}$", "wsl:Ubuntu-24.04")
    assert re.fullmatch(f"^{mesh_host.pattern}$", "")

    solver = next(spec for spec in solver_specs() if spec.type == "solver_settings")
    solver_host = solver.param("host")
    assert solver_host.default == ""
    assert re.fullmatch(f"^{solver_host.pattern}$", "wsl:Ubuntu-24.04")
    assert re.fullmatch(f"^{solver_host.pattern}$", "")


@pytest.mark.skipif(os.name != "nt", reason="a /mnt path only exists for a Windows host")
def test_a_windows_path_becomes_a_mnt_path() -> None:
    assert _to_wsl_path(Path(r"D:\WSL\OF14_windows\agent")) == "/mnt/d/WSL/OF14_windows/agent"
    # A path with a space is not split, because `env` receives it as one argv.
    assert _to_wsl_path(Path(r"C:\Data\a b\stokes")) == "/mnt/c/Data/a b/stokes"
    with pytest.raises(RunnerUnavailable):
        _to_wsl_path(Path(r"\\server\share\agent"))


def test_the_command_is_wsl_dash_d_env_python() -> None:
    """**The record is read from the pipe, so the token is not an argument.** `env` is used rather
    than a shell, so a path with a space stays one value.
    """
    runner = WslRunner("Ubuntu-24.04", wsl=Path("wsl.exe"), pythonpath="/mnt/d/agent:/mnt/d/app")
    assert runner.command() == [
        "wsl.exe",
        "-d",
        "Ubuntu-24.04",
        "--",
        "env",
        "PYTHONPATH=/mnt/d/agent:/mnt/d/app",
        "python3",
        "-m",
        "stokes_agent",
        "--serve",
    ]
    # No PYTHONPATH when the distro has the agent installed: less to get wrong, and no Windows
    # directory in a Linux import path.
    plain = WslRunner("Ubuntu-24.04", wsl=Path("wsl.exe"), pythonpath="")
    assert plain.command() == [
        "wsl.exe",
        "-d",
        "Ubuntu-24.04",
        "--",
        "python3",
        "-m",
        "stokes_agent",
        "--serve",
    ]


def test_off_windows_the_host_list_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wsl_host_names()` is empty when there is no WSL, so the list gains nothing - which is what
    keeps `local`-only behaviour on this machine unchanged.
    """
    monkeypatch.setattr("stokes_agent.probe.wsl_distros", lambda: [])
    assert wsl_host_names() == []


def test_a_discovered_distribution_appears_in_the_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Discovered, not stored.** A distribution needs no `hosts.json` entry: a user who installs a
    new one sees it after a Rescan, and one who uninstalls it stops seeing it, with no file to edit.
    """
    monkeypatch.setattr("stokes_agent.probe.wsl_distros", lambda: ["Ubuntu", "Debian"])
    assert wsl_host_names() == ["wsl:Ubuntu", "wsl:Debian"]


class _FakeWslChild:
    """What `Popen` hands back in the distro-list tests: answers, or never does."""

    def __init__(self, stdout: bytes | None) -> None:
        self.stdout = stdout
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self.stdout is None:
            raise subprocess.TimeoutExpired(["wsl.exe"], timeout or 0)
        return self.stdout, b""

    def kill(self) -> None:
        self.killed = True


def _windows_with_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stokes_agent.probe.platform.system", lambda: "Windows")
    monkeypatch.setattr("stokes_agent.probe.shutil.which", lambda name: r"C:\Windows\wsl.exe")


def test_the_distro_list_is_asked_WITHOUT_a_console_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The start-screen-on-connect report, measured 2026-11-01.** `wsl.exe -l -q` with the
    server's console on its stdin did not exit until the timeout killed it (and the kill then waited
    on the pipe readers); with `DEVNULL` it answered in 0.2 s. `host.list` runs this on the event
    loop, so a hang here holds `node.tree` and `project.info` behind it and the window shows the
    Start screen although a project is open. The call shape is what the test pins.
    """
    from stokes_agent import probe

    _windows_with_wsl(monkeypatch)
    seen: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> _FakeWslChild:
        seen.update(kwargs, argv=argv)
        return _FakeWslChild("Ubuntu-24.04\r\ndocker-desktop\r\n".encode("utf-16-le"))

    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)
    assert probe.wsl_distros() == ["Ubuntu-24.04", "docker-desktop"]
    assert seen["argv"] == ["wsl.exe", "-l", "-q"]
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["stdout"] is subprocess.PIPE


def test_a_distro_list_that_does_not_answer_is_empty_and_the_child_is_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `wsl.exe` that hangs costs the timeout and nothing more: no second wait on its pipes
    (which is what `subprocess.run` does on Windows after a timeout), and an empty list rather
    than an exception out of a host listing.
    """
    from stokes_agent import probe

    _windows_with_wsl(monkeypatch)
    child = _FakeWslChild(None)
    monkeypatch.setattr(probe.subprocess, "Popen", lambda argv, **kwargs: child)
    assert probe.wsl_distros() == []
    assert child.killed is True


def test_the_registry_asks_for_the_distributions_ONCE(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking runs `wsl.exe`, and `host.list` is asked on every connect and project open: the
    answer is kept per registry, and `refresh_hosts` (the settings' Refresh) is what asks again.
    """
    calls = 0

    def counted() -> list[str]:
        nonlocal calls
        calls += 1
        return ["wsl:Ubuntu"]

    monkeypatch.setattr(hosts_module, "wsl_host_names", counted)

    class FakeSession:
        extensions: dict[str, object] = {}

    registry = hosts_module.HostRegistry.of(FakeSession())  # type: ignore[arg-type]
    assert "wsl:Ubuntu" in registry.host_names()
    assert "wsl:Ubuntu" in registry.host_names()
    assert calls == 1
    registry.refresh_hosts()
    assert "wsl:Ubuntu" in registry.host_names()
    assert calls == 2
    registry.close()


def test_a_runner_without_wsl_explains_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off Windows, or with no `wsl.exe`, the runner is unavailable with a reason - not an empty
    host that fails later.
    """
    monkeypatch.setattr("stokes.run.wsl_runner._find_wsl", lambda: None)
    runner = WslRunner("Ubuntu")
    assert runner.available is False
    assert runner.why == NO_WSL_WHY
    with pytest.raises(RunnerUnavailable):
        WslRunner("-not-a-distro")


def test_the_registry_ROUTES_a_wsl_name_to_the_wsl_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this the list would raise `UnknownHost` for a name it had just offered - the same
    shape of bug as `host.list` walking the wrong constant.
    """
    monkeypatch.setattr(hosts_module, "wsl_host_names", lambda: ["wsl:Ubuntu"])
    monkeypatch.setattr("stokes.run.wsl_runner._find_wsl", lambda: Path(sys.executable))

    class FakeSession:
        extensions: dict[str, object] = {}

    registry = hosts_module.HostRegistry.of(FakeSession())  # type: ignore[arg-type]
    runner = registry.runner("wsl:Ubuntu")
    assert isinstance(runner, WslRunner)
    assert runner.available is True
    entry = next(row for row in registry.hosts() if row["name"] == "wsl:Ubuntu")
    assert entry["available"] is True
    assert entry["why"] == ""
    registry.close()


def test_a_failed_start_is_an_ANSWER_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WSL row that was available when listed but whose agent cannot start must come back
    `available: false` with the reason, so the row can say what to fix and `mesh.generate` can
    write `skipped` instead of raising through the host list.
    """

    class FailingRunner:
        name = "wsl:Ubuntu"
        available = True
        why = ""

        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            raise RunnerUnavailable("the agent in Ubuntu could not start: no module named msgpack")

        def close(self) -> None:
            pass

    class FakeSession:
        extensions: dict[str, object] = {}

    registry = hosts_module.HostRegistry.of(FakeSession())  # type: ignore[arg-type]
    monkeypatch.setattr(registry, "runner", lambda name: FailingRunner())
    answer = registry.probe("wsl:Ubuntu")
    assert answer["available"] is False
    assert "msgpack" in answer["why"]
    assert answer["record"] is None


def test_the_lifecycle_starts_attaches_and_stops_a_real_agent() -> None:
    """Spawn, read the record from the pipe, attach, call, and shut the child down.

    The token never appears in the argv (checked by the command test); here it crosses the pipe
    and the handshake, which is the part a mock would skip.
    """
    started: list[asyncio.subprocess.Process] = []

    async def spawn(command: list[str]) -> asyncio.subprocess.Process:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "stokes_agent",
            "--serve",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        started.append(proc)
        return proc

    runner = WslRunner("Test", wsl=Path(sys.executable), spawn=spawn)
    try:
        assert runner.available is True
        assert runner.call("ping", timeout=20) == {"pong": True}
        info = runner.call("agent.info", timeout=20)
        assert "agent.shutdown" in info["methods"]
    finally:
        runner.close()

    assert started, "the runner never spawned the agent"
    assert started[0].returncode is not None, "close left the agent process running"


def test_a_start_that_dies_reports_what_it_said() -> None:
    """**The useful message is on stderr.** A distro missing a module must come back with that
    module's name, not "the agent did not start".
    """

    async def spawn(command: list[str]) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('no module named msgpack\\n'); sys.exit(3)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    runner = WslRunner("Test", wsl=Path(sys.executable), spawn=spawn)
    with pytest.raises(RunnerUnavailable) as raised:
        runner.call("ping", timeout=20)
    assert "exited before printing" in str(raised.value)
    assert "msgpack" in str(raised.value)
    assert runner.available is False
    assert "msgpack" in runner.why
    runner.close()
