"""The plane cache: one cut per plane, bounded, and removed with its run (slices contract).

**The four properties the contract names**, each with the failure it prevents:

* a second request for one plane does not cut again - a debounced drag would otherwise pay for every
  request it fires;
* two different planes give different geometry - a cache that collided would serve one plane's
  contours for another;
* the cache is bounded at `MAX_CACHED_PLANES`, least recently used first - unbounded, a session of
  dragging fills the project's cache;
* `run.clear` removes the whole slice folder - sixteen cuts of a deleted run are exactly what the
  user asked to be rid of.

The fake host **counts its cuts**, which is what makes "computed once" testable at all: the reply is
the same either way, so only the call count can tell a cache hit from a recomputation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.run.planes import MAX_CACHED_PLANES, parse_plane
from stokes.run.store import RunStore
from tests.test_results_routes import (
    TOKEN,
    FakeRunner,
    client_of,
    collected,
)


class CuttingRunner(FakeRunner):
    """Answers `slice.cut` with a real artifact and **counts how often it was asked**."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cuts: list[dict[str, Any]] = []
        self._cut_data: bytes | None = None

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "artifact.get" and self._cut_data is not None:
            # **The cut travels on the real `artifact.get` transport**, so the fake serves it there;
            # the base class serves `self.data`, which is the run's own field artifact. Returning
            # that for a cut is what made this test fail with "the agent reports 1232 bytes,
            # expected 908" - the size check doing its job on a fake that answered the wrong
            # question.
            offset, length = int(params["offset"]), int(params["length"])
            import hashlib

            # `data` is raw BYTES here, not hex - measured against the base class's own reply,
            # which sends `self.data[offset : offset + length]` unchanged.
            return {
                "size": len(self._cut_data),
                "sha256": hashlib.sha256(self._cut_data).hexdigest(),
                "offset": offset,
                "data": self._cut_data[offset : offset + length],
            }
        if method == "slice.cut":
            self.calls.append((method, params))
            plane = dict(params.get("plane") or {})
            self.cuts.append(plane)
            # **The artifact must describe the plane it was asked to cut.** A real agent cuts where
            # it is told and writes that plane into the header; a fake that returned one fixed
            # artifact for every plane is a fake whose cached files are all named for a plane their
            # contents deny - and the server refuses exactly that, so the fake, not the check, was
            # what made the first version of this test fail.
            data, header = _artifact_for(plane)
            # The agent writes the artifact and reports its name, size and digest; the server then
            # fetches it. A fake that returned the bytes inline would test a transport that does
            # not exist.
            self._cut_data = data
            import hashlib

            return {
                "artifact": "slice-cut",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "plane": params.get("plane"),
                "time": header.get("time", 0.0),
                "slice": {"points": 4, "triangles": 2, "edges": 4},
            }
        return super().call(method, timeout=timeout, **params)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", path=str(tmp_path / "p.stokes"), name="p")
    return made


