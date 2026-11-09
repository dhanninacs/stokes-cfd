"""The M1 gate: the ten-file test set through import, check, repair, enclosure or fluid
extraction, tags and STL export, and the journal replayed through the CLI (ADR-0020 decision 9;
T-034).

The set is :mod:`tests.m1_fixtures`: ten *synthetic* STEP files (plus the open surface again as
IGES), generated from OCCT shapes into ``tmp_path`` at test time. Nothing binary enters the
repository, as CLAUDE.md §6 and §8 require, and no third-party licence is involved.

Every case runs the same gate::

    geometry.import -> geometry.check -> (the repairs the file needs) -> enclosure or
    extract_fluid -> a tag over the fluid domain -> geometry.export_stl

and then replays the session's journal into a *fresh* project through the real CLI path,
``stokes replay`` (``stokes.cli.main.run``), and compares the two STL files byte for byte. The
timings are written to ``tests/perf/results.jsonl`` the way the perf harness does (a bare
``{"name", "ms"}`` line, as ``tests/perf/test_setup_check_perf.py`` writes): they are measurements,
not budgets — the brief's own budget is the owner's interactive run, which no test can time.

The whole module is marked ``slow`` (the budget in ``pyproject.toml``: longer than ten seconds) and
needs OCP; it skips itself without it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Journal, Session
from stokes.cli.main import run as cli_run
from stokes.geometry import worker
from stokes.geometry.store import GeometryStore
from tests import m1_fixtures as m1
from tests.operations_support import create_and_wait
from tests.perf.harness import RESULTS

pytestmark = pytest.mark.slow

DOMAIN_TAG = "fluid_domain"
ENCLOSURE_TAGS = ("inlet", "outlet", "walls", "far_field")


@dataclass
class Gate:
    """What one file's run through the gate produced, for the assertions and the report."""

    label: str
    parts: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    repairs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    domain: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    stl: bytes = b""
    triangles: int = 0
    flow_ms: float = 0.0
    replay_ms: float = 0.0


@pytest.fixture(scope="module")
def sources(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The whole set, built once for the module, into a folder of its own."""
    folder = tmp_path_factory.mktemp("m1-set")
    return {fixture.file_name: path for fixture, path in m1.write_set(folder)}


@pytest.fixture(scope="module", autouse=True)
def pool() -> Iterator[None]:
    """One worker kept ahead for the imports and the operations, as the app does."""
    worker.prewarm(2)
    yield
    worker.shutdown_pool()


def checked(check: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-part items of a ``geometry.check`` result, group or part alike."""
    items = check.get("parts")
    return list(items) if isinstance(items, list) else [check]


def finding_codes(check: dict[str, Any]) -> set[str]:
    """Every finding code the check reported, over the parts it covered."""
    return {
        str(finding["code"]) for item in checked(check) for finding in item.get("findings") or []
    }


def tag_names(session: Session) -> list[str]:
    return [str(tag["name"]) for tag in session.execute("tag.list")]


def run_gate(fixture: m1.Fixture, source: Path, folder: Path) -> Gate:
    """Import, check, repair, make the fluid domain, tag it and export it.

    Returns what the assertions and the replay need; the caller closes the store.
    """
    started = time.perf_counter()
    gate = Gate(label=fixture.file_name)
    session = Session(journal_path=folder / "demo.jsonl")
    session.execute("project.new", name=fixture.name, path=str(folder / "demo"))
    try:
        imported = session.execute(
            "geometry.import", path=str(source), name=fixture.name, wait=True
        )
        target = str(imported["id"])
        gate.checks.append(session.execute("geometry.check", part=target, wait=True))
        gate.parts = [item["part"] for item in checked(gate.checks[0])]

        domain_input = target
        caps: str | None = None
        for op_type in fixture.repair:
            info = create_and_wait(session, op_type, [domain_input])
            assert info["status"] == "ok", (op_type, info.get("error"))
            gate.repairs.append((op_type, dict(info)))
            domain_input = str(info["outputs"][0])
            created = list(session.require_project().get(info["id"]).meta["created_tags"])
            if created:
                caps = str(created[0])
        params = dict(fixture.domain_params)
        if fixture.caps and caps is not None:
            params.update(caps=caps, seed=[0.0, 0.0, 0.0])
        info = create_and_wait(session, fixture.domain, [domain_input], params)
        assert info["status"] == "ok", (fixture.domain, info.get("error"))
        gate.domain = dict(info)
        gate.outputs = [str(output) for output in info["outputs"]]
        # The fluid domain as the user names it: one tag over every output of the operation.
        session.execute(
            "tag.create",
            name=DOMAIN_TAG,
            members=[{"part": output, "faces": "all"} for output in gate.outputs],
        )
        members = session.execute("tag.members", tag=DOMAIN_TAG)["members"]
        assert [member["part"] for member in members] == gate.outputs
        gate.tags = tag_names(session)
        exported = session.execute("geometry.export_stl", path="stl", tags=[DOMAIN_TAG])
        assert exported["solids"] == 1, exported
        gate.triangles = int(exported["triangles"])
        gate.stl = Path(exported["files"][0]["path"]).read_bytes()
        gate.flow_ms = (time.perf_counter() - started) * 1000
        return gate
    finally:
        GeometryStore.of(session).close()


def replay_stl(fixture: m1.Fixture, folder: Path, fresh: Path) -> tuple[bytes, float]:
    """Replay the journal into a fresh project through ``stokes replay`` and export again."""
    started = time.perf_counter()
    records = Journal.read(folder / "demo.jsonl")
    assert next(record["command"] for record in records) == "project.new"
    # The fresh project is opened by the CLI, so the journal's own project.new is left out: every
    # other record replays as it was written, through the command the CLI runs.
    kept = [record for record in records if record["command"] != "project.new"]
    assert len(kept) >= 3, [record["command"] for record in kept]
    journal = folder / "replay.jsonl"
    journal.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in kept), encoding="utf-8"
    )
    assert cli_run(["new", str(fresh), "--name", fixture.name]) == 0
    assert cli_run(["replay", str(fresh), str(journal)]) == 0
    session = Session()
    try:
        session.execute("project.open", path=str(fresh))
        again = session.execute("geometry.export_stl", path="stl", tags=[DOMAIN_TAG])
        assert again["solids"] == 1, again
        return Path(again["files"][0]["path"]).read_bytes(), (time.perf_counter() - started) * 1000
    finally:
        GeometryStore.of(session).close()


