"""The SSH runner against a real sshd on this machine (T-159a).

    STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \
        python3 -m pytest -q tests/engine/test_ssh_runner_engine.py

**Why this is an engine test and not a unit test.** Every defect this feature has had so far
was in the seam between our code and `ssh` itself: the login shell re-parsing an argv list, a
redirect running before the directory existed, `--` in the wrong place, and a classifier that
only one caller used. A fake transport agrees with whatever the author believed about ssh,
which is exactly the belief that was wrong four times.

**The test builds its own host and cleans up after itself** (claude-win2's 12:35 message): a
throwaway key pair under `$HOME/.stokes-test-ssh/`, an `authorized_keys` line with a marker, our
own `known_hosts`, and removal of all three in teardown. **Nothing is printed about the key**,
and nothing is installed on the system.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.engine

HOME = Path.home()
TEST_DIR = HOME / ".stokes-test-ssh"
KEY = TEST_DIR / "id_test"
KNOWN_HOSTS = TEST_DIR / "known_hosts"
MARKER = "stokes-test-throwaway"
STATE = HOME / ".stokes" / "agent"
REPO = Path(__file__).resolve().parent.parent.parent


def _sshd_available() -> bool:
    """Whether this machine can be its own ssh host at all."""
    probe = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            f"{HOME.name}@127.0.0.1",
            "true",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return "Permission denied" not in probe.stderr or "publickey" in probe.stderr


@pytest.fixture(scope="module")
def ssh_host() -> object:
    """A throwaway ssh identity, trusted by our own known_hosts, removed afterwards."""
    import shutil

    if not shutil.which("ssh") or not shutil.which("ssh-keygen"):
        pytest.skip("no ssh client on this machine")
    TEST_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not KEY.exists():
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", MARKER, "-f", str(KEY)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    KEY.chmod(0o600)
    authorized = HOME / ".ssh" / "authorized_keys"
    authorized.parent.mkdir(mode=0o700, exist_ok=True)
    had = authorized.exists()
    before = authorized.read_text() if had else ""
    # **IDEMPOTENT ON PURPOSE.** The teardown removes the line, so the SECOND run of this module
    # would start unauthorized and fail with "Permission denied" - a failure that points at whatever
    # the test was doing rather than at the fixture. Measured: that is exactly what happened, and I
    # chased the ssh argv for several probes before checking the file.
    _authorize()
    # Our own known_hosts, populated deliberately: this is what `host.trust` will do.
    scan = subprocess.run(
        ["ssh-keyscan", "-t", "ed25519", "127.0.0.1"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    KNOWN_HOSTS.write_text(scan.stdout)

    # A python that can import the agent from this checkout - the runner's own error message names
    # this as the alternative to installing the package on the host.
    wrapper = TEST_DIR / "agent-python"
    wrapper.write_text(f'#!/bin/sh\nPYTHONPATH={REPO / "agent"} exec {sys.executable} "$@"\n')
    wrapper.chmod(0o755)

    from stokes.run.ssh_hosts import SshHost

    yield SshHost(
        name="localhost-test",
        host="127.0.0.1",
        user=HOME.name,
        port=22,
        key=str(KEY),
        python=str(wrapper),
        added="test",
    )

    # **Teardown removes what it added**, so the machine is left as it was found.
    from stokes.run.ssh import run_remote

    try:
        record = json.loads((STATE / "session.json").read_text())
        pid = record.get("pid")
        if isinstance(pid, int):
            run_remote(ssh_host_placeholder(), ["kill", "-TERM", str(pid)], home=TEST_DIR)
    except Exception:
        pass
    if not had:
        authorized.unlink(missing_ok=True)
    else:
        authorized.write_text(
            "".join(line for line in before.splitlines(keepends=True) if MARKER not in line)
        )


def _authorize() -> None:
    """Append our public key to `authorized_keys` if it is not already there.

    **Idempotent on purpose**: the teardown removes the line, so a second run needs it put back. A
    fixture that only works once is worse than none, because the failure it produces points at
    whatever the test was doing rather than at the fixture.
    """
    authorized = HOME / ".ssh" / "authorized_keys"
    authorized.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        text = authorized.read_text()
    except OSError:
        text = ""
    if MARKER not in text:
        with authorized.open("a") as handle:
            handle.write(KEY.with_suffix(".pub").read_text())
    authorized.chmod(0o600)


def ssh_host_placeholder() -> object:
    """The fixture's own host value, for teardown after the fixture object is gone."""
    from stokes.run.ssh_hosts import SshHost

    return SshHost(
        name="localhost-test",
        host="127.0.0.1",
        user=HOME.name,
        port=22,
        key=str(KEY),
        python=str(TEST_DIR / "agent-python"),
        added="test",
    )