def _run_with_fields(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[CuttingRunner, str]:
    """A finished run whose own artifact is fetched, plus the fake host that made it.

    `collected` installs the fake and adds the mesh itself, so this must not do either again -
    doing so twice raised `/mesh already has a child named 'results'` (measured).
    """
    fake = CuttingRunner()
    started = collected(session, fake, monkeypatch)
    return fake, str(started["run"])


def test_one_plane_is_cut_once_however_often_it_is_asked(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The cache's whole purpose.** A client debouncing a drag still fires several requests;
    the reply looks the same whether the cut was computed or read, so only the host's call
    count can tell them apart."""
    fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    plane = parse_plane("0,0,0,0,0,1")

    first = store.slice_artifact_for(run_id, plane)
    assert first is not None
    assert len(fake.cuts) == 1, "the first request did not cut"

    for _ in range(3):
        again = store.slice_artifact_for(run_id, plane)
        assert again is not None
    assert len(fake.cuts) == 1, (
        f"the plane was cut {len(fake.cuts)} times; a cache hit must not go to the host"
    )


def test_a_different_plane_is_a_different_cut(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The collision check.** A cache keyed too coarsely would answer one plane's request
    with another's contours - visibly wrong, and impossible to notice from the reply's
    shape."""
    fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    one = store.slice_artifact_for(run_id, parse_plane("0,0,0,0,0,1"))
    two = store.slice_artifact_for(run_id, parse_plane("0,0,0,1,0,0"))
    assert one is not None and two is not None
    assert len(fake.cuts) == 2, "the second plane was served from the first plane's cache entry"
    # And the files are distinct, which is what makes them separately evictable.
    assert store.slice_path(run_id, parse_plane("0,0,0,0,0,1")) != store.slice_path(
        run_id, parse_plane("0,0,0,1,0,0")
    )


def test_the_cache_keeps_sixteen_planes_and_drops_the_least_recently_used(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Bounded, so a session of dragging cannot fill the project.** The eviction is by use,
    not by write order: a plane a user keeps returning to must survive the ones they tried
    once."""
    _fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    # **Different PLANES, not different magnitudes.** `0,0,0,0,0,n` is one plane for every `n` - the
    # normal is normalised, which is the whole point of the key - so the first version of this test
    # asked for one plane seventeen times and held one file. Moving the ORIGIN is what makes them
    # distinct cuts.
    planes = [parse_plane(f"0,0,{n},0,0,1") for n in range(1, MAX_CACHED_PLANES + 2)]
    for plane in planes:
        assert store.slice_artifact_for(run_id, plane) is not None
    held = sorted(p.name for p in store.slices_folder(run_id).glob("*.stkslice"))
    assert len(held) == MAX_CACHED_PLANES, f"{len(held)} planes held, cap is {MAX_CACHED_PLANES}"
    # The FIRST plane is the one that went: it was written first and never used again.
    first = store.slice_path(run_id, planes[0])
    assert not first.exists(), "the least recently used plane was not the one evicted"
    assert store.slice_path(run_id, planes[-1]).exists()


def test_the_lru_keeps_a_plane_that_is_used_again(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The other half of LRU**: touching a plane has to save it. Without the touch this is
    "oldest written", which evicts exactly the plane a user is working with while keeping the
    ones they moved past."""
    _fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    keep = parse_plane("0,0,1,0,0,1")
    assert store.slice_artifact_for(run_id, keep) is not None
    # Use it again later, after several other planes, so its mtime is the newest. The origins
    # differ, because a changed magnitude is the same plane.
    for n in range(2, MAX_CACHED_PLANES + 1):
        time.sleep(0.01)
        store.slice_artifact_for(run_id, parse_plane(f"0,0,{n},0,0,1"))
    time.sleep(0.01)
    store.slice_artifact_for(run_id, keep)
    time.sleep(0.01)
    store.slice_artifact_for(run_id, parse_plane("0,0,99,0,0,1"))
    assert store.slice_path(run_id, keep).exists(), (
        "the plane that was used most recently was evicted before the ones written after it"
    )


def test_run_clear_removes_every_cached_plane(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Sixteen cuts of a deleted run are what the user asked to be rid of.** Measured through
    `run.clear`, because a folder left behind is invisible until the disk fills."""
    _fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    for n in range(1, 4):
        assert store.slice_artifact_for(run_id, parse_plane(f"0,0,{n},0,0,1")) is not None
    folder = store.slices_folder(run_id)
    assert folder.is_dir() and list(folder.glob("*.stkslice"))

    session.execute("run.clear", run=run_id)
    assert not folder.exists(), "run.clear left the cached planes behind"


def test_the_route_serves_a_named_plane_and_refuses_the_rest(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real route: a plane with fields 200s, a malformed one 400s, and **the token
    is still required** - the new parameter must not open a way past the check."""
    _fake, run_id = _run_with_fields(session, monkeypatch)
    client: TestClient = client_of(session)
    headers = {"X-Stokes-Token": TOKEN}

    ok = client.get(f"/api/results/slice/{run_id}?plane=0,0,0,0,0,1", headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.content

    bad = client.get(f"/api/results/slice/{run_id}?plane=nope", headers=headers)
    assert bad.status_code == 400

    for url in (
        f"/api/results/slice/{run_id}?plane=0,0,0,0,0,1",
        f"/api/results/slice-edges/{run_id}?plane=0,0,0,0,0,1",
    ):
        assert client.get(url).status_code == 401, "a plane bypassed the token check"

    edges = client.get(f"/api/results/slice-edges/{run_id}?plane=0,0,0,0,0,1", headers=headers)
    assert edges.status_code == 200, edges.text


def _artifact_for(plane: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """A cut artifact describing ``plane``, built with the agent's own packer.

    **Using the real packer, not a hand-built header.** A fake that assembled the bytes itself would
    be testing its own idea of the format, and the plane it writes into the header is exactly what
    the server checks a cached file against - so the fake has to do this the way the agent does.
    """
    import numpy as np

    from stokes_agent.extract import pack_fields
    from tests.test_results_routes import patch_arrays, quad

    patches = [
        {
            "name": "inlet",
            "points": np.array(quad(0.0), dtype="float32"),
            "fields": patch_arrays(1.0),
        }
    ]
    cut = {
        "origin": list(plane.get("origin") or [0.0, 0.0, 0.0]),
        "normal": list(plane.get("normal") or [1.0, 0.0, 0.0]),
        "positions": np.array(quad(0.0), dtype="float32"),
        "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
        "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
        "fields": patch_arrays(3.0),
    }
    return pack_fields(patches, cut, 1.5)


# ---------------------------------------------------------------- M2: one failure, one plane


class FailingRunner(CuttingRunner):
    """Fails `slice.cut` **for one named plane only**, and cuts the rest normally.

    A fake that failed *every* cut could not show the defect: what M2 is about is a failure on one
    plane changing the answer for a DIFFERENT one.
    """

    def __init__(self, refuse: str = "x", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.refuse = refuse

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        if method == "slice.cut":
            plane = params.get("plane") or {}
            if str(plane.get("normal_axis") or plane.get("axis") or "") == self.refuse:
                self.calls.append((method, params))
                raise RuntimeError("the host is gone")
        return super().call(method, timeout=timeout, **params)


def test_a_failed_plane_does_not_change_the_answer_for_another_plane(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus M2.** `_slice_error` was ONE attribute for the whole store: set by any failure,
    read by any later question, never cleared - although the docstring said it was "cleared at
    the start of every cut".

    So a host that refused one plane made **every later "this plane has nothing on it" report "the
    host is unavailable"**, sending a user to check a machine that was fine. The key is
    `(run, plane)` because that is the question being asked.

    **Both planes must FAIL here, and that took me three attempts to see.** `slice_state` consults
    the error only when there is no artifact - and a plane with no artifact is CUT, which normally
    succeeds. The defect shows itself in the sequence the route actually performs: a fetch fails, so
    `_slice_missing` asks `slice_state`, which re-cuts. When the SECOND plane's cut fails too, that
    second answer must be about the second plane.
    """
    from stokes.run.planes import plane_key
    from stokes.run.store import RunStore

    fake = CuttingRunner()
    started = collected(session, fake, monkeypatch)
    run = str(started["run"])
    store = RunStore.of(session)

    first = {"normal": [1.0, 0.0, 0.0], "origin": [0.0, 0.0, 0.0]}
    second = {"normal": [0.0, 1.0, 0.0], "origin": [0.0, 0.0, 0.0]}
    assert plane_key(first) != plane_key(second), "two different questions"

    # **Every cut fails, and each records its OWN plane** - which is what `_cut_once` does on a
    # runner exception. Writing the entry here is that bookkeeping without a host that refuses.
    original = store._cut_and_cache

    def always_fail(run_id: str, plane: dict[str, Any], path: Path) -> Any:
        if run_id == run:
            with store._lock:
                store._slice_errors[(run_id, plane_key(plane))] = (
                    f"the host is gone ({plane_key(plane)[:6]})"
                )
            return None
        return original(run_id, plane, path)

    monkeypatch.setattr(store, "_cut_and_cache", always_fail)

    assert store.slice_state(run, first) == "no_host"
    assert store.slice_state(run, second) == "no_host"

    # **AND THE TWO MUST BE REMEMBERED SEPARATELY.** With one store-wide string the answer is
    # `no_host` for both, so the value alone proves nothing - what the defect destroyed is the
    # ABILITY TO TELL THEM APART, and that is what this asserts.
    with store._lock:
        assert store._slice_errors.get((run, plane_key(first)))
        assert store._slice_errors.get((run, plane_key(second)))
        assert len(store._slice_errors) >= 2, (
            "one entry for two planes: the store-wide error is back, so a later plane inherits an "
            "earlier plane's failure"
        )

    # A plane neither request touched has no entry at all, so its answer is `empty` - "nothing here"
    # rather than "the host is down".
    untouched = {"normal": [0.0, 0.0, 1.0], "origin": [900.0, 900.0, 900.0]}
    with store._lock:
        assert (run, plane_key(untouched)) not in store._slice_errors


def test_a_retried_cut_does_not_inherit_the_previous_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The other half of M2: "cleared at the start of every cut".** The docstring said it and the
    code never did it, so a plane that failed once and then **succeeded** kept its old error - and a
    user who retried was told the host was down for a problem that had gone away."""
    from stokes.run.planes import plane_key
    from stokes.run.store import RunStore

    fake = CuttingRunner()
    started = collected(session, fake, monkeypatch)
    run = str(started["run"])
    store = RunStore.of(session)
    plane = {"normal": [0.0, 0.0, 1.0], "origin": [5.0, 5.0, 5.0]}

    # A first attempt that failed.
    original = store._cut_and_cache

    def fail_once(run_id: str, plane_arg: dict[str, Any], path: Path) -> Any:
        if run_id == run and plane_key(plane_arg) == plane_key(plane):
            with store._lock:
                store._slice_errors[(run_id, plane_key(plane))] = "the host is gone"
            return None
        return original(run_id, plane_arg, path)

    monkeypatch.setattr(store, "_cut_and_cache", fail_once)
    assert store.slice_artifact_for(run, plane) is None
    with store._lock:
        assert (run, plane_key(plane)) in store._slice_errors, "the failure was not recorded"

    # A SECOND attempt, this time allowed to succeed. The started cut must drop the old reason,
    # because it is no longer true.
    monkeypatch.setattr(store, "_cut_and_cache", original)
    assert store.slice_artifact_for(run, plane) is not None, "the second cut did not succeed"
    with store._lock:
        assert (run, plane_key(plane)) not in store._slice_errors, (
            "the old failure survived a successful cut, so the state still reports the host"
        )
    assert store.slice_state(run, plane) == "ok"


def test_an_overflowing_plane_is_a_400_and_not_a_host_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The symptom M3 actually produced, through the real route.**

    The normal `1e200,1e200,0` overflowed to `[0,0,0]`, so it was **accepted** here, sent to the
    host, refused by the agent - and the client was told **`results.host_unavailable`**. A user
    with a malformed plane was sent to check a machine that was fine, and retrying would not help.

    Now it is a **400** at the boundary: the fault is the request, and it says so.
    """
    _fake, run_id = _run_with_fields(session, monkeypatch)
    client: TestClient = client_of(session)
    headers = {"X-Stokes-Token": TOKEN}

    # **The huge normal is now a VALID plane, so it is served** - which is the fix, not a
    # failure of it. My first version expected a 400 here and got a 200: the point of M3 is that
    # this normal was never malformed, it was mangled by an overflow, and refusing it would have
    # been a false refusal for a perfectly good plane.
    for plane in ("0,0,0,1e200,1e200,0", "0,0,0,1e308,1e308,0"):
        served = client.get(f"/api/results/slice/{run_id}?plane={plane}", headers=headers)
        assert served.status_code == 200, (
            f"{plane} gave {served.status_code}: a normal at the edge of the range is a plane"
        )

    # **The genuinely malformed one is a 400 at the boundary**, and never a host error.
    zero = client.get(f"/api/results/slice/{run_id}?plane=0,0,0,0,0,0", headers=headers)
    assert zero.status_code == 400, zero.text
    detail = zero.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else ""
    assert code != "results.host_unavailable", "the client is told the host is down"


def test_a_failed_request_cuts_the_plane_once_not_twice(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus M4, first half: a miss costs TWO cuts.** The route calls `slice_artifact_for`, which
    cuts on demand; when that fails it calls `_slice_missing` -> `slice_state`, which calls
    `slice_artifact_for` **again**. Against a host that is slow rather than dead, each attempt waits
    `CUT_SLICE_TIMEOUT_S` = **300 s**, so one request can hold a worker thread **600 s or more** -
    and about forty distinct planes occupy every route thread.

    The count is what this asserts, because the count is the defect.
    """
    from stokes.run.store import RunStore

    fake = CuttingRunner()
    started = collected(session, fake, monkeypatch)
    run = str(started["run"])
    store = RunStore.of(session)

    cuts = {"n": 0}
    original = store._cut_and_cache

    def counting(run_id: str, plane: dict[str, Any], path: Path) -> Any:
        cuts["n"] += 1
        return original(run_id, plane, path)

    monkeypatch.setattr(store, "_cut_and_cache", counting)

    # **The cut FAILS, and it records WHY** - what the real `_cut_once` does when the runner
    # raises. My first version returned None without recording anything, so `slice_state` had no
    # reason to report and answered "empty" - **the fake was wrong, not the code**, and it made the
    # route look as if it were misreporting a host failure.
    def always_fails(run_id: str, plane_arg: dict[str, Any], path: Path) -> Any:
        from stokes.run.planes import plane_key

        cuts["n"] += 1
        with store._lock:
            store._slice_errors[(run_id, plane_key(plane_arg))] = "the host is gone"
        return None

    monkeypatch.setattr(store, "_cut_and_cache", always_fails)
    client = client_of(session)
    reply = client.get(
        f"/api/results/slice/{run}?plane=0,0,0,0,0,1", headers={"X-Stokes-Token": TOKEN}
    )
    # **`results.host_unavailable`, and NOT `results.slice_empty`.** A host that failed was reported
    # as "the plane misses the model", which tells a client to move the plane - the wrong action
    # entirely, and it was the symptom of the second cut: `slice_state` re-cut, got nothing, and had
    # no reason to report because the re-cut had not recorded one yet.
    assert reply.status_code == 404, reply.text
    assert reply.json()["detail"]["code"] == "results.host_unavailable", reply.text
    assert cuts["n"] == 1, (
        f"one failed request cut the plane {cuts['n']} times; a slow host makes that {cuts['n']} "
        f"x CUT_SLICE_TIMEOUT_S of a held worker thread"
    )


def test_all_THREE_routes_answer_503_when_the_server_is_busy(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus recheck item 3.** Only ONE of the three routes caught `SliceBusy`, so a busy server
    answered **500** on the other two - and the client asks for the slice and its edges together, so
    half a request looked like a crash rather than a retry.

    **The `Retry-After` header is asserted too**, because it is the part that tells a client to come
    back: a 503 without it invites an immediate retry into the same wall.
    """
    from stokes.run.store import SliceBusy

    _fake, run_id = _run_with_fields(session, monkeypatch)
    client: TestClient = client_of(session)
    headers = {"X-Stokes-Token": TOKEN}

    # **Every store method that can reach a cut raises**, which is what a full queue does.
    store = RunStore.of(session)
    busy = {"message": "the server has 32 plane cuts queued; try again shortly"}

    def always_busy(*_args: Any, **_kwargs: Any) -> Any:
        raise SliceBusy(busy["message"])

    for method in ("slice_transport_for", "slice_edges_for", "field_payload_for"):
        monkeypatch.setattr(store, method, always_busy)

    urls = (
        f"/api/results/slice/{run_id}?plane=0,0,0,1,0,0",
        f"/api/results/slice-edges/{run_id}?plane=0,0,0,1,0,0",
        f"/api/results/field/{run_id}/slice?field=U&plane=0,0,0,1,0,0",
    )
    for url in urls:
        reply = client.get(url, headers=headers)
        assert reply.status_code == 503, f"{url} gave {reply.status_code}, not a busy answer"
        assert reply.headers.get("Retry-After"), f"{url} has no Retry-After"
        assert reply.json()["detail"]["code"] == "results.busy", reply.text


def test_waiting_for_a_CUT_SLOT_is_bounded(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus recheck item 4a.** `with self._cut_slots` waits for ever, and a cut may take
    `CUT_SLICE_TIMEOUT_S` = 300 s - so with two slots and 32 queued planes a caller could wait
    **sixteen times 300 s** while holding its route thread.

    Driven by taking both slots and then asking for a cut: the answer must be `SliceBusy`, not
    a wait that outlives the test.
    that outlives the test.
    """
    import threading

    from stokes.run.store import SliceBusy

    _fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    monkeypatch.setattr("stokes.run.store.CUT_QUEUE_TIMEOUT_S", 0.2)

    # **Both slots held**, so the next acquisition must time out.
    held = [store._cut_slots.acquire(), store._cut_slots.acquire()]
    assert all(held), "could not take both slots"
    try:
        started = threading.Event()
        plane = {"normal": [0.0, 0.0, 1.0], "origin": [0.0, 0.0, 0.0]}
        with pytest.raises(SliceBusy):
            started.set()
            store._cut_and_cache(run_id, plane, store.slice_path(run_id, plane))
    finally:
        for _ in held:
            store._cut_slots.release()


def test_a_FLOOD_OF_DUPLICATE_requests_is_refused(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus recheck item 4b.** The caps were checked only when a NEW entry was created, so any
    number of callers asking for the SAME plane took the `ahead` branch and waited 30 s each -
    **forty duplicates held forty route threads and were counted by nothing.**

    My first two versions of this test checked the wrong thing: one only looked at the counters
    after a finished cut, which passes with the cap removed. **A cap that is not checked while
    callers are WAITING is not a cap** - the point is the number that arrive together. What must be
    asserted is that a waiter over the limit is **refused**, so this occupies the plane with a live
    waiter and then asks again.
    """
    import threading

    from stokes.run.store import MAX_WAITERS_PER_PLANE, SliceBusy

    _fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)
    plane = {"normal": [0.0, 0.0, 1.0], "origin": [0.0, 0.0, 0.0]}
    key = str(store.slice_path(run_id, plane))

    # **A waiter is already in flight for this plane.** `_cut_waiting` is the dict the real code
    # checks, so putting an unset Event there is exactly the state a slow first cut leaves behind.
    ahead = threading.Event()
    with store._lock:
        store._cut_waiting[key] = ahead
        store._cut_waiters[key] = MAX_WAITERS_PER_PLANE
    try:
        # **THE REFUSAL MUST BE IMMEDIATE, and my first version could not tell that apart from the
        # timeout.** With the cap removed the call still raised `SliceBusy` - after
        # `CUT_QUEUE_TIMEOUT_S` = 30 s of waiting on the event, which the test measured as a pass.
        # **A cap that answers after thirty seconds is not a cap.** Timing it is what makes the
        # assertion about the CAP rather than about any `SliceBusy` at all.
        started = time.monotonic()
        with pytest.raises(SliceBusy):
            store._cut_and_cache(run_id, plane, store.slice_path(run_id, plane))
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, (
            f"refused only after {elapsed:.1f}s - that is the queue timeout, not the cap: the "
            f"waiter count is not being consulted"
        )
    finally:
        # **Released before the assertion**, or a failure would leave the plane stuck for every test
        # that runs after this one.
        with store._lock:
            store._cut_waiting.pop(key, None)
            store._cut_waiters.pop(key, None)
        ahead.set()

    # And with the count back to zero the same call is allowed through, which is the false-refusal
    # half: a cap that never lets anything past is not a cap.
    store.slice_artifact_for(run_id, plane)
    assert store._cut_waiters == {}, "a finished cut left a waiter counted"


def test_the_slice_ERROR_map_does_not_grow_without_bound(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus recheck item 4d.** `_slice_errors` was written by every failed cut and cleared only
    when that same plane was cut again - so a drag across a model that fails left an entry for
    **every** distinct plane, for the life of the store. A long session against an unreachable host
    could accumulate thousands.

    **Bounded by `MAX_CACHED_PLANES`, the same number the plane cache keeps**, because the two
    describe the same set of planes: a reason for a plane whose artifact has been trimmed away can
    never be read, since a request for it cuts afresh and **that** is what records a reason.

    The **newest** are kept, asserted here, because those are the planes a client is asking about.
    """
    from stokes.run.planes import MAX_CACHED_PLANES, plane_key
    from stokes.run.store import RunStore

    _fake, run_id = _run_with_fields(session, monkeypatch)
    store = RunStore.of(session)

    # **Every cut records a reason against its OWN plane**, which is what `_cut_once` does when the
    # runner raises. Driven at the map, because the bound is about the map.
    def always_fails(run_id_arg: str, plane: dict[str, Any], path: Path) -> Any:
        with store._lock:
            store._slice_errors[(run_id_arg, plane_key(plane))] = "the host is gone"
            store._trim_slice_errors(run_id_arg)
        return None

    monkeypatch.setattr(store, "_cut_and_cache", always_fails)

    asked = []
    for number in range(MAX_CACHED_PLANES * 3):
        plane = {"normal": [1.0, float(number), 0.0], "origin": [0.0, 0.0, 0.0]}
        asked.append(plane_key(plane))
        store.slice_artifact_for(run_id, plane)

    with store._lock:
        kept = [key for key in store._slice_errors if key[0] == run_id]
    assert len(kept) <= MAX_CACHED_PLANES, (
        f"{len(kept)} reasons kept for {len(asked)} planes - the map grows with the drag"
    )
    # **The newest survive**, because a client is asking about the plane it just moved to.
    assert (run_id, asked[-1]) in store._slice_errors, "the newest reason was trimmed"
    assert (run_id, asked[0]) not in store._slice_errors, "the oldest reason was kept"
