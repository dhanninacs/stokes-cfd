"""The condition kinds an engine can write: ``boundary.kinds`` says which, the engine catalogue is
the one source, and every kind an engine declares really compiles (so a declaration cannot get
ahead of the writer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.compile.of14.boundaries import ALLOWED_PATCH_TYPES
from stokes.compile.snapshot import KINDS as WRITER_KINDS
from stokes.engines.catalogue import ENGINES, UNSUPPORTED_KIND_REASON, engine, kinds_for
from stokes.errors import StokesError
from stokes.physics.conditions.catalogue import KIND_IDS
from tests.selection_support import new_session


def kinds_of(rows: list[dict]) -> dict[str, dict]:  # type: ignore[type-arg]
    return {row["id"]: row for row in rows}


def test_every_engine_kind_is_one_the_writer_knows() -> None:
    for spec in ENGINES:
        assert set(spec.kinds) <= set(WRITER_KINDS) | set(KIND_IDS), spec.id
        # a declared kind must have a patch-type rule, or the writer would refuse it at solve time
        assert set(spec.kinds) <= set(ALLOWED_PATCH_TYPES), spec.id


def test_the_catalogue_kinds_an_engine_lacks_are_the_ones_that_fail_at_solve_time() -> None:
    spec = engine("of14.incompressibleFluid")
    assert spec.supports("velocity_inlet") and spec.supports("pressure_outlet")
    assert spec.supports("wall") and spec.supports("symmetry")
    assert spec.supports("mass_flow_inlet")
    assert not spec.supports("cyclic")
    assert not spec.supports("pressure_inlet")


def test_an_unknown_engine_falls_back_to_the_writer_list() -> None:
    assert kinds_for("") == WRITER_KINDS
    assert kinds_for("nope") == WRITER_KINDS
    assert kinds_for("of14.kOmegaSST") == engine("of14.incompressibleFluid").kinds


def test_kinds_with_an_engine_mark_what_it_cannot_write(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "journal.jsonl")
    rows = kinds_of(session.execute("boundary.kinds", engine="of14.incompressibleFluid"))
    assert list(rows) == list(KIND_IDS)  # unsupported kinds stay listed, to be shown disabled
    assert rows["velocity_inlet"]["supported"] is True
    assert rows["velocity_inlet"]["unsupported_reason"] is None
    assert rows["pressure_inlet"]["supported"] is False
    assert rows["pressure_inlet"]["unsupported_reason"] == UNSUPPORTED_KIND_REASON
    assert rows["mass_flow_inlet"]["supported"] is True
    assert json.loads(json.dumps(list(rows.values())))  # still plain JSON


def test_kinds_without_an_engine_or_project_filters_nothing(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "journal.jsonl")
    assert all(row["supported"] for row in session.execute("boundary.kinds"))


def test_kinds_default_to_the_open_projects_engine(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    rows = kinds_of(session.execute("boundary.kinds"))
    assert rows["pressure_inlet"]["supported"] is False
    assert rows["wall"]["supported"] is True


def test_an_unknown_engine_is_refused(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "journal.jsonl")
    with pytest.raises(StokesError) as caught:
        session.execute("boundary.kinds", engine="nope")
    assert getattr(caught.value, "code", "") == "solve.invalid"
