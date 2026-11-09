"""The OpenFOAM environment capture and the host record (T-140, ADR-0024).

**No OpenFOAM needed**: every test here builds its own bashrc in `tmp_path`, so it runs on the
laptop and
in CI. The one test that needs the real install is `tests/engine/test_host_probe.py`, marked
`engine`.

The interesting cases are the hostile ones — a path with a quote in it, a bashrc that hangs, a
bashrc that
is not one — because the capture runs a shell command whose arguments come from the filesystem.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from stokes_agent import engine_env
from stokes_agent.client import AgentClient
from stokes_agent.engine_env import (
    EngineEnvError,
    capture_env,
    flavour_of,
    host_record,
    supports,
    utilities,
)
from stokes_agent.server import AgentServer

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="the capture needs bash")


def _bashrc(tmp_path: Path, body: str, name: str = "bashrc") -> Path:
    """A bashrc that exports what ``body`` says, in a directory of its own."""
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body)
    return path


def _exports(tmp_path: Path, extra: str = "", name: str = "bashrc") -> Path:
    """A minimal bashrc: the three variables the capture insists on, plus anything extra."""
    return _bashrc(
        tmp_path,
        f"export WM_PROJECT=OpenFOAM\nexport WM_PROJECT_VERSION=14\n"
        f"export WM_PROJECT_DIR={tmp_path}\n{extra}",
        name=name,
    )


@POSIX_ONLY
def test_capture_keeps_the_filtered_keys_and_drops_the_rest(tmp_path: Path) -> None:
    """**What the filter is for.** A bashrc exports far more than the utilities need, and the
    agent must not carry a caller's secrets into a subprocess. Measured on a real sourcing run."""
    path = _exports(tmp_path, "export FOAM_X=1\nexport SECRET=1\nexport PATH=$PATH\n")
    env = capture_env(path)
    assert env.version == "14"
    assert env.flavour == "org"
    assert env.project == "OpenFOAM"
    assert env.env["FOAM_X"] == "1", "a FOAM_ prefixed key is kept"
    assert "SECRET" not in env.env, "an unrelated key must not survive the filter"
    assert env.env["WM_PROJECT_DIR"] == str(tmp_path)
    assert env.seconds > 0


@POSIX_ONLY
def test_a_path_with_a_space_and_a_quote_still_works(tmp_path: Path) -> None:
    """**The reason the path travels as an argument.** The sourcing script is a constant, so
    no punctuation in the path can change what runs. A path with a space and a quote is the
    case a script built by interpolation gets wrong."""
    folder = tmp_path / 'a dir with "quotes" and spaces'
    folder.mkdir()
    path = folder / "bashrc"
    # **The path is single-quoted inside the bashrc**, which is what a real one does and what lets a
    # path with a space survive `export`. Unquoted, bash splits at the first space and the value is
    # truncated — measured, and it is what the first version of this test did.
    #
    # Single quotes and not double: **bash strips the quoting characters from the value**, so
    # `export X="a b"` gives `a b` and `export X='a "b"'` gives `a "b"` — the second is the only
    # spelling
    # that preserves a literal `"` in the path, which is the case being tested. A test that quotes
    # with
    # `"` measures bash's own quoting rules rather than the capture's argument passing.
    path.write_text(
        f"export WM_PROJECT=OpenFOAM\nexport WM_PROJECT_VERSION=14\n"
        f"export WM_PROJECT_DIR='{folder}'\n"
    )
    env = capture_env(path)
    assert env.bashrc == str(path)
    assert env.env["WM_PROJECT_DIR"] == str(folder)


@POSIX_ONLY
def test_a_bad_bashrc_path_is_refused_before_anything_runs(tmp_path: Path) -> None:
    """Three shapes refused with a message naming the path: relative, a directory, absent."""
    with pytest.raises(EngineEnvError, match="absolute"):
        capture_env(Path("bashrc"))
    folder = tmp_path / "adir"
    folder.mkdir()
    with pytest.raises(EngineEnvError, match="directory"):
        capture_env(folder)
    with pytest.raises(EngineEnvError):
        capture_env(tmp_path / "nope" / "bashrc")


@POSIX_ONLY
def test_a_bashrc_that_hangs_times_out(tmp_path: Path) -> None:
    """A bashrc is arbitrary shell. One that blocks must not block the agent: the timeout is the
    only thing between a bad install and a hung probe."""
    path = _bad_bashrc(tmp_path, "sleep 5\n")
    with pytest.raises(EngineEnvError, match="timed out"):
        capture_env(path, timeout=0.5)


