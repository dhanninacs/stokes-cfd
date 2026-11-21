"""Installing FoamWin from its GitHub release (ADR-0037), without the network.

The release metadata and the archive are served from memory through `_open`; the archive is a small
zip with a stand-in `setup-openfoam14.ps1` that writes the `of14-env.ps1` discovery looks for. The
real download was run once by hand (v14.0.0-win.5: 41 s, handshake OpenFOAM 14 org).
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest

from stokes.engines import foamwin_install as fw

SETUP = (
    "param([string]$InstallDir, [switch]$NoShortcut)\n"
    "New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null\n"
    "Set-Content -Path (Join-Path $InstallDir 'of14-env.ps1') -Value '# env' -Encoding ascii\n"
    "exit 0\n"
)


def package(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


class Served:
    """`_open` from memory: the release JSON for the API, the archive for its URL."""

    def __init__(self, archive: bytes, *, digest: str | None = None, url: str | None = None):
        self.archive = archive
        self.url = url or "https://github.com/dhanninacs/foamWin/releases/download/v1/OF14-win.zip"
        self.digest = "sha256:" + hashlib.sha256(archive).hexdigest() if digest is None else digest
        self.opened: list[str] = []

    def __call__(self, url: str, timeout: float, accept: str = "") -> Any:  # Any: a response
        self.opened.append(url)
        if url == fw.RELEASE_API:
            body = json.dumps(
                {
                    "tag_name": "v14.0.0-win.9",
                    "name": "FoamWin test",
                    "html_url": "https://github.com/dhanninacs/foamWin/releases/tag/v14.0.0-win.9",
                    "assets": [
                        {
                            "name": fw.ASSET,
                            "size": len(self.archive),
                            "digest": self.digest,
                            "browser_download_url": self.url,
                        }
                    ],
                }
            ).encode()
        else:
            body = self.archive
        response = io.BytesIO(body)
        response.geturl = lambda: url  # type: ignore[attr-defined]
        return response


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Served:
    serve = Served(package({fw.SETUP_SCRIPT: SETUP, "OpenFOAM-14/etc/configDict": "x"}))
    monkeypatch.setattr(fw, "_open", serve)
    return serve


def test_the_release_names_the_archive_and_its_digest(served: Served) -> None:
    release = fw.latest_release()
    assert (release.tag, release.size) == ("v14.0.0-win.9", len(served.archive))
    assert release.sha256 == hashlib.sha256(served.archive).hexdigest()


def test_a_release_without_a_digest_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = Served(b"zip", digest="")
    monkeypatch.setattr(fw, "_open", serve)
    with pytest.raises(fw.FoamWinInstallError, match="no SHA-256"):
        fw.latest_release()


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x.zip",
        "https://example.com/OF14-win.zip",
        "file:///C:/OF14-win.zip",
    ],
)
def test_only_https_github_hosts_are_used(url: str) -> None:
    with pytest.raises(fw.FoamWinInstallError, match="refusing a download"):
        fw._check_url(url)


def test_a_wrong_checksum_deletes_the_archive_and_installs_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve = Served(package({fw.SETUP_SCRIPT: SETUP}), digest="sha256:" + "0" * 64)
    monkeypatch.setattr(fw, "_open", serve)
    target = tmp_path / "OF14-win.zip"
    with pytest.raises(fw.FoamWinChecksum):
        fw.download(fw.latest_release(), target)
    assert not target.exists()


def test_a_cancelled_download_stops(served: Served, tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(fw.FoamWinCancelled):
        fw.download(fw.latest_release(), tmp_path / "a.zip", cancel=cancel)


@pytest.mark.parametrize("bad", ["../evil.ps1", "/abs.txt", "C:/x.txt", "a/../../b.txt"])
def test_an_unsafe_member_is_refused(tmp_path: Path, bad: str) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(package({bad: "x"}))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(fw.FoamWinInstallError, match=r"unsafe path|outside"):
        fw.safe_extract(archive, out)
    assert not (tmp_path / "evil.ps1").exists()


def test_a_package_without_the_setup_script_is_refused(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    with pytest.raises(fw.FoamWinInstallError, match="not a FoamWin package"):
        fw._package_root(tmp_path / "pkg")


@pytest.mark.skipif(sys.platform != "win32", reason="runs FoamWin's setup with Windows PowerShell")
def test_the_install_runs_the_package_setup_and_lands_where_discovery_looks(
    served: Served, tmp_path: Path
) -> None:
    stages: list[str] = []
    folder = tmp_path / "OpenFOAM-14-win"
    result = fw.install(
        install_dir=folder,
        progress=lambda stage, done, total: stages.append(stage) if stage not in stages else None,
    )
    assert Path(result["path"]) == folder / fw.ENV_SCRIPT and (folder / fw.ENV_SCRIPT).is_file()
    assert stages == ["release", "download", "unpack", "install", "done"]
    assert result["release"]["tag"] == "v14.0.0-win.9"
    # A second install into the same folder is refused, naming Rescan.
    with pytest.raises(fw.FoamWinInstallError, match="already installed"):
        fw.install(install_dir=folder)


def test_off_windows_the_install_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fw, "supported_platform", lambda: False)
    with pytest.raises(fw.FoamWinUnsupported):
        fw.install()


def test_the_status_command_says_what_a_download_would_fetch(
    served: Served, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from stokes.api import Session

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(fw, "supported_platform", lambda: True)
    answer = Session().execute("host.foamwin")
    assert answer["supported"] is True and answer["installed"] is None
    assert answer["release"]["tag"] == "v14.0.0-win.9"
    assert answer["folder"] == str(tmp_path / "OpenFOAM-14-win")