def record(label: str, ms: float) -> None:
    """One measurement in ``results.jsonl``, as the perf tests write them (no budget)."""
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"name": label, "ms": round(ms, 3)}) + "\n")


@pytest.mark.parametrize("file_name", [fixture.file_name for fixture in m1.FIXTURES])
def test_the_gate_on_one_file(file_name: str, sources: dict[str, Path], tmp_path: Path) -> None:
    fixture = next(f for f in m1.FIXTURES if f.file_name == file_name)
    folder = tmp_path / "run"
    folder.mkdir()
    gate = run_gate(fixture, sources[file_name], folder)

    # ---- the import and the check
    assert gate.parts, "the import made no part"
    for check in gate.checks:
        for item in checked(check):
            assert item["checked"] is True, item
            assert item["stats"]["triangles"] > 0, item
            assert item["stats"]["closed"] is fixture.closed_before, item
    assert set(fixture.finds) <= finding_codes(gate.checks[0]), gate.checks[0]

    # ---- each repair did what it was run for
    for op_type, info in gate.repairs:
        report = info["report"]
        assert report["check_after"]["valid"] is True, report
        match op_type:
            case "cap":
                assert report["closed"] is True
                assert report["left_open"] == 0
                assert report["check_after"]["closed"] is True
            case "slivers":
                assert report["strip_faces"] + report["spot_faces"] >= 1, report
                assert report["faces_after"] < report["faces_before"]
            case "sew":
                assert report["free_edges_after"] < report["free_edges_before"], report
            case other:  # pragma: no cover - the set only repairs with the three above
                raise AssertionError(f"unknown repair {other}")

    # ---- the fluid domain and its tags
    report = gate.domain["report"]
    assert report["check_after"]["valid"] is True, report
    if fixture.domain == "enclosure":
        assert report["check_after"]["closed"] is True, report
        assert set(ENCLOSURE_TAGS) <= set(gate.tags), gate.tags
    elif fixture.domain_params.get("mode") == "cavity":
        cavities = list(report["cavities"])
        assert len(cavities) == fixture.cavities, cavities
        assert all(cavity["volume"] > 0.0 for cavity in cavities), cavities
        assert len(gate.outputs) == fixture.cavities, gate.outputs
    else:
        assert report["check_after"]["closed"] is True, report
    assert DOMAIN_TAG in gate.tags, gate.tags

    # ---- the STL, and the replay of the whole chain into a fresh project
    assert gate.stl and gate.triangles > 0
    again, replay_ms = replay_stl(fixture, folder, tmp_path / "again")
    gate.replay_ms = replay_ms
    assert again == gate.stl, f"{file_name}: the replayed STL differs"

    record(f"m1.gate.{file_name}", gate.flow_ms)
    record(f"m1.gate.replay.{file_name}", gate.replay_ms)
