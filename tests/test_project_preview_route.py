"""The preview route: serving a thumbnail, and refusing to be a file browser.

**This route is the one place a start screen asks the server for a file by path**, so its
containment is the whole design. "Resolve inside a root" is not enough: a folder under a root
with a `preview.png` in it is not a project, and serving its picture is an arbitrary file read
wearing a thumbnail's clothes. The path must name a project the app has **already offered** -
one in the recent list, or inside a browse root - and it must still hold a `project.json`.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.server.app import create_app

TOKEN = "t0k3n"
HEADERS = {"X-Stokes-Token": TOKEN}


def a_png(width: int = 320, height: int = 200) -> str:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    head = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + head).decode()


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return Session()


def client_of(session: Session) -> TestClient:
    return TestClient(
        create_app(session, token=TOKEN, dist_dir=None), raise_server_exceptions=False
    )


def a_project_with_a_preview(session: Session, folder: Path) -> Path:
    session.execute("project.new", name=folder.stem, path=str(folder))
    session.execute("project.preview.set", png_base64=a_png())
    return folder


def test_a_projects_own_preview_is_served(session: Session, tmp_path: Path) -> None:
    folder = a_project_with_a_preview(session, tmp_path / "home" / "projects" / "duct.stokes")
    reply = client_of(session).get(
        "/api/project/preview", params={"path": str(folder)}, headers=HEADERS
    )
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "image/png"
    assert reply.content.startswith(b"\x89PNG")
    assert reply.headers.get("etag"), "an image that rarely changes must be cacheable"


def test_an_unchanged_preview_answers_304(session: Session, tmp_path: Path) -> None:
    """**The point of the ETag.** A start screen redraws often; the picture rarely changes."""
    folder = a_project_with_a_preview(session, tmp_path / "home" / "projects" / "duct.stokes")
    client = client_of(session)
    first = client.get("/api/project/preview", params={"path": str(folder)}, headers=HEADERS)
    etag = first.headers["etag"]
    again = client.get(
        "/api/project/preview",
        params={"path": str(folder)},
        headers={**HEADERS, "If-None-Match": etag},
    )
    assert again.status_code == 304
    assert again.content == b""


def test_a_project_with_no_preview_is_a_404_with_a_code(session: Session, tmp_path: Path) -> None:
    """The tile shows a placeholder, so this must be distinguishable from "no such thing"."""
    folder = tmp_path / "home" / "projects" / "bare.stokes"
    session.execute("project.new", name="bare", path=str(folder))
    reply = client_of(session).get(
        "/api/project/preview", params={"path": str(folder)}, headers=HEADERS
    )
    assert reply.status_code == 404
    assert reply.json()["detail"]["code"] == "project.no_preview"


def test_a_folder_outside_the_roots_is_refused_even_with_a_preview_in_it(
    session: Session, tmp_path: Path
) -> None:
    """**The case that makes this more than a containment check.** This folder holds a real
    `preview.png`, and it is outside every root - so serving it would be a file read by another
    name."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    reply = client_of(session).get(
        "/api/project/preview", params={"path": str(outside)}, headers=HEADERS
    )
    assert reply.status_code == 404


def test_a_folder_inside_a_root_WITHOUT_a_project_file_is_refused(
    session: Session, tmp_path: Path
) -> None:
    """Inside a root is not enough: membership means "a project we offered", and the marker is the
    same `project.json` that `project.open` looks for."""
    from stokes.model.browse import projects_folder

    folder = projects_folder()
    (folder / "not-a-project").mkdir()
    (folder / "not-a-project" / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    reply = client_of(session).get(
        "/api/project/preview", params={"path": str(folder / "not-a-project")}, headers=HEADERS
    )
    assert reply.status_code == 404


@pytest.mark.parametrize(
    "where",
    ["/etc", "/root", ""],
)
def test_a_path_that_is_not_a_project_is_refused(
    session: Session, tmp_path: Path, where: str
) -> None:
    reply = client_of(session).get("/api/project/preview", params={"path": where}, headers=HEADERS)
    assert reply.status_code == 404


def test_traversal_out_of_a_known_project_is_refused(session: Session, tmp_path: Path) -> None:
    """A path that starts at a real project and climbs out is not that project."""
    folder = a_project_with_a_preview(session, tmp_path / "home" / "projects" / "duct.stokes")
    climbing = str(folder / ".." / ".." / ".." / ".." / "etc")
    reply = client_of(session).get(
        "/api/project/preview", params={"path": climbing}, headers=HEADERS
    )
    assert reply.status_code == 404


def test_a_symlinked_preview_is_not_served(session: Session, tmp_path: Path) -> None:
    """A link named `preview.png` is not a preview, and following one is how this route would be
    turned into a read of something else."""
    folder = tmp_path / "home" / "projects" / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    (folder / "preview.png").symlink_to("/etc/hostname")
    reply = client_of(session).get(
        "/api/project/preview", params={"path": str(folder)}, headers=HEADERS
    )
    assert reply.status_code == 404


def test_a_preview_over_the_cap_is_not_served(session: Session, tmp_path: Path) -> None:
    """**A file swapped in by hand must not be served whole.** `project.preview.set` refuses
    anything over 512 KiB, but the file on disk is the user's and can be replaced - so the route
    checks too, rather than trusting a check that ran at some earlier time.
    """
    from stokes.model.preview import MAX_BYTES

    folder = tmp_path / "home" / "projects" / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    (folder / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_BYTES + 1))
    reply = client_of(session).get(
        "/api/project/preview", params={"path": str(folder)}, headers=HEADERS
    )
    assert reply.status_code == 404


def test_the_token_is_still_required(session: Session, tmp_path: Path) -> None:
    """A new route must not open a way past the check every other route makes."""
    folder = a_project_with_a_preview(session, tmp_path / "home" / "projects" / "duct.stokes")
    assert (
        client_of(session).get("/api/project/preview", params={"path": str(folder)}).status_code
        == 401
    )
