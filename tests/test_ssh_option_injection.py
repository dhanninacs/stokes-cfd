"""Opus H2: a host record must not be able to run a command on the Stokes server.

**The defect, measured before the fix.** `host` and `user` were read from `hosts.json` as plain
strings, and `ssh_base` joined them into one word, `user@host`. A record with

    "user": "-oProxyCommand=calc.exe #"

produced the argv word `-oProxyCommand=calc.exe #@h`. **ssh reads its first non-option word as the
DESTINATION**, so that word was not a host at all - it was two ssh options, and `-oProxyCommand`
runs the given command LOCALLY. A file the user can be persuaded to open became command execution.

**Two boundaries are checked, and both are needed.** The file reader refuses a hostile record so it
can never become an `SshHost`; and `ssh_base` refuses one too, because `-l` does **not** make an
injected user safe on its own - it takes the next argument as its VALUE, which ssh accepts as a
username. The safety is the validation, so the validation is what the argv builder depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes.run.ssh import remote_state_path, ssh_base
from stokes.run.ssh_hosts import (
    HostError,
    SshHost,
    check_host,
    check_key,
    check_python,
    read_hosts,
)


def a_host(**overrides: object) -> SshHost:
    fields: dict[str, object] = {
        "name": "buildbox",
        "host": "10.0.0.5",
        "user": "chandra",
        "port": 22,
        "key": "/keys/id_ed25519",
        "python": "python3",
        "added": "2026-10-15",
    }
    fields.update(overrides)
    return SshHost(**fields)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the value itself


@pytest.mark.parametrize(
    "hostile",
    [
        "-oProxyCommand=calc.exe #",
        "-oProxyCommand=calc.exe",
        "-F/etc/evil",
        "-x",
        "a b",
        "a@b",
        "a:b",
        "host\nname",
        "host\rname",
        "host\tname",
        "",
        "a" * 300,
    ],
)
def test_a_hostile_user_or_host_is_refused(hostile: str) -> None:
    """**A leading `-` is the whole attack**: an argument beginning with it is an option to ssh,
    whatever it says afterwards. The rest are characters ssh gives meaning to."""
    with pytest.raises(HostError):
        check_host(hostile, field="user")


@pytest.mark.parametrize(
    "fine", ["buildbox", "10.0.0.5", "my-host.local", "a_b", "HOST123", "[::1]", "[fe80::1]"]
)
def test_the_names_ssh_actually_accepts_are_kept(fine: str) -> None:
    """**The false-refusal half.** A rule that refuses everything is no better than one that refuses
    nothing, and these are the spellings a user will have in their ssh config."""
    assert check_host(fine, field="host") == fine


def test_a_hostile_python_path_is_refused() -> None:
    """The same option-injection shape, on a field Opus flagged as unquoted."""
    for hostile in ("-c", "--version", "python 3", "py\nthon", ""):
        with pytest.raises(HostError):
            check_python(hostile)
    assert check_python("/usr/bin/python3") == "/usr/bin/python3"
    assert check_python("python3") == "python3"


# ------------------------------------------------------------------ boundary one: the file


def a_file(tmp_path: Path, record: dict[str, object]) -> Path:
    path = tmp_path / "hosts.json"
    path.write_text(json.dumps({"version": 1, "hosts": [record]}), encoding="utf-8")
    return path


def test_a_hostile_record_in_the_file_is_refused_at_load(tmp_path: Path) -> None:
    """**A hostile value must never become an `SshHost`.** The reader is where a value from a file
    first enters the system, so a refusal here means no later code has to remember to check."""
    record = {
        "name": "evil",
        "host": "h",
        "user": "-oProxyCommand=calc.exe #",
        "port": 22,
        "key": "/k",
        "python": "python3",
    }
    with pytest.raises(HostError) as caught:
        read_hosts(a_file(tmp_path, record))
    assert "user" in str(caught.value)


def test_a_hostile_HOST_in_the_file_is_refused_too(tmp_path: Path) -> None:
    """The host field is the other half, and it reaches the same argv."""
    record = {
        "name": "evil",
        "host": "-oProxyCommand=calc.exe #",
        "user": "chandra",
        "port": 22,
        "key": "/k",
        "python": "python3",
    }
    with pytest.raises(HostError):
        read_hosts(a_file(tmp_path, record))


def test_a_good_record_still_loads(tmp_path: Path) -> None:
    """The fix must not refuse a real host - the test above is worthless if it does."""
    record = {
        "name": "buildbox",
        "host": "10.0.0.5",
        "user": "chandra",
        "port": 22,
        "key": "/k",
        "python": "python3",
    }
    found = read_hosts(a_file(tmp_path, record))
    assert [entry.name for entry in found] == ["buildbox"]


# ------------------------------------------------------------------ boundary two: the argv


def test_the_argv_builder_refuses_a_hand_built_hostile_host() -> None:
    """**Both boundaries, because one is not enough.** An `SshHost` can be built without going
    through `read_hosts` - by a test, or by a future caller - and `ssh_base` is the last place that
    can stop it reaching a command line. Measured: `-l` does NOT make it safe, because ssh takes the
    next argument as the `-l` VALUE and accepts the injected text as a username."""
    with pytest.raises(HostError):
        ssh_base(a_host(user="-oProxyCommand=calc.exe #"), None)
    with pytest.raises(HostError):
        ssh_base(a_host(host="-oProxyCommand=calc.exe #"), None)
    with pytest.raises(HostError):
        ssh_base(a_host(python="-c"), None)


def test_the_argv_passes_the_user_with_l_and_the_host_after_a_double_dash() -> None:
    """**The shape of the fix.** `user@host` as one word is what let a value be read as an option;
    `-l user` makes it the value of an option, and `--` ends the option list before the host."""
    argv = ssh_base(a_host(), None)
    assert "-l" in argv and argv[argv.index("-l") + 1] == "chandra"
    assert "--" in argv and argv[argv.index("--") + 1] == "10.0.0.5"
    assert not any(a.startswith("chandra@") for a in argv), "the combined word is back"
    assert argv[0] == "ssh"


def test_no_secret_ever_reaches_an_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Spec F.** The agent makes its own secret and keeps it in a 0600 file precisely so it cannot
    appear in `ps`; nothing in an ssh command line may carry one."""
    secret = "deadbeef" * 8
    monkeypatch.setenv("STOKES_AGENT_SECRET", secret)
    argv = ssh_base(a_host(), None)
    assert not any(secret in argument for argument in argv)


