"""L5: the answers the results routes give, and the two that were wrong.

* an **empty** `?plane=` was indistinguishable from an absent one, so it silently served the default
  cut instead of the 400 the code's own docstring promised;
* an **`ArtifactError`** was unhandled, so a digest mismatch came back as an unhandled traceback
  rather than a stated 500.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.model.ids import new_id
from stokes.run.artifacts import ArtifactError
from stokes.run.store import RunStore
from stokes.server.app import create_app

TOKEN = "test-token"


def _client(session: Session) -> TestClient:
    return TestClient(create_app(session, token=TOKEN), raise_server_exceptions=False)


def _headers() -> dict[str, str]:
    return {"X-Stokes-Token": TOKEN}


def test_an_ABSENT_plane_is_the_default_cut(tmp_path: Path) -> None:
    """**The case that must keep working.** Every client that predates planes omits the parameter,
    and it has to mean "the run's own default cut" rather than an error."""
    session = Session()
    run = new_id("r")
    # **404 rather than 400**, because the RUN is unknown - which is the point: the absent plane
    # reached the store, so it was accepted as the default.
    reply = _client(session).get(f"/api/results/slice/{run}", headers=_headers())
    assert reply.status_code == 404, reply.text


@pytest.mark.parametrize("query", ["?plane=", "?plane=%20", "?plane=+"])
def test_an_EMPTY_plane_is_a_400(tmp_path: Path, query: str) -> None:
    """**Opus L5.** `_plane_of`'s docstring promised a 400 for an empty value, and with a `str = ""`
    default FastAPI gave the same value for `?plane=` and for no parameter at all - so the promise
    could not be kept and the request **silently served the default cut**, answering a question
    nobody asked.

    **404 rather than 400**, because the RUN is unknown - which is the point: the absent plane
    reached the store, so it was accepted as the default. That is what "it was treated as absent"
    looks like from outside.
    """
    session = Session()
    run = new_id("r")
    reply = _client(session).get(f"/api/results/slice/{run}{query}", headers=_headers())
    assert reply.status_code == 400, f"{query} gave {reply.status_code}"
    assert "plane" in str(reply.json().get("detail", "")).lower()


def test_a_MALFORMED_plane_is_a_400(tmp_path: Path) -> None:
    """Unchanged, and asserted so the empty case cannot be fixed by making everything a 400."""
    session = Session()
    run = new_id("r")
    reply = _client(session).get(f"/api/results/slice/{run}?plane=1,2,3", headers=_headers())
    assert reply.status_code == 400, reply.text


def test_an_ARTIFACT_FAILURE_is_a_500_that_states_the_cause(tmp_path: Path) -> None:
    """**Opus L5.** A digest mismatch, or an artifact that arrives short, is the HOST's or the
    transport's failure - **not the client's**, so a 4xx would send a caller to fix a fine request.

    It was unhandled, so the answer was FastAPI's default: a 500 with a traceback. Now the code
    (`run.artifact`) and the message are in the detail, so a caller can branch on the code rather
    than matching on prose.
    """
    session = Session()
    run = new_id("r")
    client = _client(session)

    store = RunStore.of(session)

    def refuses(*_args: object, **_kwargs: object) -> None:
        raise ArtifactError("artifact 'slice.stkslice' does not match its digest")

    store.slice_transport_for = refuses  # type: ignore[method-assign]
    session.__dict__["_run_store"] = store

    reply = client.get(f"/api/results/slice/{run}?plane=0,0,0,1,0,0", headers=_headers())
    assert reply.status_code == 500, reply.text
    detail = str(reply.json().get("detail", ""))
    assert "run.artifact" in detail, detail
    assert "digest" in detail, detail


def test_the_SLICE_EDGES_route_answers_the_same_way(tmp_path: Path) -> None:
    """**Both routes, because one of them was missing the `SliceBusy` handler before** (item 3) -
    the same class of omission, and a shared helper is what stops it recurring.
    """
    session = Session()
    run = new_id("r")
    client = _client(session)

    empty = client.get(f"/api/results/slice-edges/{run}?plane=", headers=_headers())
    assert empty.status_code == 400, empty.text

    store = RunStore.of(session)

    def refuses(*_args: object, **_kwargs: object) -> None:
        raise ArtifactError("artifact 'edges.stkslice' is short")

    store.slice_edges_for = refuses  # type: ignore[method-assign]
    session.__dict__["_run_store"] = store

    broken = client.get(f"/api/results/slice-edges/{run}?plane=0,0,0,1,0,0", headers=_headers())
    assert broken.status_code == 500, broken.text
    assert "run.artifact" in str(broken.json().get("detail", ""))