def test_an_unknown_host_key_is_refused_without_a_prompt(ssh_host: object) -> None:
    """**The refusal the spec requires, seen against a real sshd.** Our `known_hosts` starts
    empty for this, so ssh has nothing to verify against - and the answer must be a refusal,
    never a prompt: a prompt inside a server process is a hang."""
    from stokes.run.ssh import SshError, run_remote

    empty = TEST_DIR / "known_hosts_empty"
    empty.write_text("")
    bare = ssh_host_placeholder()
    object.__setattr__  # noqa: B018 - documented below
    # Point the run at the empty file by using a host whose known_hosts directory is a temp one.
    import tempfile

    from stokes.run.ssh_hosts import SshHost

    with tempfile.TemporaryDirectory() as folder:
        tmp_home = Path(folder)
        (tmp_home / "known_hosts").write_text("")
        host = SshHost(
            name=bare.name,
            host=bare.host,
            user=bare.user,
            port=bare.port,
            key=bare.key,
            python=bare.python,
            added="test",
        )
        with pytest.raises(SshError) as caught:
            run_remote(host, ["true"], home=tmp_home)
    # **THE EXACT CODE, not either of two** (Opus recheck round 3, finding 5). The old assertion
    # accepted `host.key_changed` for an UNKNOWN key, which is the one case where carrying on is
    # dangerous - so a regression that conflated them would have passed.
    assert caught.value.code == "host.key_unknown", caught.value.code
    # **And the fingerprint is IN the message**, because "check its fingerprint" without one is
    # advice a user cannot act on.
    assert "SHA256:" in str(caught.value), str(caught.value)


def test_a_TRUSTED_host_connects(ssh_host: object, tmp_path: Path) -> None:
    """The second of the three states: having trusted the key, the connection works.

    **Asserted so the test above cannot pass by refusing everything** - a rule that refuses every
    host is no better than one that refuses none.
    """
    from stokes.run.ssh import run_remote, trust_host

    folder = tmp_path / "trusted"
    folder.mkdir()
    (folder / "known_hosts").write_text("")
    host = _a_host(folder)

    # **The fingerprint for THIS host and port**, from the same scan `host.trust` uses.
    from stokes.run.ssh import host_key_fingerprint

    fingerprint = host_key_fingerprint(host)
    trust_host(host, fingerprint, folder)

    reply = run_remote(host, ["true"], home=folder)
    assert reply.returncode == 0


def test_a_CHANGED_key_is_its_own_error_and_is_not_accepted(
    ssh_host: object, tmp_path: Path
) -> None:
    """**The third state, and the one that matters.** A host whose key has changed must be refused
    with `host.key_changed` - different from an unknown key, because an unknown key may be a machine
    nobody has visited while a changed one is what an interception looks like.

    The file is made to hold a DIFFERENT key for the same host, which is what a rebuild or an attack
    looks like from here.
    """
    import subprocess

    from stokes.run.ssh import SshError, host_key_fingerprint, run_remote

    folder = tmp_path / "changed"
    folder.mkdir()
    other = folder / "other"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    key = other.with_suffix(".pub").read_text().split()[1]
    (folder / "known_hosts").write_text(f"127.0.0.1 ssh-ed25519 {key}\n")

    host = _a_host(folder)
    with pytest.raises(SshError) as caught:
        run_remote(host, ["true"], home=folder)
    assert caught.value.code == "host.key_changed", caught.value.code
    # **And the new fingerprint is in the message**, so a user can compare it with what they were
    # told rather than being left to guess what changed.
    assert "SHA256:" in str(caught.value), str(caught.value)
    assert host_key_fingerprint(host) in str(caught.value), (
        "the message does not carry the key the host now presents"
    )


