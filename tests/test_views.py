"""`view` nodes and the `view.*` commands (RESULTS-OVERHAUL brief R4): save, list, get, remove,
replace by name, undo, journal replay and the save/load round trip."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session, replay
from stokes.api.journal import Journal
from stokes.errors import StokesError, ValidationError
from stokes.model.persist import load_project

RECORD: dict[str, Any] = {
    "version": 1,
    "stokes": {
        "camera": {
            "target": [0.0, 0.0, 0.5],
            "dir": [0.0, 1.0, 0.0],
            "span": 0.12,
            "orthographic": False,
        },
        "field": {"name": "U_mag", "title": "|U|", "units": "m/s"},
        "range": {"min": 0.0, "max": 1.5, "locked": True},
        "map": "viridis",
        "reverse": False,
        "bands": 0,
        "looks": {"surface": False, "slices": [{"id": "s1", "plane": None}], "iso": None},
        "legend": {"rangeMode": "locked", "placement": "right", "title": "|U| (m/s)"},
    },
}


def _other(**changed: Any) -> dict[str, Any]:
    return {**RECORD, "stokes": {**RECORD["stokes"], **changed}}


def test_a_project_without_views_has_no_group_and_lists_none(session: Session) -> None:
    assert session.execute("view.list") == {"views": []}
    assert session.require_project().find("/views") is None


def test_save_list_get_remove(session: Session) -> None:
    saved = session.execute("view.save", name="Lengthwise velocity", record=RECORD)
    assert saved["name"] == "Lengthwise velocity" and saved["replaced"] is False
    assert saved["created"] != "" and saved["id"].startswith("n_")
    node = session.require_project().get(saved["id"])
    assert node.type == "view" and node.path == f"/views/v_{saved['id'][2:]}"

    listed = session.execute("view.list")["views"]
    assert listed == [
        {"id": saved["id"], "name": "Lengthwise velocity", "created": saved["created"]}
    ]
    # By id, by tree path and by the name the user typed.
    for ref in (saved["id"], node.path, "Lengthwise velocity"):
        assert session.execute("view.get", view=ref) == RECORD

    gone = session.execute("view.remove", view="Lengthwise velocity")
    assert gone == {"id": saved["id"], "name": "Lengthwise velocity"}
    assert session.execute("view.list") == {"views": []}
    with pytest.raises(StokesError) as caught:
        session.execute("view.get", view=saved["id"])
    assert caught.value.code == "view.unknown"


def test_the_record_is_kept_as_it_is_and_not_shared(session: Session) -> None:
    record = _other(map="magma")
    session.execute("view.save", name="a", record=record)
    record["stokes"]["map"] = "turbo"  # the caller changing its copy must not change the project
    assert session.execute("view.get", view="a")["stokes"]["map"] == "magma"


def test_saving_a_name_again_replaces_it_in_place(session: Session) -> None:
    first = session.execute("view.save", name="A", record=RECORD)
    session.execute("view.save", name="B", record=RECORD)
    again = session.execute("view.save", name="A", record=_other(map="coolwarm"))
    assert again["replaced"] is True and again["id"] == first["id"]
    listed = session.execute("view.list")["views"]
    assert [v["name"] for v in listed] == ["A", "B"]  # a replaced view keeps its place
    assert session.execute("view.get", view="A")["stokes"]["map"] == "coolwarm"


def test_a_name_is_trimmed_and_checked(session: Session) -> None:
    saved = session.execute("view.save", name="  padded  ", record=RECORD)
    assert saved["name"] == "padded"
    assert session.execute("view.save", name="padded", record=RECORD)["replaced"] is True
    for bad in ("", "   ", "x" * 81, "two\nlines"):
        with pytest.raises(StokesError) as caught:
            session.execute("view.save", name=bad, record=RECORD)
        assert caught.value.code == "view.name_invalid"
    with pytest.raises(ValidationError):
        session.execute("view.save", name="ok", record=["not", "an", "object"])  # type: ignore[arg-type]
    assert [v["name"] for v in session.execute("view.list")["views"]] == ["padded"]


def test_the_name_may_be_free_text(session: Session) -> None:
    name = "Δp at 4x / inlet (final)"
    session.execute("view.save", name=name, record=RECORD)
    assert session.execute("view.list")["views"][0]["name"] == name


def test_undo_of_a_save_removes_it_and_the_group_it_made(session: Session) -> None:
    session.execute("view.save", name="one", record=RECORD)
    assert session.require_project().find("/views") is not None
    session.execute("history.undo")
    assert session.execute("view.list") == {"views": []}
    assert session.require_project().find("/views") is None
    session.execute("history.redo")
    assert [v["name"] for v in session.execute("view.list")["views"]] == ["one"]


def test_undo_of_a_replace_gives_back_the_old_record(session: Session) -> None:
    session.execute("view.save", name="one", record=RECORD)
    before = session.execute("view.list")["views"][0]
    session.execute("view.save", name="one", record=_other(map="turbo"))
    session.execute("history.undo")
    assert session.execute("view.get", view="one")["stokes"]["map"] == "viridis"
    assert session.execute("view.list")["views"][0] == before


def test_undo_of_a_remove_brings_the_view_back(session: Session) -> None:
    session.execute("view.save", name="one", record=RECORD)
    session.execute("view.remove", view="one")
    session.execute("history.undo")
    assert session.execute("view.get", view="one") == RECORD


def test_the_views_group_cannot_be_renamed(session: Session) -> None:
    # The commands find `/views` by path: a rename would hide every view and make the next save
    # collide on the group's stable id (the R4 review measured both).
    session.execute("view.save", name="one", record=RECORD)
    with pytest.raises(StokesError):
        session.execute("node.rename", node="/views", name="myviews")
    assert [v["name"] for v in session.execute("view.list")["views"]] == ["one"]
    assert session.execute("view.save", name="two", record=RECORD)["replaced"] is False


def test_remove_refuses_what_is_not_a_view(session: Session) -> None:
    session.execute("view.save", name="one", record=RECORD)
    for ref in ("/views", "/geometry", "nowhere", "../../etc"):
        with pytest.raises(StokesError) as caught:
            session.execute("view.remove", view=ref)
        assert caught.value.code == "view.unknown"
    assert [v["name"] for v in session.execute("view.list")["views"]] == ["one"]


def test_an_oversized_record_is_refused_and_leaves_nothing(session: Session) -> None:
    big = _other(note="x" * 300_000)
    with pytest.raises(ValidationError):
        session.execute("view.save", name="big", record=big)
    assert session.execute("view.list") == {"views": []}
    assert session.require_project().find("/views") is None


def test_a_bad_node_id_is_a_validation_error(session: Session) -> None:
    with pytest.raises(ValidationError) as caught:
        session.execute("view.save", name="one", record=RECORD, node_id="not-an-id")
    assert caught.value.code == "validation"


def test_the_second_save_does_not_make_a_second_group(session: Session) -> None:
    session.execute("view.save", name="one", record=RECORD)
    session.execute("view.save", name="two", record=RECORD)
    groups = [n for n in session.require_project().root.children if n.name == "views"]
    assert len(groups) == 1
    session.execute("history.undo")  # only the second view goes; the group stays
    assert [v["name"] for v in session.execute("view.list")["views"]] == ["one"]


def test_the_record_survives_save_and_load(tmp_path: Path, session: Session) -> None:
    saved = session.execute("view.save", name="Figure 1", record=RECORD)
    session.execute("view.save", name="Figure 2", record=_other(bands=8))
    session.execute("project.save")
    assert session.project_path is not None
    loaded = load_project(session.project_path)
    node = loaded.get(saved["id"])
    assert node.type == "view"
    assert node.params["name"] == "Figure 1"
    assert node.params["record"] == RECORD
    assert node.params["created"] == saved["created"]
    reopened = Session()
    reopened.execute("project.open", path=str(session.project_path))
    assert [v["name"] for v in reopened.execute("view.list")["views"]] == ["Figure 1", "Figure 2"]
    assert reopened.execute("view.get", view="Figure 2")["stokes"]["bands"] == 8


def test_a_journal_replays_to_the_same_ids_and_records(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal.jsonl"
    first = Session(journal_path=journal_path)
    first.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    saved = first.execute("view.save", name="one", record=RECORD)
    first.execute("view.save", name="one", record=_other(map="turbo"))
    first.execute("view.save", name="two", record=RECORD)
    first.execute("view.remove", view="two")

    second = Session()
    second.execute("project.new", name="demo", path=str(tmp_path / "again"))
    replay(second, [r for r in Journal.read(journal_path) if r["command"].startswith("view.")])
    listed = second.execute("view.list")["views"]
    assert [v["name"] for v in listed] == ["one"]
    assert listed[0]["id"] == saved["id"]
    assert second.execute("view.get", view="one")["stokes"]["map"] == "turbo"