def _bad_bashrc(tmp_path: Path, body: str) -> Path:
    """A bashrc whose body is ``body`` — a separate helper so the exports above stay readable."""
    folder = tmp_path / "slow"
    folder.mkdir(exist_ok=True)
    path = folder / "bashrc"
    path.write_text(body)
    return path


@POSIX_ONLY
def test_a_bashrc_that_is_not_openfoam_is_refused(tmp_path: Path) -> None:
    """**`WM_PROJECT_DIR` is the test, not the exit code.** A bashrc that exits 0 having exported
    nothing would otherwise yield an install with an empty PATH, and every later call would then
    fail one at a time."""
    path = _bad_bashrc(tmp_path, "export SOMETHING=1\n")
    with pytest.raises(EngineEnvError, match="WM_PROJECT_DIR"):
        capture_env(path)


def test_the_flavour_rule() -> None:
    """Foundation is all digits or ``dev``; ESI's carry a leading ``v``; else unknown."""
    assert flavour_of("14") == "org"
    assert flavour_of("dev") == "org"
    assert flavour_of("v2512") == "com"
    assert flavour_of("foo") == "unknown"
    assert flavour_of("") == "unknown"
    # A `v` with no digits is not ESI's scheme, so it is not guessed at.
    assert flavour_of("vNext") == "unknown"
    # And support is narrow on purpose: Foundation 14 only (ADR-0024).
    assert supports("org", "14")
    assert not supports("org", "13")
    assert not supports("com", "v2512")
    assert not supports("unknown", "14")


def test_a_host_with_no_install_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """`installs: []` with a non-empty `why`. An empty list alone leaves a client guessing."""
    monkeypatch.setattr(engine_env, "bashrc_candidates", list)
    record = host_record(refresh=True)
    assert record["installs"] == []
    assert record["default_install"] is None
    assert record["why"], "a host with no engine must say why, not just be empty"
    assert "OpenFOAM" in record["why"]


@POSIX_ONLY
def test_an_install_whose_capture_fails_is_listed_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A bad install never fails the whole record.** It is listed with `supported: false` and the
    reason, because "found something I cannot use" and "found nothing" are different answers."""
    broken = _bad_bashrc(tmp_path, "sleep 30\n")
    monkeypatch.setattr(engine_env, "bashrc_candidates", lambda: [broken])
    monkeypatch.setattr(engine_env, "capture_env", lambda *_a, **_k: _raise_timeout())
    record = host_record(refresh=True)
    assert len(record["installs"]) == 1
    entry = record["installs"][0]
    assert entry["supported"] is False
    assert entry["why"], "the entry must carry the reason"
    assert entry["utilities"] == {}
    assert record["default_install"] is None


def _raise_timeout() -> Any:
    raise EngineEnvError("sourcing timed out")


@POSIX_ONLY
def test_an_unsupported_install_explains_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ESI install is found, listed, and explained — not silently dropped."""
    esi = _bashrc(
        tmp_path,
        f"export WM_PROJECT=OpenFOAM\nexport WM_PROJECT_VERSION=v2512\n"
        f"export WM_PROJECT_DIR={tmp_path}\n",
    )
    monkeypatch.setattr(engine_env, "bashrc_candidates", lambda: [esi])
    record = host_record(refresh=True)
    entry = record["installs"][0]
    assert entry["flavour"] == "com" and entry["supported"] is False
    assert "Foundation 14" in entry["why"]
    assert record["default_install"] is None


