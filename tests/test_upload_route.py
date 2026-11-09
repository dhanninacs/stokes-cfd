"""The upload route (G1): a browser hands the server a CAD file and gets a path back.

`geometry.import {path}` takes a path on the **server**. A browser user has bytes and no path, so
`POST /api/upload?name=<file name>` streams the body to `<project folder>/imports/` (or the
session's scratch folder while the project has none) and answers with the absolute path, its size
and its sha256 — which `geometry.import` then takes, unchanged.

What is proved here, and each of these fails without the work:

* the happy path: a small STL uploads and **`geometry.import` of the answered path works**;
* the token is checked first, exactly like every other route (401 without it);
* the suffix is one of `stokes.geometry.formats.FORMATS`, case-insensitive (415 otherwise);
* `Content-Length` over the cap is refused before a byte is read (413), and a body that **lies**
  about its length is still stopped when the stream passes the cap (413);
* the name is sanitised — path parts, a leading dot and foreign characters cannot escape
  `imports/` — and an empty result is a 400;
* a repeated name gets `-2`, `-3` before its extension;
* a body cut short mid-stream leaves no file `geometry.import` could pick up: the reply is not
  written, and the half-written `.part` is gone;
* the folder itself is checked, so a symlinked `imports/` cannot redirect an upload out of the
  project.

**No scratch path here is invented**: every one is `tmp_path` from pytest. Filling `/tmp` breaks
the session's own shell (the brief's rule 2).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import IO

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.geometry import formats
from stokes.geometry.files import MAX_SOURCE_BYTES
from stokes.geometry.worker import prewarm, shutdown_pool
from stokes.server.app import create_app

TOKEN = "t0k3n"
STL_RECORD = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])


@pytest.fixture(scope="module")
def workers() -> Iterator[None]:
    pytest.importorskip("OCP")
    prewarm(2)  # the happy path imports through a real worker
    yield
    shutdown_pool()


def box_stl(size: float = 10.0) -> bytes:
    """A binary STL of a closed box: 8 corners, 12 triangles, `size` millimetres."""
    corners = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=np.float32,
    ) * np.float32(size)
    faces = (
        (0, 1, 2),
        (0, 2, 3),
        (4, 6, 5),
        (4, 7, 6),
        (5, 6, 2),
        (5, 2, 1),
        (7, 4, 0),
        (7, 0, 3),
        (3, 2, 6),
        (3, 6, 7),
        (0, 4, 5),
        (0, 5, 1),
    )
    records = np.zeros(len(faces), dtype=STL_RECORD)
    records["corners"] = corners[np.asarray(faces, dtype=np.uint32)]
    count = np.uint32(len(faces)).astype("<u4").tobytes()
    return b"solid box".ljust(80, b" ") + count + records.tobytes()


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """A session with a project, **because a session without one can no longer upload**.

    **This fixture used to be "an unsaved session: the scratch branch of the upload folder"** -
    is the branch that no longer exists (claude-win2, reading 1). `STOKES_HOME` is pointed at
    `tmp_path` first so the project's default folder is created under the test's own directory and
    **never in the real one** - the requirement claude-win2 attached to keeping `path` optional.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    s = Session(journal_path=tmp_path / "journal.jsonl")
    s.execute("project.new", name="uploads")
    return s


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """A session whose project was given an explicit folder under `tmp_path`."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    s = Session(journal_path=tmp_path / "journal.jsonl")
    s.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    return s


def client_for(session: Session) -> TestClient:
    return TestClient(create_app(session, token=TOKEN, dist_dir=None))


def upload(
    client: TestClient,
    name: str,
    body: bytes,
    *,
    token: str | None = TOKEN,
    content_length: int | None = None,
) -> httpx.Response:
    """A `POST /api/upload` with the raw bytes as the body.

    `content_length` overrides the header, which is how the client that lies about its length is
    tested: Starlette's `TestClient` would otherwise compute the truthful one.
    """
    headers = {}
    if content_length is not None:
        headers["content-length"] = str(content_length)
    query = {"name": name}
    if token is not None:
        query["token"] = token
    return client.post("/api/upload", params=query, content=body, headers=headers or None)


# ---------------------------------------------------------------- the happy path


def test_upload_then_import_the_answered_path(
    saved: Session, tmp_path: Path, workers: None
) -> None:
    """The whole point: a browser's bytes become a part, through the path the route answers."""
    body = box_stl()
    client = client_for(saved)
    reply = upload(client, "box.stl", body)
    assert reply.status_code == 200, reply.text
    answer = reply.json()
    assert set(answer) == {"path", "size", "sha256"}
    path = Path(answer["path"])
    assert path.is_absolute() and path.is_file()
    assert path.parent == Path(saved.project_path) / "imports"
    assert answer["size"] == len(body)
    assert answer["sha256"] == hashlib.sha256(body).hexdigest()
    assert path.read_bytes() == body
    assert reply.headers["cache-control"] == "no-store"

    # The path the route answers is exactly what `geometry.import` takes.
    result = saved.execute("geometry.import", path=answer["path"], wait=True)
    assert result["type"] == "part" and result["import"] == {"status": "ready"}
    assert result["params"]["triangles"] == 12