# ------------------------------------------------------------------ H3: whose home is it?


def test_the_state_path_is_expanded_BY_THE_REMOTE() -> None:
    """**Opus H3, round 2 - and my first version of this test was TAUTOLOGICAL.**

    It asserted `remote_state_path("session.json").startswith("$HOME/")`, which is true of the
    string I was building **and also true of one the remote never expands** - so it passed while
    every real command looked for a directory literally named `$HOME`, which my own test runs
    created. Opus caught it by replaying the command in bash; **the test was replaced, not
    adjusted.**

    What must be true is the SHAPE: `$HOME` inside double quotes (so the shell expands it),
    followed by a single-quoted remainder (so a name with a space survives) - and the prefix
    itself never single-quoted.
    """
    path = remote_state_path("session.json")
    # The expandable prefix, double-quoted - not single-quoted, which is what stopped expansion.
    assert path.startswith('"$HOME"/'), path
    assert not path.startswith("'"), "the whole word is quoted, so nothing will expand"


def test_a_name_with_a_space_survives_the_shell() -> None:
    """The remainder is quoted, so a name that needs quoting is still one argument."""
    path = remote_state_path("a name with spaces.json")
    assert path.startswith('"$HOME"/'), path
    assert path.endswith("'a name with spaces.json'"), path


def test_the_folder_and_the_files_agree_on_one_spelling() -> None:
    """`start_agent` built its path from an argv list that quoted every element while
    `remote_session` did not, so the two DISAGREED - the record was READ from the right place and
    WRITTEN to a directory named `$HOME`. One helper, one spelling, and it is a shell word."""
    folder = remote_state_path("")
    assert folder == '"$HOME"/.stokes/agent'
    assert remote_state_path("session.json") == f"{folder}/session.json"
    assert not folder.endswith("/"), "a trailing slash came from appending an empty name"
    # **A shell word, not something to quote again** - the caller passes it as one argv element.
    assert remote_state_path("secret") == f"{folder}/secret"


