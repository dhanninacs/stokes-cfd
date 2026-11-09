"""Opus recheck item 6c: showing a host key, and trusting only the one that was confirmed.

**Spec D**, which the earlier work was missing: an unknown key must come with a **SHA256
fingerprint** and a way to trust it, and a **changed** key must stay a **different** error -
because an unknown key may be a machine nobody has visited, while a changed one is what an
interception looks like. Telling them apart is "carry on" against "stop".
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from stokes.run.ssh import SshError, host_key_fingerprint, trust_host
from stokes.run.ssh_hosts import HostError, SshHost, known_hosts_path, strict_options

#: Where the throwaway key and our own `known_hosts` live, made by the engine fixture.
TEST_DIR = Path.home() / ".stokes-test-ssh"


def _authorize() -> None:
    """Make sure the throwaway key is accepted, **because another file's teardown removes it**.

    The engine fixture strips its `authorized_keys` line when it finishes, so a test in THIS file
    afterwards is refused with "Permission denied" - which reads as a broken tunnel rather than
    a missing fixture. Measured: two tests here failed for exactly that while passing when this
    file ran alone. Idempotent, so running it twice costs nothing.
    """
    authorized = Path.home() / ".ssh" / "authorized_keys"
    public = (TEST_DIR / "id_test.pub").read_text()
    try:
        text = authorized.read_text()
    except OSError:
        text = ""
    if public.strip() not in text:
        authorized.parent.mkdir(mode=0o700, exist_ok=True)
        with authorized.open("a") as handle:
            handle.write(public)
    authorized.chmod(0o600)


def a_host() -> SshHost:
    return SshHost(
        name="localhost-test",
        host="127.0.0.1",
        user="chandra",
        port=22,
        key=str(Path.home() / ".stokes-test-ssh" / "id_test"),
        python="python3",
        added="test",
    )


# ------------------------------------------------------------------ item 6a: the option list


def test_the_option_list_omits_options_OLD_ssh_does_not_know() -> None:
    """**Opus recheck item 6a.** `KnownHostsCommand` exists only from **OpenSSH 8.5**, and an ssh
    that does not recognise an option name **refuses every connection** with "Bad configuration
    option". Passing it would break Windows 10's built-in 8.1, Ubuntu 20.04's 8.2 and RHEL 8's 8.0
    **completely** - on the machines where the app would otherwise work.

    **Nothing is lost**: `-F none` is the first argument, so no configuration file is read and a
    `KnownHostsCommand` can never be set in the first place.
    """
    joined = " ".join(strict_options(Path("/tmp/known_hosts"), Path("/tmp/key")))
    assert "KnownHostsCommand" not in joined, (
        "an option older ssh does not know makes it refuse every connection"
    )
    # **The protections that DO work everywhere are still there.**
    assert "ProxyCommand=none" in joined
    assert "GlobalKnownHostsFile=" in joined
    assert "ControlMaster=no" in joined


# ------------------------------------------------------------------ item 6b: the % expansion


def test_a_state_folder_with_a_PERCENT_is_refused() -> None:
    """**Opus recheck item 6b.** ssh expands `%`-tokens in a `UserKnownHostsFile` value, and every
    one of them expands to something - measured on this tree:

        %u -> the user name    %h -> the host    %d -> the home directory    %% -> a literal %

    So a `STOKES_HOME` containing `%` would be rewritten by ssh into a **different file**, and since
    that file is what host-key verification reads, the effect is silent: our `known_hosts` would not
    be consulted at all.
    """
    with pytest.raises(HostError) as caught:
        known_hosts_path(Path("/home/<you>/100%done"))
    assert "%" in str(caught.value)


def test_a_state_folder_with_WHITESPACE_is_ALLOWED_and_quoted() -> None:
    """**My refusal was a false refusal for a real user** (Opus round 3, item 6d). Windows user
    names contain spaces, and refusing the folder made **every SSH host unusable** for them.

    Measured both ways: `ssh -o UserKnownHostsFile="…"` with a quoted value containing a space
    connects successfully, and `ssh -G` reports the path intact rather than split. So the fix is to
    QUOTE the path where it reaches ssh, not to refuse the user's home.
    """
    from stokes.run.ssh_hosts import known_hosts_option

    spaced = Path("/home/<you>/a b")
    assert known_hosts_path(spaced).name == "known_hosts"
    option = known_hosts_option(known_hosts_path(spaced))
    assert option.startswith('UserKnownHostsFile="'), option
    assert option.endswith('"'), option
    # **And it is the option `strict_options` actually passes**, not a helper nothing calls.
    assert option in strict_options(known_hosts_path(spaced), None)


def test_an_ordinary_state_folder_is_kept() -> None:
    """The false-refusal half - the path every real user has."""
    assert known_hosts_path(Path("/home/<you>/.stokes")).name == "known_hosts"


# ------------------------------------------------------------------ item 6c: the fingerprint


@pytest.mark.engine
def test_the_fingerprint_is_a_SHA256_and_is_not_invented(tmp_path: Path) -> None:
    """**What a person compares.** `host.fingerprint` shows this and `host.trust` requires it, so it
    must be exactly the string `ssh-keygen` prints - not a hash of our own devising, because a user
    checks it against what whoever runs the machine told them.
    """
    _authorize()
    fingerprint = host_key_fingerprint(a_host(), tmp_path)
    assert fingerprint.startswith("SHA256:"), fingerprint
    # **Checked against ssh-keygen directly**, so this asserts the SHAPE OF THE VALUE rather than
    # that our own function is self-consistent.
    import subprocess

    scan = subprocess.run(
        ["ssh-keyscan", "-t", "ed25519", "127.0.0.1"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    keys = [
        line
        for line in scan.stdout.splitlines()
        if line.strip() and not line.startswith("#") and len(line.split()) >= 2
    ]
    assert keys, "ssh-keyscan gave no key, so this test proves nothing"
    key_file = tmp_path / "host.key"
    key_file.write_text(keys[0] + "\n")
    printed = subprocess.run(
        ["ssh-keygen", "-lf", str(key_file), "-E", "sha256"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert fingerprint in printed.stdout, (
        f"our fingerprint {fingerprint} is not the one ssh-keygen printed: {printed.stdout!r}"
    )


@pytest.mark.engine
def test_a_fingerprint_that_does_NOT_match_writes_NOTHING(tmp_path: Path) -> None:
    """**The comparison is the entire value of asking a person to confirm.** A `host.trust` that
    fetched the fingerprint and then wrote whatever came back would be trusting the network.

    Asserted on the FILE, because "nothing was written" is the claim.
    """
    _authorize()
    _ = host_key_fingerprint(a_host(), tmp_path)
    with pytest.raises(SshError) as caught:
        trust_host(a_host(), "SHA256:not-the-key-you-checked", tmp_path)
    # **Its OWN code** (Opus round 3, finding 5). Reporting a typo as `host.key_changed` sends a
    # user to the wrong conclusion: `key_changed` means the HOST presented a different key, which is
    # what an interception looks like, while this is a value the CALLER got wrong.
    assert caught.value.code == "host.fingerprint_mismatch", caught.value.code
    assert not known_hosts_path(tmp_path).exists(), "a refused trust wrote a key anyway"


@pytest.mark.engine
def test_trusting_the_confirmed_key_writes_it_once(tmp_path: Path) -> None:
    """The happy path, and **idempotent**: trusting the same host twice must not append the key a
    second time, or the file grows with every connection attempt."""
    _authorize()
    host = a_host()
    fingerprint = host_key_fingerprint(host, tmp_path)
    # **The reply says WHICH it did** (Opus finding 4), so a caller can report "replaced" rather
    # than guessing - which is why this is a record and not a bare fingerprint.
    reply = trust_host(host, fingerprint, tmp_path)
    assert reply["fingerprint"] == fingerprint
    assert reply["replaced"] is False
    target = known_hosts_path(tmp_path)
    assert target.is_file()
    lines = [line for line in target.read_text().splitlines() if line.strip()]
    assert len(lines) == 1, lines
    # **No comment line.** `ssh-keyscan` prints a banner first, and writing it would put text into
    # the file ssh parses as keys.
    assert not any(line.startswith("#") for line in lines)
    assert target.stat().st_mode & 0o077 == 0, oct(target.stat().st_mode)

    trust_host(host, fingerprint, tmp_path)
    again = [line for line in target.read_text().splitlines() if line.strip()]
    assert len(again) == 1, "trusting twice appended the key again"


# ------------------------------------------------------------------ L3: the tunnel


def test_the_tunnel_writes_stderr_to_a_FILE_not_a_pipe() -> None:
    """**Opus L3, and my first version of this test did not govern it.**

    It called `_stderr_log()` and inspected the object it returned - so replacing the argument
    `_open_once` actually passes with a `PIPE` left it **passing**. **A test of a helper cannot see
    what the caller does with it**, which is the same lesson as the mesh fakes that ignored the host
    name and the H1 mode that was set at the wrong call site.

    This starts a REAL tunnel and asserts what the running process holds. **`Popen.stderr` is
    `None` when a file is passed** - measured, after my first version asserted the opposite and
    failed on a correct implementation - so `None` is the evidence that the destination is a file.
    """
    _authorize()
    from stokes.run.ssh import Tunnel

    # **A port with nothing behind it still forwards**, so this needs no agent behind the far end.
    tunnel = Tunnel(a_host(), 9, TEST_DIR)
    try:
        tunnel.open(timeout=20)
        process = tunnel._process
        assert process is not None
        # **`None` means "not a pipe this process owns"**: `Popen` sets the attribute only when it
        # created the pipe, which is the case that can fill and block.
        assert process.stderr is None, (
            f"stderr is {process.stderr!r}: a pipe nobody drains fills after 64 KiB and stops the "
            f"tunnel forwarding"
        )
        # **And a real file exists**, which is what carries the error message out.
        assert tunnel._stderr_path is not None
        assert Path(tunnel._stderr_path).is_file()
    finally:
        tunnel.close()


def test_ExitOnForwardFailure_is_on_the_REAL_command_line() -> None:
    """**The argv the tunnel is actually started with**, not the source that builds it. My first
    version read the source text, which passes whenever the string appears anywhere - including in a
    comment.

    Driven by starting a tunnel against a host that cannot resolve: ssh fails, and the failure is
    observable. With the option it exits promptly and says why; without it, a forward that fails
    leaves ssh running against a port nothing can use.
    """
    _authorize()
    from stokes.run.ssh import Tunnel

    host = SshHost(
        name="nowhere",
        # **`.invalid` is reserved by RFC 2606 and never resolves**, so this needs no network.
        host="stokes-does-not-exist.invalid",
        user="chandra",
        port=22,
        key=str(TEST_DIR / "id_test"),
        python="python3",
        added="test",
    )
    tunnel = Tunnel(host, 22, TEST_DIR)
    captured: dict[str, object] = {}
    real_popen = subprocess.Popen

    def recording(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return real_popen(argv, **kwargs)

    try:
        # **One `with`, two contexts**, which is also the clearer reading: patch the call, ignore
        # its failure, then assert on what it was given.
        with (
            mock.patch("stokes.run.ssh.subprocess.Popen", recording),
            contextlib.suppress(SshError),
        ):
            tunnel.open(timeout=8)
        argv = captured.get("argv")
        assert isinstance(argv, list), "Popen was not called, so this proves nothing"
        joined = " ".join(argv)
        assert "ExitOnForwardFailure=yes" in joined, joined
        # **And the stderr destination, from the call itself.**
        kwargs = captured.get("kwargs")
        assert isinstance(kwargs, dict)
        assert kwargs.get("stderr") != subprocess.PIPE, "a pipe that nothing drains"
    finally:
        tunnel.close()


def test_a_tunnel_that_cannot_open_REPORTS_the_hosts_own_words() -> None:
    """**The message, not just the failure.** Measured before the fix: a tunnel that could not come
    up said only "did not come up within 20s". Now ssh's last stderr line is appended.
    """
    _authorize()
    from stokes.run.ssh import Tunnel

    host = SshHost(
        name="nowhere",
        host="stokes-does-not-exist.invalid",
        user="chandra",
        port=22,
        key=str(TEST_DIR / "id_test"),
        python="python3",
        added="test",
    )
    tunnel = Tunnel(host, 22, TEST_DIR)
    try:
        with pytest.raises(SshError) as caught:
            tunnel.open(timeout=5)
        # **`host.ssh_failed`, not `host.tunnel_failed`** (Opus residue 2, after the fix): ssh
        # says "Could not resolve hostname", which is a definite answer about the HOST rather than
        # about the tunnel. Reporting it as a tunnel failure sent the user to the wrong thing, and
        # retrying it five times could not help. Measured before the fix: a bare
        # "did not come up within 20s" for every cause, after a pointless retry.
        assert caught.value.code == "host.ssh_failed", caught.value.code
        # **And the host's own words are in it**, which is the point of the test.
        assert "resolve" in str(caught.value).lower(), str(caught.value)
    finally:
        tunnel.close()


# ------------------------------------------------------------------ round 3, finding 2


def test_the_state_folder_is_not_QUOTED_AGAIN_in_the_start_command() -> None:
    """**Opus recheck round 3, finding 2.** `start_agent` wrapped the folder in `shlex.quote`, which
    single-quotes the `"$HOME"` prefix and stops it expanding - so `mkdir -p` created a directory
    **literally named `"$HOME"`** and the log redirect then failed. Because the job is backgrounded,
    the only symptom was the 60 s timeout, and **the existing tests passed only because
    `~/.stokes/agent` already existed on the test box.**

    Measured against a real sshd with a fresh home:

        BEFORE  mkdir -p '"$HOME"/.stokes/agent'   -> a literal \'"$HOME"\' directory, no real path
        AFTER   mkdir -p "$HOME"/.stokes/agent     -> the remote's own .stokes/agent

    Asserted on the command TEXT, because that is the choice: one form expands and one does not.
    """
    import inspect

    from stokes.run.ssh import remote_state_path, start_agent

    folder = remote_state_path("")
    assert folder == '"$HOME"/.stokes/agent'
    source = inspect.getsource(start_agent)
    # **The f-string interpolates the VARIABLE, so the source reads `{folder}`** - asserting the
    # substituted text failed on a correct implementation. What matters is that the expression is
    # the bare variable rather than a quoting call around it.
    assert "mkdir -p {folder} &&" in source, source[-400:]
    assert "shlex.quote(str(folder))" not in source, "the folder is quoted again"
    assert "shlex.quote(folder)" not in source, "the folder is quoted again"


def test_the_log_redirect_uses_the_same_unquoted_prefix() -> None:
    """The redirect is performed by the shell **before** `nohup` runs, so a wrong path here fails
    before anything starts - and the log that would explain it cannot be written either."""
    import inspect

    from stokes.run.ssh import remote_state_path, start_agent

    log = remote_state_path("agent.log")
    assert log == '"$HOME"/.stokes/agent/agent.log'
    source = inspect.getsource(start_agent)
    # **`{log_path}`, the variable**, for the same reason as above.
    assert "> {log_path} 2>&1" in source, source[-400:]
    assert "shlex.quote(remote_state_path" not in source, "the log path is quoted again"


def test_a_PORT_TAKEN_by_another_process_is_not_reported_as_success(tmp_path: Path) -> None:
    """**The worst bug I found in this file, and Opus's residue 2 led me to it.**

    `free_local_port` binds a port, reads the number and **closes** the socket; ssh binds it a
    moment later. If another process takes it in between, `wait_for_port` sees **that** process
    listening and reports success - so `Tunnel.open` returned a port while ssh had already died,
    and a connection went to a stranger's listener. Measured: `open` returned the port, ssh's exit
    code was **255** ("Could not request local forwarding"), and the squatter got the connection.

    **What it costs, measured rather than inferred**: `connect_mutual` checks the AGENT's proof
    **before** sending its own, so a squatter receives a nonce and a client name and never the
    secret - Opus corrected me on this after I wrote that a caller "would send the token". It is a
    **misleading error and a denial of service, not a leak**.

    **And the first fix did not work**, which Opus found with a stand-in ssh that binds late: ssh
    binds `-L` only after key exchange and login, so a fixed pause is not enough. The tunnel now
    waits for **ssh's own announcement** (`debug1: Local forwarding listening on …`) and treats the
    failure lines as the truth, because ssh prints BOTH when the bind fails.
    """
    import shutil
    import socket

    _authorize()
    from stokes.run.ssh import Tunnel

    folder = tmp_path / "spaced"
    folder.mkdir()
    shutil.copy(TEST_DIR / "known_hosts", folder / "known_hosts")

    # **A real squatter on a real port**, held from before the tunnel starts.
    squatter = socket.socket()
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(1)
    stolen = int(squatter.getsockname()[1])

    import stokes.run.ssh as ssh_module

    real = ssh_module.free_local_port
    handed: list[int] = []

    def rigged() -> int:
        # **The race, exactly**: the first port handed out is one that is already taken.
        if not handed:
            handed.append(stolen)
            return stolen
        return real()

    ssh_module.free_local_port = rigged
    tunnel = Tunnel(a_host(), 22, folder)
    try:
        port = tunnel.open(timeout=15)
        # **Either it retried onto a different port, or it refused.** What it must never do is
        # return the taken port and leave the caller talking to the squatter.
        assert port != stolen, (
            "the tunnel reported the squatter's port as its own, so the caller would exchange "
            "the handshake with another process"
        )
        assert handed, "the rigged port was never handed out, so this proves nothing"
    except SshError as exc:
        # A refusal is also acceptable - but it must not be a bare timeout, and it must be about
        # the port rather than claiming the tunnel simply did not come up.
        assert "port" in str(exc).lower() or exc.code == "host.tunnel_failed", str(exc)
    finally:
        tunnel.close()
        squatter.close()
        ssh_module.free_local_port = real


def test_the_tunnel_log_is_PRIVATE_and_cannot_be_symlinked(tmp_path: Path) -> None:
    """**Opus residue 1.** The log used to be `<state>/tunnel-<port>.log`, opened `"wb"` - so it was
    **predictable**, it **followed a symlink**, and it was created **0664**.

    Measured before the fix: a symlink planted at that path made the tunnel **overwrite the file it
    pointed at** (a file reading "the user's data" came back as "ssh noise"), and the log was
    world-readable. A local user can win a race against a predictable name in a shared folder.

    `mkstemp` is `O_EXCL` - so it cannot follow an existing symlink - and 0600.
    """
    from stokes.run.ssh import Tunnel

    state = tmp_path / "state"
    state.mkdir()
    # **A symlink where the old code would have written**, pointing at something that matters.
    precious = state / "IMPORTANT"
    precious.write_text("the user's data")
    (state / "tunnel-45000.log").symlink_to(precious)

    tunnel = Tunnel.__new__(Tunnel)
    tunnel._home = state
    tunnel.local_port = 45000
    tunnel._stderr_path = None
    tunnel._stderr_handle = None
    handle = tunnel._stderr_log()
    try:
        handle.write(b"ssh noise")
        handle.flush()
        # **The file the symlink pointed at is untouched**, which is the whole point.
        assert precious.read_text() == "the user's data", "the tunnel wrote through a symlink"
        # **And the real log is private, unique and not a symlink.**
        assert tunnel._stderr_path is not None
        log = Path(tunnel._stderr_path)
        assert log.name != "tunnel-45000.log", "the name is still predictable"
        assert log.stat().st_mode & 0o077 == 0, oct(log.stat().st_mode)
        assert not log.is_symlink()
        # **Two tunnels must not share a log**, or the second truncates the first's complaint.
        other = Tunnel.__new__(Tunnel)
        other._home = state
        other.local_port = 45000
        other._stderr_path = None
        other._stderr_handle = None
        second = other._stderr_log()
        assert Path(other._stderr_path).name != log.name
        second.close()
        other._remove_stderr_log()
    finally:
        handle.close()
        # **Removed on close**, or a reconnecting client leaves one file per attempt behind.
        tunnel._remove_stderr_log()
        tunnel._stderr_handle = None
        assert not log.exists(), "the tunnel left its log behind"


def test_a_key_for_ANOTHER_PORT_is_not_mistaken_for_this_host(tmp_path: Path) -> None:
    """**Opus residue 3.** `_held_keys` always matched the bare host name, so an entry for port 22
    counted as an entry for the same machine on 2222 - trusting the second was refused as
    `host.key_changed`, and `replace` then **deleted the port-22 entry**, which was perfectly good.
    Measured: both ports matched the same line.

    A port is part of a host's identity, and ssh spells a non-default one `[host]:port`.
    """
    from stokes.run.ssh import _held_keys

    text = (
        "127.0.0.1 ssh-ed25519 KEY-A\n"
        "[127.0.0.1]:2222 ssh-ed25519 KEY-B\n"
        "other.example ssh-ed25519 KEY-C\n"
        "host.example,10.0.0.1 ssh-ed25519 KEY-D\n"
        "|1|aGFzaGVk hash= ssh-ed25519 KEY-E\n"
    )

    def held(host_name: str, port: int) -> int:
        return len(
            _held_keys(
                text,
                SshHost(
                    name="x", host=host_name, user="u", port=port, key="/k", python="py", added="t"
                ),
            )
        )

    assert held("127.0.0.1", 22) == 1, "the bare entry is this host's, on the default port"
    assert held("127.0.0.1", 2222) == 1, "the bracketed entry is this host's, on 2222"
    # **The regression**: 2222 must NOT see the port-22 line.
    on_2222 = _held_keys(
        text,
        SshHost(name="x", host="127.0.0.1", user="u", port=2222, key="/k", python="py", added="t"),
    )
    assert "[127.0.0.1]:2222" in on_2222[0], "2222 matched something that is not its own entry"
    assert held("127.0.0.1", 2022) == 0, "a port with no entry matches nothing"
    assert held("other.example", 22) == 1
    # **`host,ip` lists several names** for one key, and either name matches.
    assert held("host.example", 22) == 1
    assert held("10.0.0.1", 22) == 1
    # **A hashed entry names nothing we can compare**, so it is not claimed - documented, and the
    # test below shows removal still works for it.
    assert held("anything.example", 22) == 0


def test_replacing_removes_a_HASHED_entry_our_matching_cannot_see(tmp_path: Path) -> None:
    """**Why removal goes through `ssh-keygen -R` rather than deleting the lines we recognised.**

    A hashed `known_hosts` entry (`|1|...`) names no host, so `_held_keys` cannot see it - measured:
    it returns 0 for a file that certainly holds the entry. A hand-rolled removal would therefore
    **leave the old key in place while reporting that it was replaced**, and the old key would keep
    working. `ssh-keygen` knows the file's own format, including hashes.
    """
    import subprocess

    from stokes.run.ssh import _held_keys, _remove_with_ssh_keygen

    folder = tmp_path / "hashed"
    folder.mkdir()
    known_hosts = folder / "known_hosts"
    known_hosts.write_text(
        "127.0.0.1 ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
    )
    subprocess.run(
        ["ssh-keygen", "-H", "-f", str(known_hosts)], check=True, capture_output=True, timeout=60
    )
    assert known_hosts.read_text().lstrip().startswith("|1|"), "the file did not get hashed"

    host = SshHost(
        name="hashed", host="127.0.0.1", user="u", port=22, key="/k", python="py", added="t"
    )
    assert _held_keys(known_hosts.read_text(), host) == [], (
        "this test is only meaningful while our matching CANNOT see a hashed entry"
    )
    # **And ssh-keygen removes it anyway**, which is the point of using it.
    assert _remove_with_ssh_keygen(known_hosts, host).strip() == ""


def test_CheckHostIP_is_pinned_so_no_IP_entry_is_written(tmp_path: Path) -> None:
    """**Opus residue 4.** With `-F none` there is no config to set `CheckHostIP`, and **ssh older
    than 8.5 defaults it to yes** - which is Windows 10's built-in 8.1. It then writes the host's
    ADDRESS beside its name into OUR `known_hosts`, and an address that changes (a laptop, a DHCP
    lease, a VPN) makes strict checking fail for a host whose name is correctly trusted.

    **Unlike `KnownHostsCommand`, this option exists in every version**, which is why pinning it
    costs no compatibility.
    """
    joined = " ".join(strict_options(Path("/tmp/known_hosts"), Path("/tmp/key")))
    assert "CheckHostIP=no" in joined, (
        "older ssh will add IP entries to our known_hosts, which strict checking then trips over"
    )
    # **And the option older ssh rejects is still absent**, so this did not undo that fix.
    assert "KnownHostsCommand" not in joined


def test_a_host_NAMED_changed_is_not_reported_as_an_interception() -> None:
    """**Opus residue 6.** The test was `"changed" in lowered` over the whole of ssh's output - so
    a host called `changed.example` made an **UNKNOWN** key report as `host.key_changed`, the code
    that means "this looks like an interception".

    **That is the dangerous direction to be wrong in**: it tells a user to stop for a machine nobody
    attacked, and it blurs the case that really IS an interception, because the two become
    indistinguishable. ssh's own banner is matched instead.
    """
    import subprocess

    from stokes.run.ssh import classify_failure

    host = SshHost(
        name="changed", host="changed.example", user="u", port=22, key="/k", python="py", added="t"
    )
    unknown = classify_failure(
        subprocess.CompletedProcess(
            [], 255, "", "Host key verification failed for changed.example"
        ),
        host,
    )
    assert unknown.code == "host.key_unknown", (
        f"an unknown key for a host named 'changed' reported {unknown.code}"
    )
    # **And the real banner still gives the changed-key code**, so this did not simply disable it.
    real = classify_failure(
        subprocess.CompletedProcess(
            [], 255, "", "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"
        ),
        host,
    )
    assert real.code == "host.key_changed", real.code


def test_a_ssh_that_binds_LATE_is_not_accepted_early(tmp_path: Path) -> None:
    """**Opus round 4, item 1: my first fix for the race did not work, and this is the case.**

    ssh binds `-L` only AFTER key exchange and login - so on a real host, or a deliberately slow
    one, a fixed pause is not long enough. Opus's probe used a stand-in ssh failing its bind after
    1 s and `open()` still returned the squatter's port. **My own measurement used a localhost sshd
    where login is milliseconds**, which is exactly why I did not see it.

    The tunnel waits for **ssh's own announcement** now, and this drives a stand-in that announces
    late - so a fix that relies on elapsed time cannot pass it.

    **And ssh prints `Local forwarding listening` EVEN WHEN THE BIND FAILS** (measured on a real
    sshd), so the failure lines are checked on every pass rather than trusted in order.
    """
    import stat

    # **The host record's own folder**, which is `TEST_DIR`: `a_host()` builds its `SshHost` from
    # there, so pointing the tunnel at a different directory made it refuse for a KEY reason and
    # never reach the forwarding logic - measured, and the first version of this test passed for
    # that wrong reason ("DID NOT RAISE" once the folder was right).
    folder = TEST_DIR

    # **A stand-in ssh that behaves as the real one does, slowly**: it announces its forwarding
    # only after a delay, and the bind fails because a squatter already holds the port.
    # **A SQUATTER that is really listening on the port the tunnel will pick.** Without this the
    # mutant of the old timing-based fix also passes - measured - because `wait_for_port` fails
    # immediately and the timing never matters. Opus's case needs something there to be believed.
    import socket

    squatter = socket.socket()
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(1)
    stolen = int(squatter.getsockname()[1])

    stand_in = tmp_path / "ssh-late"
    # **The script text starts at column 0**, because a shebang that is indented is not a shebang -
    # measured: the file ran as "Exec format error", which is the kernel refusing to exec it.
    # **It announces "listening" and THEN fails the bind**, which is what real ssh does.
    stand_in.write_text(
        "#!/bin/sh\n"
        "sleep 1\n"
        f'echo "debug1: Local forwarding listening on 127.0.0.1 port {stolen}." >&2\n'
        f'echo "bind [127.0.0.1]:{stolen}: Address already in use" >&2\n'
        'echo "Could not request local forwarding." >&2\n'
        "exit 255\n"
    )
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IEXEC)

    # **`subprocess.Popen`, not `run_local`** - measured: the tunnel launches ssh with `Popen`
    # directly, and `run_local` is only for ssh-keyscan and ssh-keygen. My first version patched
    # `run_local` and therefore ran the REAL OpenSSH, which is how I found that out.
    import subprocess as _subprocess

    real_popen = _subprocess.Popen

    def patched(argv: list[str], **kwargs: object) -> object:
        swapped = [str(stand_in) if arg == "ssh" else arg for arg in argv]
        return real_popen(swapped, **kwargs)  # type: ignore[arg-type]

    _subprocess.Popen = patched  # type: ignore[assignment]
    try:
        # **The tunnel is made to pick the squatter's port**, which is the race exactly: ssh binds
        # a port this side chose, and something else got there first.
        import stokes.run.ssh as ssh_module

        real_free = ssh_module.free_local_port
        ssh_module.free_local_port = lambda: stolen
        try:
            from stokes.run.ssh import Tunnel

            tunnel = Tunnel(a_host(), 22, folder)
        finally:
            ssh_module.free_local_port = real_free
        try:
            with pytest.raises(SshError) as caught:
                tunnel.open(timeout=10)
            # **Refused, and it says what happened.** A tunnel that reported success here would
            # hand the caller a port ssh never bound - which is the whole defect.
            assert "forward" in str(caught.value).lower(), str(caught.value)
        finally:
            tunnel.close()
    finally:
        _subprocess.Popen = real_popen  # type: ignore[assignment]
