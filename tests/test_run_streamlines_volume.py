"""`run.streamlines` with `source="volume"`: the host traces, the command unpacks (ADR-0030).

No OpenFOAM and no VTK on the desktop side: the fake host answers `stream.trace` with the arrays the
agent's own packing makes, and records what it was asked, so the parameters that cross the wire are
checked as well as the unpacking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session
from stokes.post.streamlines import StreamlinesInvalid
from tests.test_results_routes import FakeRunner, collected


def reply_of(lines: list[np.ndarray], reasons: list[str]) -> dict[str, Any]:
    """What `stokes_agent.trace._answer` returns, built here from point arrays."""
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    points = np.concatenate(lines) if lines else np.zeros((0, 3))
    speeds = np.linalg.norm(np.diff(points, axis=0, prepend=points[:1]), axis=1) + 1.0
    return {
        "count": len(lines),
        "points": points.astype("<f4").tobytes(),
        "speeds": speeds.astype("<f4").tobytes(),
        "offsets": offsets,
        "reasons": reasons,
        "time": 1.5,
    }


class TraceRunner(FakeRunner):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.traces: list[dict[str, Any]] = []

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "stream.trace":
            self.traces.append(dict(params))
            line = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.1], [0.0, 0.0, 0.2]])
            return reply_of([line, line + np.array([0.1, 0, 0])], ["left_domain", "length"])
        return super().call(method, timeout=timeout, **params)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    made = Session()
    made.execute("project.new", name="duct")
    return made


def a_run(session: Session, monkeypatch: pytest.MonkeyPatch) -> tuple[TraceRunner, str]:
    fake = TraceRunner()
    started = collected(session, fake, monkeypatch)
    return fake, str(started["run"])


def test_a_volume_trace_goes_to_the_host_and_comes_back_as_lines(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    reply = session.execute(
        "run.streamlines",
        run=run,
        patch="volume",
        field="U",
        seeds=[[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]],
        source="volume",
        direction="both",
        max_length=0.8,
        step=0.1,
        max_points=50,
    )
    assert reply["source"] == "volume" and reply["field"] == "U"
    assert [line["reason"] for line in reply["lines"]] == ["left_domain", "length"]
    assert reply["lines"][0]["points"][2] == pytest.approx([0.0, 0.0, 0.2])
    assert len(reply["lines"][0]["speeds"]) == 3
    asked = fake.traces[0]
    assert asked["direction"] == "both" and asked["max_length"] == 0.8
    assert asked["step_cells"] == 0.1 and asked["max_points"] == 50
    assert asked["seeds"] == [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]]


def test_the_defaults_are_the_hosts_own_length_and_a_modest_line(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    session.execute(
        "run.streamlines",
        run=run,
        patch="volume",
        field="U",
        seeds=[[0, 0, 0]],
        source="volume",
        max_length=0,
    )
    assert fake.traces[0]["max_length"] is None  # the host's default: 3 x the mesh's diagonal
    assert fake.traces[0]["max_points"] == 400


def test_only_the_velocity_can_be_traced_through_the_volume(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, run = a_run(session, monkeypatch)
    with pytest.raises(StreamlinesInvalid):
        session.execute(
            "run.streamlines",
            run=run,
            patch="volume",
            field="p",
            seeds=[[0, 0, 0]],
            source="volume",
        )


def test_no_seeds_and_too_many_seeds_are_refused_before_the_host(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    for seeds in ([], [[0, 0, 0]] * 201):
        with pytest.raises(StreamlinesInvalid):
            session.execute(
                "run.streamlines", run=run, patch="volume", field="U", seeds=seeds, source="volume"
            )
    assert fake.traces == []


def test_an_unknown_run_is_refused(session: Session) -> None:
    from stokes.errors import StokesError

    with pytest.raises(StokesError):
        session.execute(
            "run.streamlines",
            run="r_nope",
            patch="volume",
            field="U",
            seeds=[[0, 0, 0]],
            source="volume",
        )


def test_inlet_patches_seed_the_trace_on_the_host(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Seeds on every inlet**, spread by the host over the patches it holds (the elbow's two)."""
    fake, run = a_run(session, monkeypatch)
    reply = session.execute(
        "run.streamlines",
        run=run,
        patch="volume",
        field="U",
        source="volume",
        seed_patches=["inlet", "in_2"],
        count=60,
    )
    assert len(reply["lines"]) == 2
    asked = fake.traces[0]
    assert asked["seed_patches"] == ["inlet", "in_2"] and asked["seed_count"] == 60
    assert "seeds" not in asked


def test_points_alone_ask_the_host_what_an_older_agent_understands(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    session.execute(
        "run.streamlines", run=run, patch="volume", field="U", seeds=[[0, 0, 0]], source="volume"
    )
    assert "seed_patches" not in fake.traces[0] and "seed_count" not in fake.traces[0]


def test_seed_patches_are_refused_off_the_volume_or_when_empty(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, run = a_run(session, monkeypatch)
    bad: list[dict[str, Any]] = [
        {"seed_patches": ["inlet"]},  # not a volume trace
        {"seed_patches": [], "source": "volume"},
        {"seed_patches": [""], "source": "volume"},
        {"seed_patches": ["inlet"], "source": "volume", "count": 0},
        {"seed_patches": ["inlet"], "source": "volume", "count": 500},
    ]
    for params in bad:
        with pytest.raises(StreamlinesInvalid):
            session.execute("run.streamlines", run=run, patch="volume", field="U", **params)
    assert fake.traces == []


class SampleRunner(TraceRunner):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.samples: list[dict[str, Any]] = []

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "vector.sample":
            self.samples.append(dict(params))
            points = np.array([[0.0, 0.0, 0.1], [0.0, 0.01, 0.2]], dtype="<f4")
            vectors = np.array([[0.0, 0.0, 2.0], [0.1, 0.0, 1.9]], dtype="<f4")
            return {
                "count": 2,
                "points": points.tobytes(),
                "vectors": vectors.tobytes(),
                "spacing": 0.015,
                "time": 500.0,
            }
        return super().call(method, timeout=timeout, **params)


def test_volume_vectors_come_from_the_host_as_points_and_velocities(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SampleRunner()
    run = str(collected(session, fake, monkeypatch)["run"])
    reply = session.execute("run.vectors", run=run, count=300)
    assert fake.samples[0]["count"] == 300 and fake.samples[0]["job"]
    assert reply["field"] == "U" and reply["spacing"] == pytest.approx(0.015)
    assert reply["points"][1] == pytest.approx([0.0, 0.01, 0.2])
    assert reply["vectors"][1] == pytest.approx([0.1, 0.0, 1.9], abs=1e-6)


def test_volume_vectors_refuse_a_count_outside_the_cap(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SampleRunner()
    run = str(collected(session, fake, monkeypatch)["run"])
    for count in (0, 5000):
        with pytest.raises(StreamlinesInvalid):
            session.execute("run.vectors", run=run, count=count)
    assert fake.samples == []