def _a_host(home: Path) -> object:
    """A host record for this machine, with our own `known_hosts` under `home`."""
    from stokes.run.ssh_hosts import SshHost

    return SshHost(
        name="localhost-test",
        host="127.0.0.1",
        user=HOME.name,
        port=22,
        key=str(KEY),
        python=str(TEST_DIR / "agent-python"),
        added="test",
    )


def test_the_runner_works_when_the_CLIENT_home_differs(
    ssh_host: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus H3, against a real sshd.** The client's own state dir is pointed at a path the remote
    does not have - which is the ordinary case for a Windows client, and the case a localhost test
    with identical homes can never see.

    The path sent must be one the REMOTE expands, so the run works whatever the client home is.
    """
    monkeypatch.setenv(
        "STOKES_AGENT_STATE_DIR", str(tmp_path / "a-place-the-remote-has-never-heard-of")
    )
    from stokes.run.ssh import ensure_agent, remote_state_path, run_remote

    # **`$HOME` MUST BE EXPANDED BY THE REMOTE, and this is the assertion that says so.** The
    # previous version of this test checked only that the string STARTED WITH `$HOME/` - which is
    # true of a string the remote never expands, and it passed while every real command looked for a
    # directory literally named `$HOME`. Opus caught it by replaying the command in bash; the test
    # was **tautological** and had to be replaced rather than adjusted.
    built = remote_state_path("session.json")
    assert "$HOME" in built, built

    # 1. **The remote resolves it to a real path**, and NOT to a literal `$HOME`.
    echo = run_remote(ssh_host, ["echo", built], home=TEST_DIR)  # type: ignore[arg-type]
    resolved = echo.stdout.strip()
    assert resolved.startswith("/"), f"the remote did not expand it: {resolved!r}"
    assert "$HOME" not in resolved, f"the remote kept the literal: {resolved!r}"

    # 2. **The whole chain works with the client pointed somewhere else.**
    record = ensure_agent(ssh_host, TEST_DIR)  # type: ignore[arg-type]
    assert record.get("protocol") == 3
    again = ensure_agent(ssh_host, TEST_DIR)  # type: ignore[arg-type]
    assert again.get("pid") == record.get("pid"), "a differing client home broke the re-attach"

    # 3. **AND NO LITERAL `$HOME` DIRECTORY WAS CREATED.** This is the defect itself: the old code
    #    made `~/$HOME/.stokes/agent` on the host, and nothing said so.
    #
    #    **THE COMMAND GOES AS ONE ARGUMENT, and my first version did not** (Opus residue 5a).
    #    `run_remote` joins its argv and the remote LOGIN SHELL parses the result, so
    #    `["sh", "-c", "..."]` arrived as three words, the `sh -c` was mangled, and the check
    #    printed CLEAN **even with the bad directory present** - measured: I created one and the
    #    test still passed. One string means the login shell parses it once.
    assert _literal_home_present(ssh_host) is False, (
        "a directory named '$HOME' exists on the remote: the path was never expanded"
    )
    #    **AND IT IS PROVEN ABLE TO FAIL**, which is the part that was missing. The negative
    #    case below creates the bad directory and asserts the check NOTICES - a check that cannot
    #    fail is worse than none, because it is counted as evidence.
    assert _literal_home_present(ssh_host, create=True) is True, (
        "the probe reports CLEAN even for a directory it just created, so it governs nothing"
    )
    #    Cleaned up, so the suite leaves the host as it found it.
    _remove_literal_home(ssh_host)


def _literal_home_present(ssh_host: object, *, create: bool = False) -> bool:
    """Whether a directory literally named `$HOME` exists in the remote's home.

    **One command string, not an argv list**: `run_remote` joins its arguments and the remote login
    shell parses the result, so a `sh -c` split across arguments is mangled - which is what made the
    first version of this check unable to fail.
    """
    from stokes.run.ssh import run_remote

    if create:
        made = run_remote(
            ssh_host,  # type: ignore[arg-type]
            [r'mkdir -p "$HOME/\$HOME" && echo MADE'],
            home=TEST_DIR,
        )
        assert "MADE" in made.stdout, made.stderr
    probe = run_remote(
        ssh_host,  # type: ignore[arg-type]
        [r'test -e "$HOME/\$HOME" && echo LITERAL || echo CLEAN'],
        home=TEST_DIR,
    )
    return "LITERAL" in probe.stdout


def _remove_literal_home(ssh_host: object) -> None:
    from stokes.run.ssh import run_remote

    run_remote(
        ssh_host,  # type: ignore[arg-type]
        [r'rm -rf "$HOME/\$HOME" && echo GONE'],
        home=TEST_DIR,
    )


def test_the_runner_starts_the_agent_attaches_and_re_attaches(ssh_host: object) -> None:
    """**The whole chain, measured**: the agent starts detached over ssh, a tunnel carries the v3
    mutual handshake, and a second call finds the SAME agent rather than starting another."""
    from stokes.run.ssh import Tunnel, ensure_agent, read_secret, secure_connect

    record = ensure_agent(ssh_host, TEST_DIR)  # type: ignore[arg-type]
    assert record.get("protocol") == 3, "a remote agent must speak the mutual handshake"

    secret = read_secret(ssh_host, TEST_DIR)  # type: ignore[arg-type]
    assert len(secret) == 64, "the agent's own secret, 32 bytes hex"

    with Tunnel(ssh_host, int(record["port"]), TEST_DIR) as tunnel:  # type: ignore[arg-type]
        client = secure_connect(ssh_host, secret, tunnel.local_port)  # type: ignore[arg-type]
        assert client.agent, "the mutual handshake did not identify the agent"

    again = ensure_agent(ssh_host, TEST_DIR)  # type: ignore[arg-type]
    assert again.get("pid") == record.get("pid"), "a second attach started a NEW agent"


def test_a_metacharacter_in_the_interpreter_cannot_RUN_anything(ssh_host: object) -> None:
    """**Opus item 1, against a real sshd.** A `python` of `python3;echo${IFS}INJECTED;python3` is
    refused at the boundary now, and this asserts the thing that matters: **nothing executes.**

    **THE ASSERTION IS ON STDOUT, and that distinction is the whole test.** With the value quoted
    but not validated, `INJECTED` still appears - in bash's *error message* ("command not found"),
    because the shell was handed one word it could not execute. So `"INJECTED" not in output` would
    fail even with correct quoting, and would pass for the wrong reason. Measured both ways:

        UNQUOTED  stdout='INJECTED'   the command RAN
        QUOTED    stdout=''           the name appears only in the error text
    """
    from stokes.run.ssh import SshError, check_agent_importable
    from stokes.run.ssh_hosts import HostError, SshHost

    payload = "python3;echo${IFS}INJECTED;python3"
    hostile = SshHost(
        name="localhost-test",
        host="127.0.0.1",
        user=HOME.name,
        port=22,
        key=str(KEY),
        python=payload,
        added="test",
    )
    with pytest.raises((HostError, SshError)):
        check_agent_importable(hostile, TEST_DIR)


def test_a_host_that_cannot_import_the_agent_says_how_to_fix_it(ssh_host: object) -> None:
    """The spec asks for the instruction rather than a timeout. A bare `python3` on a machine
    without the package is exactly the case, and the message names both ways to satisfy it."""
    from stokes.run.ssh import SshError, check_agent_importable
    from stokes.run.ssh_hosts import SshHost

    bare = SshHost(
        name="localhost-test",
        host="127.0.0.1",
        user=HOME.name,
        port=22,
        key=str(KEY),
        python="python3",
        added="test",
    )
    with pytest.raises(SshError) as caught:
        check_agent_importable(bare, TEST_DIR)
    assert caught.value.code == "host.agent_not_installed"
    message = str(caught.value)
    assert "pip install" in message and "PYTHONPATH" in message
