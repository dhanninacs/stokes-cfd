"""`case.file_read`, `case.file_write`, `case.file_revert`: the in-app text editor's commands.

Synthetic cases only. The copy never changes; the text lives in the node's `file_edits`, is linted
by the external profile when saved, and is what stages for a run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError

from .synthetic import GUARDS, make_case
from .test_case_settings import CONTROL, FILES, _imported

NODE = "/cases/cavity"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(folder))
    return folder


@pytest.fixture
def tutorials(tmp_path: Path) -> Path:
    root = tmp_path / "tutorials"
    make_case(
        root,
        "incompressibleFluid/cavity",
        CONTROL,
        allrun=GUARDS
        + "runApplication blockMesh"
        + chr(10)
        + "runApplication $(getApplication)"
        + chr(10),
        files=FILES,
    )
    return root


def test_read_gives_what_stages_now_and_says_it_is_not_edited(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
) -> None:
    session = _imported(tmp_path, tutorials)
    reply = session.execute("case.file_read", node=NODE, file="system/controlDict")
    assert reply["edited"] is False
    assert "endTime 0.5;" in reply["text"]


def test_a_saved_text_is_read_back_and_undoes_as_one_step(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
) -> None:
    session = _imported(tmp_path, tutorials)
    edited = CONTROL.replace("endTime 0.5;", "endTime 2;")
    session.execute("case.file_write", node=NODE, file="system/controlDict", text=edited)
    reply = session.execute("case.file_read", node=NODE, file="system/controlDict")
    assert reply["edited"] is True and "endTime 2;" in reply["text"]
    session.execute("history.undo")
    assert (
        session.execute("case.file_read", node=NODE, file="system/controlDict")["edited"] is False
    )


def test_saving_text_drops_the_key_edits_of_that_file(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
) -> None:
    session = _imported(tmp_path, tutorials)
    session.execute(
        "case.set", node=NODE, file="system/controlDict", path=["PISO", "nCorrectors"], value=3
    )
    session.execute("case.file_write", node=NODE, file="system/controlDict", text=CONTROL)
    params = session.require_project().find(NODE).params
    assert params["overrides"] == []
    assert [item["file"] for item in params["file_edits"]] == ["system/controlDict"]


def test_revert_drops_the_text(tmp_path: Path, tutorials: Path, home: Path) -> None:
    session = _imported(tmp_path, tutorials)
    session.execute("case.file_write", node=NODE, file="system/controlDict", text=CONTROL)
    assert session.execute("case.file_revert", node=NODE, file="system/controlDict")["reverted"]
    assert not session.execute("case.file_revert", node=NODE, file="system/controlDict")["reverted"]


def test_code_in_the_text_is_refused_by_the_external_lint(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
) -> None:
    session = _imported(tmp_path, tutorials)
    evil = CONTROL + "#codeStream\n{\n    code #{ Info << 1; #};\n}\n"
    with pytest.raises(StokesError) as caught:
        session.execute("case.file_write", node=NODE, file="system/controlDict", text=evil)
    assert caught.value.code == "case.file_lint"
    assert session.require_project().find(NODE).params["file_edits"] == []


@pytest.mark.parametrize(
    "file", ["../escape", "/etc/passwd", "system/../../x", "a" + chr(92) + "b"]
)
def test_a_path_outside_the_case_is_refused(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
    file: str,
) -> None:
    session = _imported(tmp_path, tutorials)
    with pytest.raises(StokesError) as caught:
        session.execute("case.file_write", node=NODE, file=file, text="x")
    assert caught.value.code == "case.override_file"


def test_a_file_the_case_lacks_is_refused(tmp_path: Path, tutorials: Path, home: Path) -> None:
    session = _imported(tmp_path, tutorials)
    with pytest.raises(StokesError) as caught:
        session.execute("case.file_write", node=NODE, file="system/nothere", text=CONTROL)
    assert caught.value.code == "case.file_missing"


def test_a_too_large_or_binary_text_is_refused(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
) -> None:
    session = _imported(tmp_path, tutorials)
    for text in ("x" * (256 * 1024 + 1), "a\x00b"):
        with pytest.raises(StokesError) as caught:
            session.execute("case.file_write", node=NODE, file="system/controlDict", text=text)
        assert caught.value.code == "case.file_too_large"


def test_the_text_is_what_a_run_stages_and_moves_the_basis(
    tmp_path: Path,
    tutorials: Path,
    home: Path,
) -> None:
    from stokes.run.external import basis

    session = _imported(tmp_path, tutorials)
    node = session.require_project().find(NODE)
    before = basis(node)
    session.execute(
        "case.file_write",
        node=NODE,
        file="system/controlDict",
        text=CONTROL.replace("endTime 0.5;", "endTime 9;"),
    )
    assert basis(session.require_project().find(NODE)) != before
    session.execute("case.file_revert", node=NODE, file="system/controlDict")
    assert basis(session.require_project().find(NODE)) == before


def _unused(_: Session) -> None:  # keeps the Session import used for type readers
    return None
