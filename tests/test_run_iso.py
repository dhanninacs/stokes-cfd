"""`run.iso_surface`, the iso cache and `GET /api/results/iso/{run}` (ADR-0026 decision 3).

**No OpenFOAM, no VTK on the desktop side.** The fake host answers `iso.cut` with an artifact the
agent's own `pack_fields` builds, and **counts its cuts**: the reply is the same whether a surface
was cut or read, so only the count can tell a cache hit from a recomputation.

The run's own fields are those of `tests/test_results_routes.py`: `p` ranges over 0 to 9 m2/s2
(the patches and the default slice), and there is no `T`. That artifact has no `volume` table, so
it is also the cache written before the agent measured the volume; `volume_field_artifact` adds one.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session
from stokes.errors import StokesError
from stokes.run.iso import ISO_SUFFIX, MAX_ISO_TRIANGLES, MAX_VALUE_TEXT
from stokes.run.planes import iso_key, parse_plane, plane_key
from stokes.run.store import SLICE_SUFFIX, RunStore
from stokes_agent.extract import pack_fields
from tests.test_results_routes import (
    TOKEN,
    UNITS,
    FakeRunner,
    client_of,
    collected,
    patch_arrays,
    quad,
    release_views,
)


def iso_artifact(field: str, value: float) -> bytes:
    """An iso-surface artifact as the agent packs it: one quad at the value, no patches."""
    cut = {
        "field": field,
        "value": value,
        "positions": np.array(quad(0.0), dtype="float32"),
        "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
        "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
        "fields": {field: np.full(4, value, dtype="float32")},
    }
    data, _header = pack_fields([], cut, 1.5, UNITS)
    return data


class IsoRunner(FakeRunner):
    """Answers `iso.cut` with a real artifact and counts the cuts; `reported` overrides the
    triangle count the reply claims, for the size limit."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cuts: list[dict[str, Any]] = []
        self.fetches = 0
        self.reported: int | None = None
        self.down = False
        self._cut: bytes | None = None

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "iso.cut":
            if self.down:
                raise ConnectionError("the host is gone")
            self.cuts.append(dict(params))
            self._cut = iso_artifact(str(params["field"]), float(params["value"]))
            return {
                "artifact": "iso-cut.stkfld",
                "size": len(self._cut),
                "sha256": hashlib.sha256(self._cut).hexdigest(),
                "field": params["field"],
                "value": params["value"],
                "time": 1.5,
                "surface": {"points": 4, "triangles": self.reported or 2, "edges": 4},
            }
        if method == "artifact.get" and self._cut is not None:
            self.fetches += 1
            offset, length = int(params["offset"]), int(params["length"])
            return {
                "size": len(self._cut),
                "sha256": hashlib.sha256(self._cut).hexdigest(),
                "offset": offset,
                "data": self._cut[offset : offset + length],
            }
        return super().call(method, timeout=timeout, **params)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    made = Session()
    made.execute("project.new", name="duct")
    return made


def a_run(session: Session, monkeypatch: pytest.MonkeyPatch) -> tuple[IsoRunner, str]:
    fake = IsoRunner()
    started = collected(session, fake, monkeypatch)
    return fake, str(started["run"])


def code_of(excinfo: pytest.ExceptionInfo[StokesError]) -> str:
    return excinfo.value.code


# ---------------------------------------------------------------- the command


