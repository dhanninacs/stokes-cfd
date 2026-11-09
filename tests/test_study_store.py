"""Design-table storage (stokes.explore.study_store and study_table, ADR-0016 point 9): the files
under <name>.stokes/studies/<study_id>/, lazy loading, project open, save, save as and close, and
the validation of everything read back."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import NoProject
from stokes.explore.definition import Column
from stokes.explore.evaluate import PointResult
from stokes.explore.study_store import StudyStore
from stokes.explore.study_table import (
    CSV_FILE,
    POINTS_FILE,
    TABLE_FILE,
    PointRow,
    StudyTable,
    StudyTableError,
    TableMeta,
    csv_text,
    read_table,
    table_dir,
    write_table,
)
from tests.test_study_support import SWEEP, define_pipe, dp, make_sweep


def evaluated(session: Session) -> str:
    """The pipe sweep, generated and evaluated; returns the study id."""
    define_pipe(session)
    make_sweep(session)
    study = str(session.execute("study.generate", study=SWEEP)["study"])
    session.execute("study.update", study=SWEEP)
    if session.project_path is not None:
        session.execute("project.save")  # the tree; the table files are already written
    return study


def files(folder: Path, study: str) -> Path:
    return folder / "studies" / study


def test_tables_are_written_on_generate_and_update(session: Session) -> None:
    study = evaluated(session)
    assert session.project_path is not None
    directory = files(session.project_path, study)
    lines = (directory / POINTS_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["status"] for line in lines] == ["done"] * 3
    assert json.loads(lines[2])["outputs"]["dp_dyn"] == pytest.approx(dp(3.0))
    meta = json.loads((directory / TABLE_FILE).read_text(encoding="utf-8"))
    assert meta["version"] == 1 and meta["engine"] == "function" and meta["count"] == 3
    assert [c["name"] for c in meta["responses"]] == ["dp_dyn", "Q"]
    mirror = list(csv.reader(io.StringIO((directory / CSV_FILE).read_text(encoding="utf-8"))))
    assert mirror[0][:4] == ["index", "status", "U_in [m/s]", "dp_dyn [Pa]"] and len(mirror) == 4


def test_a_table_survives_save_and_open(session: Session) -> None:
    study = evaluated(session)
    expected = session.execute("study.results", study=SWEEP)
    fresh = Session()
    fresh.execute("project.open", path=str(session.project_path))
    store = fresh.extensions.get("study")
    assert store is None  # nothing loads until a study command needs it
    reopened = fresh.execute("study.results", study=SWEEP)
    assert reopened["points"] == expected["points"] and reopened["status"] == "done"
    assert reopened["generated"] == expected["generated"]
    assert fresh.execute("node.get", node=SWEEP)["params"]["status"] == "done"
    assert fresh.extensions["study"].is_loaded(study)
    fresh.execute("study.update", study=SWEEP, points=[0])  # carries on with the loaded table
    assert fresh.execute("study.results", study=SWEEP)["counts"]["done"] == 3


def test_a_project_without_a_folder_keeps_tables_until_it_is_saved(tmp_path: Path) -> None:
    session = Session()
    session.execute("project.new", name="draft")
    study = evaluated(session)
    assert not list(tmp_path.iterdir())
    session.execute("project.save", path=str(tmp_path / "draft"))
    directory = files(tmp_path / "draft.stokes", study)
    assert len((directory / POINTS_FILE).read_text(encoding="utf-8").splitlines()) == 3
    assert (directory / CSV_FILE).is_file() and (directory / TABLE_FILE).is_file()


def test_save_as_carries_every_table(session: Session, tmp_path: Path) -> None:
    study = evaluated(session)
    reopened = Session()
    reopened.execute("project.open", path=str(session.project_path))
    reopened.execute("study.cancel")  # the store exists; no table is loaded yet
    assert not reopened.extensions["study"].is_loaded(study)
    reopened.execute("project.save", path=str(tmp_path / "copy"))
    copied = files(tmp_path / "copy.stokes", study)
    assert (copied / POINTS_FILE).read_bytes() == (
        files(session.project_path or Path(), study) / POINTS_FILE
    ).read_bytes()
    third = Session()
    third.execute("project.open", path=str(tmp_path / "copy"))
    assert third.execute("study.results", study=SWEEP)["counts"]["done"] == 3


def test_opening_or_closing_a_project_drops_its_tables(session: Session, tmp_path: Path) -> None:
    study = evaluated(session)
    store = session.extensions["study"]
    assert isinstance(store, StudyStore) and store.is_loaded(study)
    folder = session.project_path
    session.execute("project.close")
    assert not store.is_loaded(study) and store.folder is None
    with pytest.raises(NoProject):
        session.execute("study.results", study=SWEEP)
    session.execute("project.open", path=str(folder))
    assert store.folder == folder and not store.is_loaded(study)
    assert session.execute("study.results", study=SWEEP)["counts"]["done"] == 3
    # **A new project has a folder**, so the store moves to it rather than to `None`. The point of
    # this step is that the PREVIOUS project's tables are dropped, which still holds - and the f
    # is asserted against what the store reports so the test states the rule without inventing a
    session.execute("project.new", name="other", path=str(tmp_path / "other"))
    assert store.folder == session.project_path
    assert not store.is_loaded(study)


def test_unreadable_tables_raise_typed_errors_and_generate_replaces_them(session: Session) -> None:
    study = evaluated(session)
    folder = session.project_path
    assert folder is not None
    points = files(folder, study) / POINTS_FILE
    good = points.read_text(encoding="utf-8").splitlines()
    row = json.loads(good[0])
    broken = {
        "not json": "{",
        "unknown key": json.dumps({**row, "colour": "red"}),
        "bad status": json.dumps({**row, "status": "finished"}),
        "not finite": json.dumps({**row, "outputs": {"dp_dyn": "NaN"}}).replace('"NaN"', "NaN"),
        "repeated": good[0] + "\n" + good[0],
        "bad index": json.dumps({**row, "index": True}),
    }
    for case, text in broken.items():
        points.write_text(text + "\n", encoding="utf-8")
        reader = Session()
        reader.execute("project.open", path=str(folder))
        with pytest.raises(StudyTableError) as caught:
            reader.execute("study.results", study=SWEEP)
        assert caught.value.code == "explore.study_table", case
        assert "line" in caught.value.message, case
    reader.execute("study.generate", study=SWEEP)  # replaces the table without reading it
    assert reader.execute("study.results", study=SWEEP)["counts"]["pending"] == 3
    (files(folder, study) / TABLE_FILE).write_text('{"version": 99}', encoding="utf-8")
    newer = Session()
    newer.execute("project.open", path=str(folder))
    with pytest.raises(StudyTableError, match="format 99"):
        newer.execute("study.results", study=SWEEP)


def test_a_point_left_running_reads_as_pending(tmp_path: Path) -> None:
    directory = tmp_path / "studies" / "n_0000abcd"
    row = PointRow(0, {"U_in": 1.0}, status="running")
    write_table(directory, StudyTable(TableMeta(), [row]))
    table, existed = read_table(directory)
    assert existed and table.rows[0].status == "pending"
    assert read_table(tmp_path / "studies" / "n_ffff0000") == (StudyTable(), False)


def test_rows_round_trip_and_are_validated() -> None:
    row = PointRow(4, {"U_in": 2.0}, {"dp_dyn": 1996.0}, "done", None, True, None)
    assert PointRow.from_json(row.to_json()) == row
    assert (
        PointRow.from_json({"index": 1, "inputs": {"x": 1}, "outputs": {"y": None}}).outputs == {}
    )
    for bad in (
        [],
        {"index": -1, "inputs": {}},
        {"index": 0, "inputs": {"x": "1"}},
        {"index": 0, "inputs": {}, "retain": "yes"},
        {"index": 0, "inputs": {}, "run": 5},
    ):
        with pytest.raises(StudyTableError):
            PointRow.from_json(bad)


def test_csv_cells_cannot_become_formulas() -> None:
    rows = [PointRow(0, {"x": 1.0}, status="failed", error="=HYPERLINK(1)", run="+run")]
    text = csv_text(
        rows, [Column("x", "length", "m", "x")], [Column("y", "dimensionless", "", "y")]
    )
    table = list(csv.reader(io.StringIO(text)))
    assert table[0] == ["index", "status", "x [m]", "y", "retain", "run", "error"]
    assert table[1] == ["0", "failed", "1.0", "", "false", "'+run", "'=HYPERLINK(1)"]


def test_table_folders_come_from_node_ids_only(tmp_path: Path) -> None:
    assert table_dir(tmp_path, "n_0123abcd") == (tmp_path / "studies" / "n_0123abcd").resolve()
    for bad in ("../escape", "n_0123abcd/..", "studies", ""):
        with pytest.raises(StudyTableError):
            table_dir(tmp_path, bad)


def test_the_store_records_results_and_closes_updates(tmp_path: Path) -> None:
    store = StudyStore(tmp_path)
    meta = TableMeta(variables=[Column("x", "length", "m", "x")])
    store.replace("n_0000aaaa", meta, [PointRow(0, {"x": 1.0}), PointRow(1, {"x": 2.0})])
    token = store.begin("n_0000aaaa")
    assert store.status("n_0000aaaa") == "running"
    store.start_point("n_0000aaaa", 0)
    store.start_point("n_0000aaaa", 1)
    store.record("n_0000aaaa", PointResult(0, "failed", error="x" * 5000))
    table = store.end("n_0000aaaa", meta, "function")
    assert [r.status for r in table.rows] == ["failed", "pending"]  # 1 started, never finished
    assert len(table.rows[0].error or "") == 2000 and not token.cancelled
    assert table.status() == "failed" and table.meta.engine == "function"
    assert read_table(files(tmp_path, "n_0000aaaa"))[0].rows == table.rows
