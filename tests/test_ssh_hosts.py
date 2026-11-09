"""The SSH host list and the ssh options it implies (T-159a).

**Two properties matter more than the rest**, and both are asserted here rather than intended:
that `hosts.json` can never carry a secret, and that no ssh invocation can be built without
strict host checking. The file is read at start-up and pasted into bug reports; the options
decide whether we run code on a machine we actually verified.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes.run.ssh_hosts import (
    KNOWN_HOSTS_FILE,
    HostError,
    SshHost,
    check_name,
    find_host,
    known_hosts_path,
    read_hosts,
    strict_options,
    write_hosts,
)


def a_host(**changes: object) -> SshHost:
    base = {
        "name": "buildbox",
        "host": "10.0.0.5",
        "user": "chandra",
        "port": 22,
        "key": "~/.ssh/id_ed25519",
        "python": "python3",
        "added": "2026-10-15",
    }
    base.update(changes)
    return SshHost(**base)  # type: ignore[arg-type]


def test_a_missing_file_is_no_hosts_not_an_error(tmp_path: Path) -> None:
    """A user who has never added a host has no file, and that is the ordinary state."""
    assert read_hosts(tmp_path / "hosts.json") == []


def test_a_record_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    write_hosts([a_host()], path)
    found = read_hosts(path)
    assert len(found) == 1
    assert found[0].target == "chandra@10.0.0.5"
    assert find_host("buildbox", path) == found[0]
    assert find_host("nope", path) is None


def test_a_secret_in_the_file_is_refused_not_ignored(tmp_path: Path) -> None:
    """**The property this module exists to protect.** `hosts.json` is read at start-up and
    copied into bug reports; a `password` key in it is a mistake that must not stand, and
    ignoring it would leave the user believing their password was being used."""
    for forbidden in ("password", "secret", "token"):
        path = tmp_path / "hosts.json"
        path.write_text(
            json.dumps(
                {"version": 1, "hosts": [{"name": "a", "host": "h", "user": "u", forbidden: "x"}]}
            )
        )
        with pytest.raises(HostError) as caught:
            read_hosts(path)
        assert forbidden in str(caught.value)
        assert "no secrets" in str(caught.value)


def test_every_ssh_invocation_verifies_the_host_key(tmp_path: Path) -> None:
    """**A call site cannot forget one of these**, because the list is built in one place. Losing
    `StrictHostKeyChecking=yes` would silently trust an unknown machine; losing `BatchMode=yes`
    would let ssh prompt, which in a server process is a hang."""
    options = strict_options(known_hosts_path(tmp_path), tmp_path / "id")
    joined = " ".join(options)
    assert "StrictHostKeyChecking=yes" in joined
    assert "BatchMode=yes" in joined
    assert "IdentitiesOnly=yes" in joined
    # **Quoted**, so a state folder with a space - which a Windows user name produces - reaches
    # ssh as one value (Opus round 3, item 6d).
    assert f'UserKnownHostsFile="{known_hosts_path(tmp_path)}"' in joined
    assert str(tmp_path / "id") in joined
    # And never the permissive spellings, in any argument.
    for banned in ("StrictHostKeyChecking=no", "accept-new", "=ask"):
        assert banned not in joined, banned


def test_known_hosts_is_ours_and_not_the_users(tmp_path: Path) -> None:
    """A change to the user's own `known_hosts` must not silently re-trust a machine for Stokes."""
    assert known_hosts_path(tmp_path).name == KNOWN_HOSTS_FILE
    assert known_hosts_path(tmp_path).parent == tmp_path


@pytest.mark.parametrize(
    "bad",
    ["BuildBox", "with space", "", "-leading", "a" * 65, "../escape", "a/b"],
)
def test_a_name_that_is_not_a_path_segment_is_refused(bad: str) -> None:
    """The name becomes a path segment, so it follows the preset rule."""
    with pytest.raises(HostError):
        check_name(bad)


def test_local_is_reserved() -> None:
    """`local` is this machine. A record claiming it would shadow the real one."""
    with pytest.raises(HostError) as caught:
        check_name("local")
    assert "reserved" in str(caught.value)


@pytest.mark.parametrize(
    "record",
    [
        {"name": "a", "host": "", "user": "u"},
        {"name": "a", "host": "h", "user": ""},
        {"name": "a", "host": "h", "user": "u", "port": 0},
        {"name": "a", "host": "h", "user": "u", "port": 70000},
        {"name": "a", "host": "h", "user": "u", "port": "22"},
        "not an object",
    ],
)
def test_a_bad_record_is_refused(tmp_path: Path, record: object) -> None:
    """Refused, never repaired: repairing means guessing which machine we are about to run on."""
    path = tmp_path / "hosts.json"
    path.write_text(json.dumps({"version": 1, "hosts": [record]}))
    with pytest.raises(HostError):
        read_hosts(path)


def test_the_file_is_written_owner_only(tmp_path: Path) -> None:
    """It sits beside `known_hosts` and the workspace; a group- or world-readable host list is a
    small leak of internal machine names."""
    path = tmp_path / "hosts.json"
    write_hosts([a_host()], path)
    assert path.stat().st_mode & 0o077 == 0, oct(path.stat().st_mode)
