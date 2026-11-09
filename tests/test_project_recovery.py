"""Autosave's recovery copy: unsaved work kept beside the project file, never over it.

The mixing-elbow walkthrough (2026-11-08) lost a whole set-up to a server restart before Ctrl+S.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import ValidationError
from stokes.model.persist import PROJECT_FILE, RECOVERY_FILE

PARAMS = "/physics/parameters"


def opened(tmp_path: Path) -> tuple[Session, Path]:
    """A saved project with one parameter, open and clean."""
    folder = tmp_path / "duct.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    session.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    session.execute("project.save")
    return session, folder


def unsaved_edit(session: Session) -> None:
    session.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="5 [m/s]")


def value_of(session: Session) -> float:
    """The parameter's value in SI (m/s)."""
    node = session.require_project().find(f"{PARAMS}/U_in")
    assert node is not None
    return float(node.params["value"].value)


def test_autosave_writes_beside_the_file_only_when_there_is_unsaved_work(tmp_path: Path) -> None:
    session, folder = opened(tmp_path)
    before = (folder / PROJECT_FILE).read_bytes()
    assert session.execute("project.autosave") == {"saved": False}, "nothing unsaved, nothing kept"
    assert not (folder / RECOVERY_FILE).exists()
    unsaved_edit(session)
    assert session.execute("project.autosave") == {"saved": True}
    assert (folder / RECOVERY_FILE).is_file()
    assert (folder / PROJECT_FILE).read_bytes() == before, "the project file is never touched"
    info = session.execute("project.info")
    assert info["dirty"] is True, "an autosave is not a save"
    assert info["recovery"] is None, "this session's own copy is not offered back to it"


def test_a_restart_offers_the_unsaved_work_and_recover_restores_it(tmp_path: Path) -> None:
    session, folder = opened(tmp_path)
    unsaved_edit(session)
    session.execute("project.autosave")
    # The server goes away without a save or a close: a new session opens the same folder.
    again = Session()
    reply = again.execute("project.open", path=str(folder))
    assert reply["recovery"] is not None
    assert value_of(again) == pytest.approx(2.0), "open reads the project file, as saved"
    assert again.execute("project.info")["recovery"] == reply["recovery"]
    info = again.execute("project.recover")
    assert value_of(again) == pytest.approx(5.0)
    assert info["dirty"] is True, "recovered work is unsaved until it is saved"
    assert info["recovery"] is None, "the offer is taken"
    # Saving it makes the copy unnecessary, and the next open offers nothing.
    again.execute("project.save")
    assert not (folder / RECOVERY_FILE).exists()
    assert Session().execute("project.open", path=str(folder))["recovery"] is None


def test_discard_and_a_discarding_close_throw_the_copy_away(tmp_path: Path) -> None:
    session, folder = opened(tmp_path)
    unsaved_edit(session)
    session.execute("project.autosave")
    again = Session()
    again.execute("project.open", path=str(folder))
    assert again.execute("project.discard_recovery") == {"discarded": True}
    assert again.execute("project.open", path=str(folder))["recovery"] is None
    with pytest.raises(ValidationError):
        again.execute("project.recover")
    # Close without saving is the user discarding the changes: no copy survives it.
    unsaved_edit(again)
    again.execute("project.autosave")
    again.execute("project.close")
    assert not (folder / RECOVERY_FILE).exists()


def test_a_copy_older_than_the_file_is_not_offered(tmp_path: Path) -> None:
    session, folder = opened(tmp_path)
    unsaved_edit(session)
    session.execute("project.autosave")
    # A save that could not remove the copy (a scanner holding it, say) leaves an older one behind.
    past = time.time() - 60
    os.utime(folder / RECOVERY_FILE, (past, past))
    assert Session().execute("project.open", path=str(folder))["recovery"] is None


def test_save_as_takes_the_work_and_leaves_no_copy_behind(tmp_path: Path) -> None:
    session, folder = opened(tmp_path)
    unsaved_edit(session)
    session.execute("project.autosave")
    target = tmp_path / "copy.stokes"
    session.execute("project.save_as", path=str(target))
    assert not (folder / RECOVERY_FILE).exists()
    assert not (target / RECOVERY_FILE).exists()