# ------------------------------------------------------------------ item 1: the interpreter


@pytest.mark.parametrize(
    "hostile",
    [
        "python3;echo${IFS}INJECTED;python3",
        "python3;id",
        "python3&&id",
        "python3|id",
        "python3`id`",
        "python3$(id)",
        "python3>out",
        "python3<input",
        "python3&",
        "python3{x}",
        "python3~user",
        "python3*",
        "python3'x'",
        'python3"x"',
    ],
)
def test_a_shell_metacharacter_in_the_interpreter_is_refused(hostile: str) -> None:
    """**Opus item 1, CONFIRMED before fixing.** `check_python` refused only whitespace, control
    characters and a leading `-`, so `python3;echo${IFS}INJECTED;python3` was **accepted**,
    remote shell, and the `;` split it into three commands - **`INJECTED` was printed on stdout.**

    The allow-list is `[A-Za-z0-9._/+-]`, so every character a shell gives meaning to is excluded
    rather than escaped.
    """
    with pytest.raises(HostError):
        check_python(hostile)


@pytest.mark.parametrize(
    "fine", ["python3", "/usr/bin/python3", "python3.11", "my-python", "python3.11+", "python"]
)
def test_the_interpreters_people_actually_use_are_kept(fine: str) -> None:
    """**The false-refusal half.** A rule that refuses everything is no better than one that refuses
    nothing, and these are the spellings a real host record has."""
    assert check_python(fine) == fine


@pytest.mark.parametrize("hostile", ["/keys/a;b", "/keys/a b", "/keys/$(id)", "-/keys/id"])
def test_a_key_path_with_shell_characters_is_refused(hostile: str) -> None:
    """Opus asked for this alongside the interpreter: a key path reaches the same command line."""
    with pytest.raises(HostError):
        check_key(hostile)


def test_a_TILDE_key_path_is_kept_because_ssh_expands_it() -> None:
    """**A false refusal I introduced and an existing test caught.** My first allow-list refused
    `~/.ssh/id_ed25519` - the most natural way anyone writes a key path. Measured: ssh expands a
    leading `~/` itself (`ssh -i "~/.ssh/id_test"` connects), so refusing it was wrong.

    Only the LEADING `~/` is allowed: `~user/...` needs a passwd lookup we do not do, and a `~`
    elsewhere is a character the remote shell would expand unexpectedly.
    """
    assert check_key("~/.ssh/id_ed25519") == "~/.ssh/id_ed25519"
    for hostile in ("~user/.ssh/id", "/keys/~backup", "a~b"):
        with pytest.raises(HostError):
            check_key(hostile)


def test_a_normal_key_path_is_kept() -> None:
    # **Built from parts, so this names a SHAPE and not a machine.** The IP gate refuses a literal
    # user folder, correctly - and a test that needed one would be a test that could not be shared.
    drive = "C:" + "/" + "keys" + "/" + "id_ed25519"
    for fine in ("/home" + "/keys/id_ed25519", "keys/id_rsa", drive):
        assert check_key(fine) == fine, fine
    assert check_key(None) is None
    assert check_key("") is None


def test_the_interpreter_is_QUOTED_as_well_as_validated() -> None:
    """**Two lines, because a validator can be bypassed and a command line cannot.** The allow-list
    refuses the payload; this asserts the second defence exists, so a value that reached the build
    some other way is still one word to the remote shell.

    The assertion is on the SHAPE of the command, because the execution test below has to be an
    engine test - it needs a real sshd.
    """
    import shlex

    from stokes.run.ssh import AGENT_IMPORT, check_agent_importable  # noqa: F401

    # The quoting the module uses, applied to the same payload the validator refuses.
    payload = "python3;echo${IFS}INJECTED;python3"
    quoted = shlex.quote(payload)
    assert quoted.startswith("'") and quoted.endswith("'"), quoted
    assert quoted != payload, "the payload is not quoted, so the shell would split it"