def test_an_upload_lands_INSIDE_the_project_folder(session: Session) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). This asserted that an upload for
    a project with no folder went to a per-session scratch `imports/` under `/tmp`.

    **There is no such project and no such folder now.** The owner's rule is that everything a
    project works with is created inside its folder, and the scratch was what broke it: uploads sat
    in `/tmp`, where they did not travel with the project on Save as and were not visibly the us

    **The coverage it carried is kept**: two uploads land in the SAME `imports/` folder rather t
    new directory per request, both files are whole, and it is the project's own folder - a
    stronger statement than the old one, which checked only that it differed from the runs folder.
    """
    client = client_for(session)
    first = Path(upload(client, "box.stl", box_stl()).json()["path"])
    second = Path(upload(client, "other.stl", box_stl()).json()["path"])
    assert first.parent.name == "imports" and second.parent == first.parent
    assert first.is_file() and second.is_file()
    # **Inside the project**, which is the rule: `<project folder>/imports/`.
    assert first.parent.parent == Path(session.project_path)
    # **And not a temporary directory**, which is where it used to go. Asserted on the location
    # rather than on a name, because the location was the defect.
    import tempfile

    assert not first.is_relative_to(Path(tempfile.gettempdir())) or first.is_relative_to(
        Path(session.project_path)
    )


# ---------------------------------------------------------------- the refusals


def test_no_token_is_a_401_and_writes_nothing(saved: Session) -> None:
    client = client_for(saved)
    reply = upload(client, "box.stl", box_stl(), token=None)
    assert reply.status_code == 401
    assert not (Path(saved.project_path) / "imports").exists()
    assert upload(client, "box.stl", box_stl(), token="wrong").status_code == 401


def test_a_bad_extension_is_a_415(saved: Session) -> None:
    """A suffix `FORMATS` does not list is a 415, including one with no suffix at all."""
    client = client_for(saved)
    for name in ("notes.txt", "part.exe", "archive.zip", "noextension", "a/", "***"):
        assert upload(client, name, b"x" * 32).status_code == 415, name
    assert not (Path(saved.project_path) / "imports").exists()


def test_every_listed_suffix_is_accepted_case_insensitively(saved: Session) -> None:
    """The allow-list is `FORMATS`, not a second copy of it."""
    client = client_for(saved)
    for fmt in formats.FORMATS:
        for suffix in fmt.suffixes:
            reply = upload(client, f"part{suffix.upper()}", b"x" * 16)
            assert reply.status_code == 200, (suffix, reply.text)


def test_the_route_cap_is_the_import_cap() -> None:
    """`MAX_UPLOAD_BYTES` must be `stokes.geometry.files.MAX_SOURCE_BYTES`, in both directions.

    The route writes the file and `check_source_file` then decides whether the import may read it;
    a route cap below that would refuse a file the import would accept, and one above it would write
    a file the import then refuses. Pinned here because `app.py` does not import the geometry
    package at start-up (it costs ~47 ms).
    """
    from stokes.geometry.files import MAX_SOURCE_BYTES
    from stokes.server.app import MAX_UPLOAD_BYTES

    assert MAX_UPLOAD_BYTES == MAX_SOURCE_BYTES == 1024**3


def test_oversize_content_length_is_a_413(tmp_path: Path) -> None:
    """The header is checked **first**: nothing is read, nothing is written."""
    session = Session(journal_path=tmp_path / "j.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    reply = upload(client_for(session), "big.stl", b"", content_length=MAX_SOURCE_BYTES + 1)
    assert reply.status_code == 413
    assert not (Path(session.project_path) / "imports").exists()


def test_a_body_that_lies_about_its_length_is_still_stopped(
    saved: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client can send a short `Content-Length` and a long body; the running total counts too.

    Proved without a 1 GiB body: the cap is monkeypatched down to a few kilobytes, so 8 KiB with a
    1-byte `Content-Length` passes the header check (nothing there is over 4096) and is refused only
    while streaming. The reply's detail is the byte cap, which is the stream branch's message; the
    header branch would have answered before the body existed.
    """
    import stokes.server.app as app_module

    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 4096)
    reply = upload(client_for(saved), "big.stl", b"x" * 8192, content_length=1)
    assert reply.status_code == 413, reply.text
    assert "4096" in reply.text
    imports = Path(saved.project_path) / "imports"
    assert not imports.exists() or list(imports.iterdir()) == []


