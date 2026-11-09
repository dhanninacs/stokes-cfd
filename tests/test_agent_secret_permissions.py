"""L4: the agent's secret is the whole authentication of the channel.

Four properties, each of which was measured as missing or wrong before this file existed:

1. the write **cannot be redirected through a symlink** - a planted one made the secret land
   in the file it pointed at;
2. the file is **0600** the moment it exists, never briefly readable;
3. a secret that is **already readable by someone else is refused**, so a leaked token cannot
   be used quietly;
4. the state folder is **0700**, because its permissions are part of the secret's protection.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from stokes_agent.daemon import (
    SECRET_FILE,
    check_secret_permissions,
    ensure_private_dir,
    read_secret,
    write_secret,
)

windows = pytest.mark.skipif(os.name == "nt", reason="mode bits are not meaningful on Windows")


def test_a_fresh_secret_is_0600_from_the_moment_it_exists(tmp_path: Path) -> None:
    """**Never briefly readable.** `write_text` + `chmod` would open a window in which the file
    exists at the process's umask, and this file authenticates every request."""
    target = write_secret(tmp_path, "s3cret")
    assert target.name == SECRET_FILE
    if os.name != "nt":  # pragma: no cover - depends on the platform
        assert stat.S_IMODE(target.stat().st_mode) == 0o600, oct(target.stat().st_mode)
    assert read_secret(tmp_path) == "s3cret"


@windows
def test_a_SYMLINK_at_the_temp_path_cannot_capture_the_secret(tmp_path: Path) -> None:
    """**Opus L4, measured.** `O_CREAT|O_TRUNC` without `O_NOFOLLOW` follows a symlink, so a local
    user who plants one at `.secret.tmp` **receives the secret** - measured: the file it pointed at
    came back reading `s3cret` before the fix.

    **Two layers guard it now, and mutating either alone leaves this passing**: the temp path's
    symlink is removed first, and the open carries `O_EXCL|O_NOFOLLOW`. That is deliberate
    defence in depth rather than redundancy for its own sake - the unlink handles a leftover
    from a crash, the flags handle a race - and the mutation that fails this test is removing
    **both**, which is the original code.
    """
    precious = tmp_path / "PRECIOUS"
    precious.write_text("keep me")
    (tmp_path / f".{SECRET_FILE}.tmp").symlink_to(precious)

    write_secret(tmp_path, "s3cret")
    # **The file the link pointed at is untouched**, which is the property.
    assert precious.read_text() == "keep me", "the secret was written through a symlink"
    # **And the secret itself still arrived**, at the real name.
    assert read_secret(tmp_path) == "s3cret"


@windows
def test_a_SYMLINK_at_the_secret_itself_is_replaced_not_followed(tmp_path: Path) -> None:
    """The rename replaces the link rather than writing through it, which is why the target needs no
    special handling - asserted so a future change to writing in place would be caught."""
    other = tmp_path / "OTHER"
    other.write_text("keep me too")
    (tmp_path / SECRET_FILE).symlink_to(other)

    write_secret(tmp_path, "s3cret")
    assert other.read_text() == "keep me too", "the secret was written through the target symlink"
    assert not (tmp_path / SECRET_FILE).is_symlink(), "the secret is still a symlink"
    assert read_secret(tmp_path) == "s3cret"


@windows
def test_a_secret_readable_by_OTHERS_is_refused(tmp_path: Path) -> None:
    """**A secret someone else can read has already leaked**, and the agent cannot tell whether
    that mattered. Returning it would let a session authenticate with a token anyone on the
    machine could have copied, and nothing would say so.

    `""` rather than an exception, because the detached agent calls this at start-up as
    `read_secret(...) or new_secret()` - so an empty answer **rotates** the secret, which is the
    right response to a leak and better than refusing to start.
    """
    write_secret(tmp_path, "leaked")
    (tmp_path / SECRET_FILE).chmod(0o644)

    assert check_secret_permissions(tmp_path) != "", "a 0644 secret was not reported"
    assert read_secret(tmp_path) == "", "an exposed secret was returned"
    # **And the diagnosis names the file and the mode**, so the user can act on it.
    problem = check_secret_permissions(tmp_path)
    assert "0644" in problem and SECRET_FILE in problem, problem


@windows
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o444, 0o666, 0o777])
def test_EVERY_group_or_other_bit_is_refused(tmp_path: Path, mode: int) -> None:
    """Any of the six bits, not just the world-readable one - a group-readable secret is just as
    available to a second person on a shared machine."""
    write_secret(tmp_path, "s")
    (tmp_path / SECRET_FILE).chmod(mode)
    assert read_secret(tmp_path) == "", f"mode {mode:04o} was accepted"


@windows
def test_a_0600_secret_is_returned(tmp_path: Path) -> None:
    """**The false-refusal half.** All of the above would pass if `read_secret` simply never retu
    anything."""
    write_secret(tmp_path, "correct-horse")
    (tmp_path / SECRET_FILE).chmod(0o600)
    assert read_secret(tmp_path) == "correct-horse"
    assert check_secret_permissions(tmp_path) == ""


@windows
def test_the_state_folder_is_0700_whether_new_or_narrowed(tmp_path: Path) -> None:
    """The folder's permissions are part of the secret's protection: 0755 lets anyone list what is
    there. **A folder that already exists is narrowed, not recreated** - the agent may be attaching
    to a directory a previous version made."""
    fresh = tmp_path / "fresh"
    ensure_private_dir(fresh)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700, oct(fresh.stat().st_mode)

    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o755)
    ensure_private_dir(loose)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700, oct(loose.stat().st_mode)
    # **And what was inside survives** - narrowing a folder must not disturb its contents.
    (loose / "kept.txt").write_text("still here")
    ensure_private_dir(loose)
    assert (loose / "kept.txt").read_text() == "still here"
