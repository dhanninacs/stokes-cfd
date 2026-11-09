"""Opus L2: the user's own ssh config must not decide what our ssh does.

**The defect.** ssh reads its configuration FIRST and the command line after, so an `-o` we
pass wins over a config that says otherwise - **but only for settings we mention.** Everything
else is the config's to choose. Measured: a config containing

    Host *
        ProxyCommand sh -c 'echo PROXYCOMMAND-RAN >&2; exec nc %h %p'

made the pre-L2 argument list **run that command**. A `ProxyCommand` is arbitrary code
execution on the machine running the app, arriving through a file the user may not know exists.

**Two defences, and both are asserted.**
`-F none` stops the file being read at all; the `-o` list pins every setting a config could
otherwise choose, because `-F none` is not available everywhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from stokes.run.ssh import ssh_base
from stokes.run.ssh_hosts import SshHost, global_known_hosts, strict_options

MARKER = "PROXYCOMMAND-RAN"


def a_host(tmp_path: Path) -> SshHost:
    return SshHost(
        name="isolated",
        host="127.0.0.1",
        user="chandra",
        port=22,
        key=str(tmp_path / "id_test"),
        python="python3",
        added="test",
    )


def test_the_option_list_pins_everything_a_config_could_choose() -> None:
    """**The list is the defence, so the list is asserted.** Each of these is a setting that, left
    unset, the user's config decides for us - and `ProxyCommand` and `KnownHostsCommand` both RUN a
    program."""
    options = strict_options(Path("/tmp/known_hosts"), Path("/tmp/key"))
    joined = " ".join(options)
    for required in (
        "StrictHostKeyChecking=yes",
        'UserKnownHostsFile="/tmp/known_hosts"',
        "GlobalKnownHostsFile=",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ControlMaster=no",
        "ControlPath=none",
        # **`KnownHostsCommand` is deliberately NOT in this list** (Opus recheck item 6a): it exists
        # only from OpenSSH 8.5, and an ssh that does not know an option name refuses EVERY
        # connection. `-F none` already stops a config setting it. See
        # `tests/test_ssh_host_trust.py` for the assertion that it stays absent.
        "IdentitiesOnly=yes",
        "ForwardAgent=no",
        "BatchMode=yes",
    ):
        assert required in joined, f"the config could decide {required!r}"


def test_the_system_known_hosts_file_is_not_consulted() -> None:
    """A host trusted in `/etc/ssh/ssh_known_hosts` is trusted by the machine's administrator -
    a different statement from "this app's user has checked this fingerprint"."""
    assert global_known_hosts() in ("/dev/null", "NUL"), global_known_hosts()
    joined = " ".join(strict_options(Path("/tmp/k"), None))
    assert f"GlobalKnownHostsFile={global_known_hosts()}" in joined


def test_no_configuration_file_is_read_at_all() -> None:
    """**`-F none` FIRST**, because "read no configuration file" only means that when it comes
    the other arguments."""
    argv = ssh_base(a_host(Path("/tmp")), None)
    assert argv[0] == "ssh"
    assert argv[1:3] == ["-F", "none"], argv[:4]


@pytest.mark.engine
def test_a_configs_proxycommand_cannot_run(tmp_path: Path) -> None:
    """**The defect itself, end to end against a real sshd.** A config that WOULD run a command is
    read by the pre-L2 arguments and ignored by ours.

    **Why this is an engine test.** It needs a real ssh and a real host to connect to; the
    above assert the ARGUMENTS, which is a different claim from "the command does not run".

    **What I could NOT do, and it cost three probes.** I first tried to make ssh read the
    config from a redirected `HOME`, and ssh does not use `$HOME` for this - it resolves the
    home from the passwd entry. Every probe passed and proved nothing, because no config was
    being read at all. `-F` is the mechanism I can control, and it is what this uses.
    """
    test_dir = Path.home() / ".stokes-test-ssh"
    if not (test_dir / "id_test").exists():
        pytest.skip("no throwaway test key; run tests/engine/test_ssh_runner_engine.py first")
    key = tmp_path / "id_test"
    shutil.copy(test_dir / "id_test", key)
    key.chmod(0o600)
    known = tmp_path / "known_hosts"
    known.write_text(
        subprocess.run(
            ["ssh-keyscan", "-t", "ed25519", "127.0.0.1"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    )
    config = tmp_path / "config"
    config.write_text(f"Host *\n    ProxyCommand sh -c 'echo {MARKER} >&2; exec nc %h %p'\n")

    base = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known}",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(key),
        "-p",
        "22",
        "-l",
        "chandra",
        "--",
        "127.0.0.1",
        "true",
    ]
    host = SshHost(
        name="isolated",
        host="127.0.0.1",
        user="chandra",
        port=22,
        key=str(key),
        python="python3",
        added="test",
    )
    # **The pre-L2 shape, with the config read** - this is the vulnerable case, and it must SHOW the
    # marker. Without this half the test could pass because the config was never read at all, which
    # is exactly the trap I fell into three times.
    vulnerable = subprocess.run(
        ["ssh", "-F", str(config), *base], capture_output=True, text=True, timeout=30, check=False
    )
    assert MARKER in vulnerable.stderr + vulnerable.stdout, (
        "the config was not read, so this test proves nothing about the fix"
    )

    # **Ours**, which must not run it.
    ours = subprocess.run(
        [*ssh_base(host, tmp_path), *base[base.index("--") + 1 :]],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert MARKER not in ours.stderr + ours.stdout, (
        "a ProxyCommand from a config file ran: the config is not isolated"
    )