def test_a_name_that_sanitises_to_empty_is_a_400(saved: Session) -> None:
    """Names with nothing usable left are a 400 (before the suffix is even considered).

    Every character outside `[A-Za-z0-9._-]` is replaced, so `***` -> `___` is *not* empty; it is a
    suffix fault (415). These reduce to nothing at all.
    """
    client = client_for(saved)
    for name in ("", ".", "..", "...", "/", "///", "..//.."):
        assert upload(client, name, box_stl()).status_code == 400, name
    assert not (Path(saved.project_path) / "imports").exists()


# ---------------------------------------------------------------- names


def test_names_are_sanitised_and_cannot_escape(saved: Session) -> None:
    """A hostile name is reduced to a plain file name inside `imports/`, never above it."""
    client = client_for(saved)
    imports = Path(saved.project_path) / "imports"
    reply = upload(client, "sub/../../evil.stl", box_stl())
    assert reply.status_code == 200, reply.text
    path = Path(reply.json()["path"]).resolve()
    assert path.parent == imports.resolve()
    assert path.name == "evil.stl"

    reply = upload(client, ".hidden.stl", box_stl())
    assert reply.status_code == 200, reply.text
    name = Path(reply.json()["path"]).name
    assert not name.startswith(".")

    reply = upload(client, "a b(c)*d.stl", box_stl())
    assert reply.status_code == 200, reply.text
    assert Path(reply.json()["path"]).name == "a_b_c__d.stl"


def test_a_collision_gets_a_numbered_suffix(saved: Session) -> None:
    client = client_for(saved)
    names = [Path(upload(client, "box.stl", box_stl()).json()["path"]).name for _ in range(3)]
    assert names == ["box.stl", "box-2.stl", "box-3.stl"]