def test_the_command_cuts_once_and_answers_from_the_cache_after(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Through `Session.execute`**: a waited cut returns the summary, and a second call is the
    cache's answer - the host is asked once."""
    fake, run = a_run(session, monkeypatch)
    first = session.execute("run.iso_surface", run=run, field="p", value="4.5 [J/kg]", wait=True)
    assert first["state"] == "ready"
    assert first["triangles"] == 2
    assert first["value"] == pytest.approx(4.5)
    assert first["units"] == "m2/s2"
    assert first["range"] == {"min": 0.0, "max": 9.0}
    assert first["key"] == iso_key("p", 4.5)
    assert first["result"].startswith(f"/api/results/iso/{run}?field=p&value=")
    assert len(fake.cuts) == 1
    assert fake.cuts[0]["field"] == "p"

    again = session.execute("run.iso_surface", run=run, field="p", value=4.5)
    assert again == first
    assert len(fake.cuts) == 1, "a cached surface went back to the host"


def test_the_command_is_a_query_and_is_not_journaled(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Not a mutation**, like `run.fields`: a replay of the journal must not cut surfaces."""
    _fake, run = a_run(session, monkeypatch)
    before = len(session.journal.records)
    session.execute("run.iso_surface", run=run, field="p", value=1.0, wait=True)
    assert len(session.journal.records) == before
    assert all(r["command"] != "run.iso_surface" for r in session.journal.records)


def test_a_value_outside_the_range_is_refused_before_any_cut(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    with pytest.raises(StokesError) as excinfo:
        session.execute("run.iso_surface", run=run, field="p", value="12 [J/kg]", wait=True)
    assert code_of(excinfo) == "results.iso_out_of_range"
    assert excinfo.value.details["max"] == 9.0
    assert fake.cuts == []


def volume_field_artifact(low: float, high: float) -> tuple[bytes, dict[str, Any]]:
    """The fake host's field artifact with a `volume` table: `p` spans `low..high` over the whole
    mesh, wider than the 0..9 its patches and slice record."""
    patches = [
        {
            "name": "inlet",
            "points": np.array(quad(0.0), dtype="float32"),
            "fields": patch_arrays(1.0),
        },
        {
            "name": "walls",
            "points": np.array(quad(1.0), dtype="float32"),
            "fields": patch_arrays(2.0),
        },
    ]
    plane = {
        "origin": [0.0, 0.0, 0.0],
        "normal": [1.0, 0.0, 0.0],
        "positions": np.array(quad(0.0), dtype="float32"),
        "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
        "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
        "fields": patch_arrays(3.0),
    }
    return pack_fields(patches, plane, 1.5, volume={"p": (low, high)})


def a_run_with_a_volume(
    session: Session, monkeypatch: pytest.MonkeyPatch, low: float, high: float
) -> tuple[IsoRunner, str]:
    fake = IsoRunner()
    fake.data, fake.header = volume_field_artifact(low, high)
    started = collected(session, fake, monkeypatch)
    return fake, str(started["run"])


def test_run_fields_reports_the_volume_range(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The query returns the agent's volume range** beside the recorded one, which the legend
    keeps using."""
    _fake, run = a_run_with_a_volume(session, monkeypatch, -3.0, 15.0)
    fields = {f["name"]: f for f in session.execute("run.fields", run=run)["fields"]}
    assert fields["p"]["min"] == 0.0 and fields["p"]["max"] == 9.0
    assert fields["p"]["volume"] == {"min": -3.0, "max": 15.0}


def test_a_value_only_the_interior_reaches_is_accepted(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**ADR-0026 amendment 2**: 12 lies outside the patches' 0..9 but inside the volume's
    -3..15, so it is cut rather than refused."""
    fake, run = a_run_with_a_volume(session, monkeypatch, -3.0, 15.0)
    answer = session.execute("run.iso_surface", run=run, field="p", value="12 [J/kg]", wait=True)
    assert answer["state"] == "ready"
    assert answer["range"] == {"min": -3.0, "max": 15.0}
    assert len(fake.cuts) == 1
    low = session.execute("run.iso_surface", run=run, field="p", value=-2.0, wait=True)
    assert low["state"] == "ready"


@pytest.mark.parametrize("value", [15.5, -3.5])
def test_a_value_outside_the_volume_range_is_refused(
    session: Session, monkeypatch: pytest.MonkeyPatch, value: float
) -> None:
    fake, run = a_run_with_a_volume(session, monkeypatch, -3.0, 15.0)
    with pytest.raises(StokesError) as excinfo:
        session.execute("run.iso_surface", run=run, field="p", value=value, wait=True)
    assert code_of(excinfo) == "results.iso_out_of_range"
    assert excinfo.value.details["min"] == -3.0
    assert excinfo.value.details["max"] == 15.0
    assert fake.cuts == []


def test_the_iso_route_accepts_a_value_inside_the_volume(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route uses the same check, so it serves 12 as the command does."""
    _fake, run = a_run_with_a_volume(session, monkeypatch, -3.0, 15.0)
    reply = client_of(session).get(f"/api/results/iso/{run}?field=p&value=12&token={TOKEN}")
    assert reply.status_code == 200, reply.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("U", 1.0),  # a vector has no single value to contour
        ("T", 300.0),  # an incompressible run has no temperature
        ("p", "4 [m/s]"),  # the wrong dimension
        ("p", "four"),  # not a quantity
        ("p", "x" * (MAX_VALUE_TEXT + 1)),  # over the length limit
        ("p", True),  # a boolean is not a number
    ],
)
def test_a_request_that_cannot_be_contoured_is_refused(
    session: Session, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    fake, run = a_run(session, monkeypatch)
    with pytest.raises(StokesError) as excinfo:
        session.execute("run.iso_surface", run=run, field=field, value=value, wait=True)
    assert code_of(excinfo) == "results.iso_invalid"
    assert fake.cuts == []


def test_a_surface_over_the_triangle_limit_is_refused_before_it_is_fetched(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    fake.reported = MAX_ISO_TRIANGLES + 1
    with pytest.raises(StokesError) as excinfo:
        session.execute("run.iso_surface", run=run, field="p", value=4.5, wait=True)
    assert code_of(excinfo) == "results.iso_too_large"
    assert fake.fetches == 0, "the over-limit surface was fetched"
    store = RunStore.of(session)
    assert not store.iso_path(run, "p", 4.5).exists()


def test_a_host_that_cannot_cut_is_a_typed_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    fake.down = True
    with pytest.raises(StokesError) as excinfo:
        session.execute("run.iso_surface", run=run, field="p", value=4.5, wait=True)
    assert code_of(excinfo) == "results.host_unavailable"
    assert "the host is gone" in excinfo.value.message


def test_without_wait_the_summary_arrives_as_an_event(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The session thread never waits on a cut**: the command answers `cutting` and the summary
    follows as `run.iso_ready`."""
    fake, run = a_run(session, monkeypatch)
    arrived = threading.Event()
    seen: list[dict[str, Any]] = []

    def listen(event: str, data: dict[str, Any]) -> None:
        if event in ("run.iso_ready", "run.iso_failed"):
            seen.append({"event": event, **data})
            arrived.set()

    session.events.subscribe("*", listen)
    answer = session.execute("run.iso_surface", run=run, field="p", value=2.0)
    assert answer["state"] == "cutting"
    assert arrived.wait(20.0), "no iso event arrived"
    assert seen[0]["event"] == "run.iso_ready"
    assert seen[0]["triangles"] == 2
    assert len(fake.cuts) == 1


# ---------------------------------------------------------------- the cache keys


def test_a_speed_value_has_a_dimension_to_be_parsed_in() -> None:
    """`U_mag` is contourable since 2026-11-01, and the iso route hands its value over as text,
    which needs the unit's dimension: `m/s` was missing and the Looks card's Iso answered 400."""
    from stokes.run.iso import value_in_si

    assert value_in_si("0.3", "m/s") == pytest.approx(0.3)
    assert value_in_si("30 [cm/s]", "m/s") == pytest.approx(0.3)
    assert value_in_si(0.25, "m/s") == pytest.approx(0.25)


def test_iso_keys_are_content_addressed() -> None:
    """**One key per (field, value)**: float noise collapses; another field or value does not."""
    assert iso_key("p", 4.5) == iso_key("p", 4.5000001)
    assert iso_key("p", 4.5) != iso_key("p", 4.6)
    assert iso_key("p", 1.0) != iso_key("T", 1.0)
    assert len(iso_key("T", 300.0)) == 32


def test_iso_and_plane_cuts_cannot_share_a_cache_slot(session: Session) -> None:
    """**The shared cut scheme keeps the kinds apart**: the key text, the file prefix and the
    suffix all differ, so an iso-surface can never be read as a plane or another field's surface."""
    store = RunStore.of(session)
    run = "r_00000001"
    plane = parse_plane("0,0,0,0,0,1")
    iso = store.iso_path(run, "p", 0.0)
    cut = store.slice_path(run, plane)
    assert iso.parent == cut.parent, "the two kinds share one folder"
    assert iso.name == f"iso-{iso_key('p', 0.0)}{ISO_SUFFIX}"
    assert cut.name == f"{plane_key(plane)}{SLICE_SUFFIX}"
    assert iso != cut
    assert iso_key("p", 0.0) != plane_key(plane)
    assert store.iso_path(run, "p", 1.0) != store.iso_path(run, "T", 1.0)
    assert store.iso_path(run, "p", 1.0) != store.iso_path(run, "p", 2.0)


def test_a_cached_file_is_content_checked_against_its_name(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The key is the identity, the header is the truth**: a file whose header names another
    value is cut again rather than served."""
    fake, run = a_run(session, monkeypatch)
    store = RunStore.of(session)
    session.execute("run.iso_surface", run=run, field="p", value=4.5, wait=True)
    wrong = store.iso_path(run, "p", 3.0)
    wrong.write_bytes(store.iso_path(run, "p", 4.5).read_bytes())
    answer = session.execute("run.iso_surface", run=run, field="p", value=3.0, wait=True)
    assert answer["value"] == pytest.approx(3.0)
    assert len(fake.cuts) == 2


# ---------------------------------------------------------------- the route


def test_the_iso_route_needs_the_token(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake, run = a_run(session, monkeypatch)
    reply = client_of(session).get(f"/api/results/iso/{run}?field=p&value=4.5")
    assert reply.status_code == 401


def test_the_iso_route_serves_transport_and_a_second_request_is_cached(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.geometry.transport import unpack

    fake, run = a_run(session, monkeypatch)
    client = client_of(session)
    url = f"/api/results/iso/{run}?field=p&value=4.5&token={TOKEN}"
    first = client.get(url)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    meshes = unpack(first.content)
    assert meshes, "the transport holds no mesh"
    second = client.get(url)
    assert second.status_code == 200
    assert second.content == first.content
    assert len(fake.cuts) == 1, "the second request went back to the host"


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("field=p&value=12", 422),  # outside 0..9
        ("field=U&value=1", 400),  # not contourable
        ("field=p&value=four", 400),  # not a quantity
        ("field=p", 400),  # no value
        (f"field=p&value={'9' * (MAX_VALUE_TEXT + 1)}", 414),  # past the length limit
        (f"field={'p' * 40}&value=1", 414),
    ],
)
def test_the_iso_route_refuses_what_it_cannot_answer(
    session: Session, monkeypatch: pytest.MonkeyPatch, query: str, status: int
) -> None:
    fake, run = a_run(session, monkeypatch)
    reply = client_of(session).get(f"/api/results/iso/{run}?{query}&token={TOKEN}")
    assert reply.status_code == status, reply.text
    assert fake.cuts == []


def test_the_iso_route_refuses_a_malformed_or_unknown_run(session: Session) -> None:
    client = client_of(session)
    assert client.get(f"/api/results/iso/..%2Fx?field=p&value=1&token={TOKEN}").status_code in (
        400,
        404,
    )
    assert client.get(f"/api/results/iso/r_00000009?field=p&value=1&token={TOKEN}").status_code == (
        404
    )


def test_the_iso_route_answers_413_past_the_triangle_limit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    fake.reported = MAX_ISO_TRIANGLES + 1
    reply = client_of(session).get(f"/api/results/iso/{run}?field=p&value=4.5&token={TOKEN}")
    assert reply.status_code == 413
    assert reply.json()["detail"]["code"] == "results.iso_too_large"


def test_a_deleted_iso_cache_file_with_no_host_is_a_404(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A deleted cache file is not served from memory**: with the host gone the route says so."""
    fake, run = a_run(session, monkeypatch)
    client = client_of(session)
    url = f"/api/results/iso/{run}?field=p&value=4.5&token={TOKEN}"
    assert client.get(url).status_code == 200
    store = RunStore.of(session)
    release_views(store)
    store.iso_path(run, "p", 4.5).unlink()
    fake.down = True
    reply = client.get(url)
    assert reply.status_code == 404
    assert reply.json()["detail"]["code"] == "results.host_unavailable"


def test_a_deleted_run_cache_file_makes_the_iso_route_404(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    store = RunStore.of(session)
    release_views(store)
    store.fields_path(run).unlink()
    reply = client_of(session).get(f"/api/results/iso/{run}?field=p&value=4.5&token={TOKEN}")
    assert reply.status_code == 404
    assert fake.cuts == []