@POSIX_ONLY
def test_get_env_refuses_a_host_with_no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent.no_engine` is a code a caller can branch on, not a string to match."""
    monkeypatch.setattr(engine_env, "bashrc_candidates", list)
    engine_env.clear_cache()
    with pytest.raises(EngineEnvError) as caught:
        engine_env.get_env()
    assert caught.value.details.get("code") == "agent.no_engine"


@POSIX_ONLY
def test_utilities_asks_the_captured_path_not_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The captured PATH is the only one that matters.** A utility reachable here but not
    through the capture is not reachable by the agent, and the record must say so."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "blockMesh"
    tool.write_text("#!/bin/sh\necho hi\n")
    tool.chmod(0o755)
    # **APPEND, as every real bashrc does.** The OpenFOAM one is `export PATH=$OFDEP/usr/bin:$PATH`.
    # Replacing PATH outright breaks the capture itself, because `env` is then not found and bash
    # exits
    # 127 — measured, and it is what the first version of this test did. A test whose fixture no
    # real
    # install resembles proves nothing about the code.
    path = _exports(tmp_path, f"export PATH={bin_dir}:$PATH\n")
    env = capture_env(path)
    found = utilities(env)
    assert found["blockMesh"] == str(tool)
    # Everything else in UTILITIES is absent from this fake PATH, and reported as None rather than
    # omitted — a caller must be able to tell "absent" from "not asked about".
    assert found["checkMesh"] is None
    assert set(found) == set(engine_env.UTILITIES)


@POSIX_ONLY
def test_the_capture_is_cached_and_refresh_re_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sourcing is a process launch; once per bashrc, and `refresh` redoes it."""
    path = _exports(tmp_path)
    calls: list[Path] = []
    real = engine_env.capture_env

    def counting(target: Path, timeout: float = 30.0) -> Any:
        calls.append(target)
        return real(target, timeout)

    monkeypatch.setattr(engine_env, "capture_env", counting)
    engine_env.clear_cache()
    first = engine_env.cached_env(str(path))
    second = engine_env.cached_env(str(path))
    assert first is second, "the same bashrc must not be sourced twice"
    assert len(calls) == 1
    engine_env.clear_cache()
    engine_env.cached_env(str(path))
    assert len(calls) == 2, "a cleared cache must source again"


@POSIX_ONLY
def test_the_agent_answers_ping_while_the_host_probe_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The event-loop test, and it took four attempts to write one that can fail.**

    A synchronous slow handler on the event loop blocks every other request, including a health
    check.
    `host.probe` is registered as an `async def` that hands the work to a thread, so a `ping` must
    be
    answered while it runs.

    **Why the earlier versions could not fail, measured rather than guessed.** Each waited for the
    handler to start by watching a flag, then timed a ping:

        DISPATCH host.probe at +0.00
        handler sleeping; now sending ping at +5.00     <- the wait consumed the whole sleep
        DISPATCH ping at +5.00

    The wait itself was the problem: with a *synchronous* handler the loop is blocked, so the
    `await asyncio.sleep()` in the polling loop cannot run until the handler finishes — the flag is
    seen only after the block, and the ping is then timed against an idle loop. The test passed with
    the
    blocking handler in place, four times.

    **The fix is to send the ping from a timer instead of after a wait.** `call_later` schedules it
    on
    the loop; if the loop is blocked the callback runs late, and that lateness IS the measurement.
    No
    flag, no polling, nothing that can be overtaken by the thing being measured.
    """
    monkeypatch.setattr(engine_env, "bashrc_candidates", list)
    # Long enough that a blocked loop is unmistakable, short enough for a test.
    hold = 3.0
    # The ping is fired this long after the probe is sent, well inside `hold`.
    fire_at = 0.5

    async def scenario() -> None:
        server = AgentServer()

        def slow(refresh: bool = False) -> dict[str, Any]:
            time.sleep(hold)
            return {"slow": True}

        monkeypatch.setattr(engine_env, "host_record", slow)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)

        started = time.monotonic()
        probe = asyncio.create_task(client.call("host.probe"))
        # **Schedule the ping, do not poll for it.** `asyncio.get_running_loop().call_later` fires
        # on the
        # loop: a free loop fires it at `fire_at`, a blocked one fires it when the block ends.
        loop = asyncio.get_running_loop()
        ping_due = loop.create_future()

        def fire() -> None:
            if not ping_due.done():
                ping_due.set_result(time.monotonic())

        handle = loop.call_later(fire_at, fire)
        try:
            fired_at = await ping_due
            pong = await asyncio.wait_for(client.call("ping"), timeout=hold + 2.0)
            answered_at = time.monotonic()
        finally:
            handle.cancel()

        assert pong == {"pong": True}
        # **The measurement.** A free loop fires the callback at `fire_at` and answers at once, so
        # the
        # ping costs a few milliseconds. A blocked loop fires it only after `hold`, so the ping is
        # late
        # by about `hold - fire_at` — and that is what the threshold separates.
        late_by = fired_at - started - fire_at
        assert late_by < 0.4, (
            f"the ping callback fired {late_by:.2f}s late, so the loop was blocked by the probe"
        )
        took = answered_at - fired_at
        assert took < 0.5, f"the ping took {took:.2f}s to answer: the loop was blocked"
        await probe
        await client.close()
        await server.stop()

    asyncio.run(scenario())


def test_the_agent_module_exposes_a_host_flag() -> None:
    """`stokes-agent --host` prints the record; the brief adds it and T-204's dispatcher runs it."""
    source = Path(engine_env.__file__).with_name("__main__.py").read_text()
    assert "--host" in source, "the --host flag has not been wired into the agent's CLI"


def test_found_by_says_HOW_an_install_was_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Settings spec §2: each install row says whether it came from the environment or a scan.

    **Why it is worth reporting.** A user looking at a list of auto-detected installs needs to know
    which one came from the environment the launcher set - configuration somebody chose - and which
    merely happened to be on the disk. The two behave differently when the setting changes, and
    without the label the list gives no way to tell them apart.
    """
    monkeypatch.setattr(engine_env.platform, "system", lambda: "Linux")
    named = tmp_path / "env.sh"
    named.write_text("# a bashrc\n")
    monkeypatch.setenv("STOKES_OPENFOAM_BASHRC", str(named))
    monkeypatch.setattr(engine_env, "openfoam_candidates", lambda: [str(tmp_path / "prefix")])
    # A standard-prefix candidate: `<dir>/etc/bashrc` must exist for the scan to take it.
    (tmp_path / "prefix" / "etc").mkdir(parents=True)
    (tmp_path / "prefix" / "etc" / "bashrc").write_text("# another\n")

    engine_env.FOUND_BY.clear()
    found = engine_env.bashrc_candidates()
    assert [Path(p) for p in found] == [named, tmp_path / "prefix" / "etc" / "bashrc"]
    assert engine_env.found_by(str(named)) == "STOKES_OPENFOAM_BASHRC"
    assert engine_env.found_by(str(tmp_path / "prefix" / "etc" / "bashrc")) == "standard-prefix"


def test_found_by_does_not_guess_for_a_path_it_never_saw() -> None:
    """An unknown path answers `standard-prefix` rather than raising.

    **A setting the user added is marked by the STORE, not by this scan** - the agent never saw
    it, so there is nothing here to report. Answering the common case keeps the field present on
    every row, which matters because the UI shows it and a missing key would read as a missing
    install.
    """
    engine_env.FOUND_BY.clear()
    assert engine_env.found_by("/somewhere/else/etc/bashrc") == "standard-prefix"


def test_a_home_folder_install_is_reported_as_home_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Settings spec §2 and claude-win2's 1330 answer: `standard-prefix` and `home-scan` differ.

    A system prefix was put there by an administrator; a home folder by the user - and the one under
    `~` is the one they may not remember installing. Measured on this machine: the install lives at
    `<home>/opt/openfoam14`, which the original four globs did NOT match, so the probe reported no
    installs at all on a host that has one.
    """
    monkeypatch.setattr(engine_env.platform, "system", lambda: "Linux")
    home = tmp_path / "home"
    (home / "opt").mkdir(parents=True)
    installed = home / "opt" / "openfoam14" / "etc"
    installed.mkdir(parents=True)
    (installed / "bashrc").write_text("# openfoam\n")

    monkeypatch.delenv("STOKES_OPENFOAM_BASHRC", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(
        engine_env, "openfoam_candidates", lambda: [str(home / "opt" / "openfoam14")]
    )

    engine_env.FOUND_BY.clear()
    found = engine_env.bashrc_candidates()
    assert [Path(p) for p in found] == [installed / "bashrc"]
    assert engine_env.found_by(str(installed / "bashrc")) == "home-scan"


def test_a_system_prefix_install_is_standard_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half, so the two values are actually distinguished rather than both saying one.

    **A rule that cannot tell its cases apart is the failure this avoids**: if everything answered
    `home-scan`, the label would be decoration.
    """
    system = tmp_path / "usr" / "lib" / "openfoam" / "14" / "etc"
    system.mkdir(parents=True)
    (system / "bashrc").write_text("# openfoam\n")

    monkeypatch.delenv("STOKES_OPENFOAM_BASHRC", raising=False)
    monkeypatch.setattr(engine_env, "openfoam_candidates", lambda: [str(system.parent)])

    engine_env.FOUND_BY.clear()
    engine_env.bashrc_candidates()
    assert engine_env.found_by(str(system / "bashrc")) == "standard-prefix"