def test_the_reservation_refuses_a_name_that_leaves_the_folder(tmp_path: Path) -> None:
    """The containment guard, exercised directly, **in the function that now builds the path**.

    It lived in `_upload_target`, which the route stopped calling when the name became a reservation
    (`os.link`) instead of a choice (`exists()`) — and **a guard left behind in a dead function is a
    guard that is not guarding**. Opus item 9's fix moved the check into `_link_into_place` with the
    reservation, and this test moved with it.

    Through the route the sanitiser has already removed every path part, so this is defence in depth
    and no request can reach it — which is exactly why it needs a test of its own: a line no request
    can reach is a line nothing would notice going wrong.
    """
    from stokes.server.app import _link_into_place

    root = tmp_path / "imports"
    root.mkdir()
    part = tmp_path / ".part"
    part.write_bytes(b"x")
    with pytest.raises(Exception) as caught:
        _link_into_place(part, root, "../escaped.stl")
    assert "escapes" in str(caught.value)
    assert not (tmp_path / "escaped.stl").exists()
    # The same name with the `..` gone is accepted and lands inside.
    inside = _link_into_place(part, root, "escaped.stl")
    assert inside.parent == root.resolve()


def test_a_symlinked_imports_folder_is_refused(saved: Session, tmp_path: Path) -> None:
    """A symlinked `imports/` must not send the upload out of the project.

    Found while probing the guard, not by a failing test: with `imports/` a link to a folder
    outside, the name check still passed, because `_upload_target` resolves the folder first and the
    link's target *became* the root — the containment check then proved the file was inside the
    wrong folder. `security.md` says "refuse symlinks that escape", so the folder itself is checked.
    """
    project = Path(saved.project_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "imports").symlink_to(outside, target_is_directory=True)

    reply = upload(client_for(saved), "box.stl", box_stl())
    assert reply.status_code == 400, reply.text
    assert list(outside.iterdir()) == [], "the upload escaped the project folder"


# ---------------------------------------------------------------- the aborted body


def test_an_aborted_upload_leaves_nothing_importable(saved: Session) -> None:
    """A body that stops mid-stream (the browser tab closed) must not leave a file behind.

    The client's exception propagates out of the test client; the `.part` file the bytes went to is
    removed, so nothing under `imports/` is a file `geometry.import` could take. The imports folder
    itself exists, which is the proof the route ran and cleaned up rather than never being reached.
    """
    client = client_for(saved)

    def aborted() -> Iterator[bytes]:
        yield box_stl()[:40]
        raise RuntimeError("connection lost")

    with pytest.raises(RuntimeError, match="connection lost"):
        client.post(f"/api/upload?token={TOKEN}&name=cut.stl", content=aborted())

    imports = Path(saved.project_path) / "imports"
    assert imports.is_dir(), "the route never ran, so this test proves nothing"
    leftovers = list(imports.iterdir())
    assert leftovers == [], leftovers
    # And the path a later import would be given is not a file.
    assert not (imports / "cut.stl").exists()


def test_the_body_is_streamed_not_buffered(saved: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """**The streaming proof.** The route must not materialise the whole body.

    `TestClient` hands the whole body over in one ASGI message, so no test through it can tell
    `request.stream()` from `await request.body()`. Two things are done here instead:

    * `Request.body` is made to raise, so a route that buffered the body would fail outright;
    * the body arrives as several ASGI chunks through `httpx.ASGITransport`, and the route still
      writes every byte and answers the right size and digest.

    What this does **not** prove is that the route's own file writes are incremental (the OS buffer
    hides that from `stat` during the request). It proves the route never asks for the body as one
    object, which is the failure that matters: `body()` on a 1 GiB upload is 1 GiB of RAM.
    """
    import httpx
    from starlette.requests import Request

    async def forbidden(self: Request) -> bytes:
        raise AssertionError("the route buffered the whole body instead of streaming it")

    monkeypatch.setattr(Request, "body", forbidden)

    body = box_stl()
    third = len(body) // 3
    chunks = [body[:third], body[third : 2 * third], body[2 * third :]]

    async def scenario() -> httpx.Response:
        app = create_app(saved, token=TOKEN, dist_dir=None)
        transport = httpx.ASGITransport(app=app)

        async def stream_body() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/upload?token={TOKEN}&name=streamed.stl", content=stream_body()
            )

    reply = asyncio.run(scenario())
    assert reply.status_code == 200, reply.text
    answer = reply.json()
    assert answer["size"] == len(body)
    assert answer["sha256"] == hashlib.sha256(body).hexdigest()
    assert Path(answer["path"]).read_bytes() == body


def test_a_part_file_is_not_visible_to_an_import(saved: Session) -> None:
    """The atomic write's contract, stated directly: the final name appears only when complete.

    A whole-looking `.part` file is not importable — `.part` is no format suffix — so an upload cut
    short can never be imported by its final name either.
    """
    from stokes.geometry.errors import GeometryUnsupported
    from stokes.geometry.files import check_source_file

    imports = Path(saved.project_path) / "imports"
    imports.mkdir(parents=True, exist_ok=True)
    part = imports / "half.stl.part"
    part.write_bytes(box_stl())
    with pytest.raises(GeometryUnsupported):
        check_source_file(part)
    # The route never answers a `.part` path either.
    client = client_for(saved)
    reply = upload(client, "half.stl", box_stl())
    assert Path(reply.json()["path"]).name == "half.stl"


def test_the_file_writes_happen_off_the_event_loop(
    saved: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus item 8: the route is `async def`, but its file writes ran ON the loop.**

    A large upload froze **every** WebSocket listener for as long as the write took. Measured: this
    work at 512 MB is **3.88 s** in a thread - during which the loop, if the work were on it, would
    have served no residuals and answered no ping.

    **The assertion is "not on the thread the route runs on", and getting that right took two
    wrong versions**, both of which passed against the unfixed code:

    * timing a heartbeat beside the request - `TestClient`/ASGI runs the app on a loop of its
      own, so the heartbeat was measuring a different one;
    * comparing against `threading.get_ident()` of the TEST - which is not the app's loop thread
      either, so it was true whether the write was offloaded or not.

    The thread the route itself is running on is captured inside `_write_blocks`' caller: the route
    records it on its first turn, and the write must not be there. That is the property, and it
    cannot be satisfied by accident.
    """
    from stokes.server import app as app_module

    loop_thread: list[int] = []
    writes: list[int] = []
    original_write = app_module._write_blocks
    original_receive = app_module._receive_to_part

    def recording_write(handle: IO[bytes], blocks: list[bytes]) -> None:
        writes.append(threading.get_ident())
        original_write(handle, blocks)

    async def record_loop_thread(request: object, part: Path, digest: object) -> int:
        # Runs as a coroutine ON the app's loop, so its thread is the loop's.
        loop_thread.append(threading.get_ident())
        return await original_receive(request, part, digest)  # type: ignore[arg-type]

    monkeypatch.setattr(app_module, "_write_blocks", recording_write)
    monkeypatch.setattr(app_module, "_receive_to_part", record_loop_thread)
    client = client_for(saved)
    reply = client.post(
        "/api/upload?name=threaded.stl",
        content=box_stl(),
        headers={"X-Stokes-Token": TOKEN},
    )
    assert reply.status_code == 200, reply.text
    assert loop_thread, "the route never reached the receive helper"
    assert writes, "the route never wrote through _write_blocks"
    assert all(ident != loop_thread[0] for ident in writes), (
        f"a file write ran on the event loop's own thread ({loop_thread[0]}), so every WebSocket "
        f"listener is frozen for the duration of the write; writes were on {writes}"
    )


def test_two_uploads_of_one_name_both_survive(saved: Session) -> None:
    """**Opus item 9: the free name was CHOSEN, not reserved, so two uploads took one.**

    The old target helper asked `exists()` and returned a free name, which cannot hold it. Two
    uploads of `part.step` in flight together both picked `part.step` (measured directly), and
    the second wrote over the first: the first reply's `path` and `sha256` then named **other
    bytes**, and its import read the wrong geometry.

    **Two earlier versions of this test passed against the unfixed code**, and both are worth
    recording because they are the obvious ways to write it:

    * a `ThreadPoolExecutor` of four uploads - `exists()` and the write are both fast, so the
      threads rarely interleave in that window;
    * two threads on a barrier with a head start - they still *list* at different times, so they get
      different names and both survive. The race needs both to list **before either writes**.

    That state is produced here by holding the first caller **inside** its reservation while the
    second runs to completion. `os.link` is what makes the hold meaningful: it fails on a name that
    exists, so the second caller's attempt is the check and the reserve at once.
    """
    from stokes.server import app as app_module
    from stokes.server.app import imports_dir

    folder = imports_dir(saved)
    folder.mkdir(parents=True, exist_ok=True)

    inside = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()
    real_link = os.link

    def pausing_link(source: object, target: object, **kwargs: object) -> None:
        nonlocal calls
        with lock:
            calls += 1
            mine = calls
        if mine == 1:
            inside.set()
            release.wait(timeout=5.0)  # the first caller is inside, its name not yet taken
        real_link(source, target, **kwargs)  # type: ignore[arg-type]

    def start(body: bytes) -> threading.Thread:
        part = folder / f".{body.decode()}.part"
        part.write_bytes(body)
        thread = threading.Thread(
            target=app_module._link_into_place, args=(part, folder, "part.step")
        )
        thread.start()
        return thread

    bodies = (b"first", b"second")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app_module.os, "link", pausing_link)
        first = start(bodies[0])
        assert inside.wait(timeout=5.0), "the first upload never reached its reservation"
        second = start(bodies[1])
        second.join(timeout=10)
        release.set()
        first.join(timeout=10)

    placed = sorted(folder.glob("part*.step"))
    assert len(placed) == 2, (
        f"an upload was overwritten; the folder holds {[p.name for p in placed]}"
    )
    assert sorted(p.read_bytes() for p in placed) == sorted(bodies), (
        "a reply's path no longer names the bytes it uploaded"
    )
    assert not list(folder.glob("*.part")) and not list(folder.glob(".*.part"))


def test_a_very_long_name_is_a_400_not_a_500(saved: Session) -> None:
    """**Opus item 17: a long name reached `mkstemp` and raised `ENAMETOOLONG`, unhandled.**

    Measured before the fix: a 50-character stem gave 200, a 200-character one gave 200, and a
    400-character one gave **`Internal Server Error`** - which tells the client the server is broken
    rather than that the name is too long. A paste produces this; it is not a hostile input.

    The cap is well inside the filesystem's `NAME_MAX` of 255 bytes, and leaves the collision suffix
    room.
    """
    from stokes.server.app import MAX_UPLOAD_STEM_CHARS

    client = client_for(saved)
    body = b"solid a\nendsolid a\n"
    for length, expected in ((MAX_UPLOAD_STEM_CHARS, 200), (MAX_UPLOAD_STEM_CHARS + 1, 400)):
        reply = client.post(
            f"/api/upload?name={'a' * length}.stl",
            content=body,
            headers={"X-Stokes-Token": TOKEN},
        )
        assert reply.status_code == expected, (
            f"{length} chars: {reply.status_code} {reply.text[:80]}"
        )


# **REMOVED: `test_the_start_up_sweep_removes_old_orphans_and_keeps_fresh_ones`.**
#
# It asserted the age guard on `sweep_orphan_scratch`, which removed scratch folders older than 24
# hours from `/tmp` - **127 imports folders and 5662 runs folders had accumulated**, and that
# measurement is what justified the sweep. **The sweep and the scratch are both gone** (claude-win2,
# reading 1): uploads land in the project's own `imports/`, where the user can see them and Save as
# carries them, so there is no orphan class to tidy. The tests above keep what still means somet
# that an upload lands in the PROJECT and not in a temporary directory.
