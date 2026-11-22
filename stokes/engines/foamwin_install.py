"""Download and install FoamWin, the native Windows OpenFOAM 14, for a user with no engine.

ADR-0046.

A Windows user who downloads Stokes and has neither FoamWin, WSL with OpenFOAM, nor a remote host
cannot mesh or solve. This module fetches FoamWin's portable release (`OF14-win.zip`) from its
GitHub releases, checks the archive's SHA-256 against the digest GitHub publishes for it, unpacks
it into a staging folder and runs FoamWin's **own** `setup-openfoam14.ps1` into the default folder,
`%LOCALAPPDATA%\\OpenFOAM-14-win`. That folder is the first place Stokes' discovery looks
(`stokes_agent.native_env.candidates`), so a finished install is linked with no setting.

**The one network call, and why.** The release metadata and the archive come from a fixed URL
(`RELEASE_API`, github.com/dhanninacs/foamWin); nothing here takes a URL from a caller, a page or a
file. The download is refused unless the user asked for it (the `engine.foamwin_install` command,
the Settings button, or `--yes` on the command line), and it is checked before anything runs.

**Safety.** The archive must hash to GitHub's published digest; each member must stay inside the
staging folder (no absolute paths, no `..`, no links) and the unpacked size is capped. The setup
script runs as `powershell.exe -File` with an argument list, never a shell string. Nothing needs
administrator rights: the default folder is under the user's profile.

Standard library only, imported lazily by the commands, so ``import stokes.api`` does not pay
for it.

    python -m stokes.engines.foamwin_install --status
    python -m stokes.engines.foamwin_install --yes [--dir D:\\FoamWin]
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from stokes import workspace_root
from stokes.errors import StokesError

#: FoamWin's latest release, as GitHub's API reports it. Fixed: never taken from input.
RELEASE_API = "https://api.github.com/repos/dhanninacs/foamWin/releases/latest"
#: The portable package: its `setup-openfoam14.ps1` installs without administrator rights.
ASSET = "OF14-win.zip"
#: Hosts a release download may come from (GitHub redirects assets to its object store).
ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
#: The most the archive may unpack to (v14.0.0-win.5: 137.5 MB packed, about 600 MB unpacked).
MAX_UNPACKED_BYTES = 4 * 1024**3
#: The most the archive itself may be.
MAX_ARCHIVE_BYTES = 2 * 1024**3
#: The environment script an install must end with; discovery looks for exactly this file.
ENV_SCRIPT = "of14-env.ps1"
SETUP_SCRIPT = "setup-openfoam14.ps1"

Progress = Callable[[str, int, int], None]  # stage, done, total (bytes, or 0 when unknown)


class FoamWinInstallError(StokesError):
    """The download or the install did not complete; the message says which step and why."""

    code = "engine.foamwin_install_failed"


class FoamWinUnsupported(FoamWinInstallError):
    code = "engine.foamwin_unsupported"


class FoamWinChecksum(FoamWinInstallError):
    code = "engine.foamwin_checksum"


class FoamWinCancelled(FoamWinInstallError):
    code = "engine.foamwin_cancelled"


@dataclass(frozen=True)
class Release:
    """The archive a download would fetch."""

    tag: str
    name: str
    url: str
    size: int
    sha256: str
    page: str

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return asdict(self)


# ------------------------------------------------------------------------------- where


def default_install_dir() -> Path:
    """FoamWin's own default (`setup-openfoam14.ps1 -InstallDir`), which discovery searches."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise FoamWinUnsupported("LOCALAPPDATA is not set, so there is no per-user install folder")
    return Path(base) / "OpenFOAM-14-win"


def installed(install_dir: Path | None = None) -> Path | None:
    """The environment script of a FoamWin already in ``install_dir`` (default folder), or None."""
    folder = install_dir or default_install_dir()
    script = folder / ENV_SCRIPT
    return script if script.is_file() else None


def supported_platform() -> bool:
    return platform.system() == "Windows"


# ------------------------------------------------------------------------------- the network


def _check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise FoamWinInstallError(
            f"refusing a download from {parts.hostname or url!r}: only GitHub release hosts "
            "are used",
            details={"url_host": parts.hostname},
        )
    return url


def _open(
    url: str, timeout: float, accept: str = "application/octet-stream"
) -> Any:  # Any: HTTPResponse
    headers = {"User-Agent": "stokes-foamwin-installer", "Accept": accept}
    request = urllib.request.Request(_check_url(url), headers=headers)  # noqa: S310 - https only
    try:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https, checked host
    except OSError as exc:
        raise FoamWinInstallError(
            f"could not reach GitHub ({exc}); check the connection or a proxy, or install FoamWin "
            "by hand from https://github.com/dhanninacs/foamWin/releases"
        ) from exc
    _check_url(response.geturl())  # a redirect must land on a GitHub host too
    return response


def latest_release(timeout: float = 30.0) -> Release:
    """The latest FoamWin release's portable archive, with GitHub's SHA-256 digest of it."""
    with _open(RELEASE_API, timeout, accept="application/vnd.github+json") as response:
        data = json.loads(response.read(1024 * 1024).decode("utf-8"))
    assets = {a.get("name"): a for a in data.get("assets", []) if isinstance(a, dict)}
    asset = assets.get(ASSET)
    if asset is None:
        raise FoamWinInstallError(
            f"the latest FoamWin release ({data.get('tag_name', '?')}) has no {ASSET}",
            details={"tag": data.get("tag_name")},
        )
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
        raise FoamWinInstallError(
            f"the release publishes no SHA-256 for {ASSET}, so the download could not be checked",
            details={"tag": data.get("tag_name")},
        )
    return Release(
        tag=str(data.get("tag_name", "")),
        name=str(data.get("name", "")),
        url=_check_url(str(asset.get("browser_download_url", ""))),
        size=int(asset.get("size", 0)),
        sha256=digest.removeprefix("sha256:").lower(),
        page=str(data.get("html_url", "")),
    )


def download(
    release: Release,
    target: Path,
    *,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    timeout: float = 60.0,
) -> Path:
    """Stream the archive to ``target``, hashing as it goes; refuse a wrong size or digest."""
    if release.size <= 0 or release.size > MAX_ARCHIVE_BYTES:
        raise FoamWinInstallError(f"the release reports an implausible size ({release.size} bytes)")
    digest = hashlib.sha256()
    done = 0
    with _open(release.url, timeout) as response, target.open("wb") as out:
        while True:
            if cancel is not None and cancel.is_set():
                raise FoamWinCancelled("the download was cancelled")
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            done += len(chunk)
            if done > release.size:
                raise FoamWinInstallError("the download is larger than the release says")
            digest.update(chunk)
            out.write(chunk)
            if progress is not None:
                progress("download", done, release.size)
    if done != release.size:
        raise FoamWinInstallError(
            f"the download stopped at {done:,} of {release.size:,} bytes; try again"
        )
    if digest.hexdigest() != release.sha256:
        target.unlink(missing_ok=True)
        raise FoamWinChecksum(
            "the downloaded archive does not match the SHA-256 the release publishes, so it was "
            "deleted and nothing was installed",
            details={"expected": release.sha256, "got": digest.hexdigest()},
        )
    return target


# ------------------------------------------------------------------------------- unpack and install


def safe_extract(archive: Path, folder: Path, *, cancel: threading.Event | None = None) -> None:
    """Unpack ``archive`` into ``folder``: every member inside it, no links, size capped."""
    root = folder.resolve()
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        total = sum(m.file_size for m in members)
        if total > MAX_UNPACKED_BYTES:
            raise FoamWinInstallError(
                f"the archive would unpack to {total:,} bytes, over the limit"
            )
        for member in members:
            if cancel is not None and cancel.is_set():
                raise FoamWinCancelled("the install was cancelled")
            name = member.filename
            if name.startswith(("/", "\\")) or ":" in name or ".." in Path(name).parts:
                raise FoamWinInstallError(f"the archive has an unsafe path: {name!r}")
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise FoamWinInstallError(f"the archive has a link: {name!r}")
            target = (root / name).resolve()
            if root not in (target, *target.parents):
                raise FoamWinInstallError(f"the archive has a path outside its folder: {name!r}")
        zf.extractall(root)


def _package_root(folder: Path) -> Path:
    """The folder holding `setup-openfoam14.ps1`: the archive's top level or one folder down."""
    for candidate in (folder, *sorted(p for p in folder.iterdir() if p.is_dir())):
        if (candidate / SETUP_SCRIPT).is_file():
            return candidate
    raise FoamWinInstallError(f"the archive has no {SETUP_SCRIPT}; it is not a FoamWin package")


def run_setup(package: Path, install_dir: Path, timeout: float = 1800.0) -> None:
    """FoamWin's own setup script, into ``install_dir``, without a desktop shortcut."""
    shell = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    argv = [
        str(shell),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(package / SETUP_SCRIPT),
        "-InstallDir",
        str(install_dir),
        "-NoShortcut",
    ]
    try:
        done = subprocess.run(  # noqa: S603 - fixed shell, the package's own script, argv list
            argv, cwd=package, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FoamWinInstallError(f"FoamWin's setup script did not finish: {exc}") from exc
    if done.returncode != 0:
        tail = (done.stdout + done.stderr).decode(errors="replace").strip()[-600:]
        raise FoamWinInstallError(
            f"FoamWin's setup script failed (exit {done.returncode}): {tail}",
            details={"exit": done.returncode},
        )


def install(
    *,
    install_dir: Path | None = None,
    release: Release | None = None,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    replace: bool = False,
) -> dict[str, Any]:  # Any: JSON
    """Download, check, unpack and install FoamWin; return where it went and which release."""
    if not supported_platform():
        raise FoamWinUnsupported(
            "FoamWin is the native Windows OpenFOAM; on Linux, install OpenFOAM 14 from "
            "openfoam.org, or add a host in Settings"
        )
    folder = install_dir or default_install_dir()
    existing = installed(folder)
    if existing is not None and not replace:
        raise FoamWinInstallError(
            f"FoamWin is already installed in {folder}; Rescan in Settings to use it",
            details={"code": "engine.foamwin_present", "path": str(existing)},
        )
    report = progress or (lambda stage, done, total: None)
    report("release", 0, 0)
    chosen = release or latest_release()
    staging = workspace_root.scratch("foamwin-")  # Stokes' scratch root, never the system /tmp
    try:
        archive = download(chosen, staging / ASSET, progress=report, cancel=cancel)
        report("unpack", 0, 0)
        unpacked = staging / "package"
        unpacked.mkdir()
        safe_extract(archive, unpacked, cancel=cancel)
        archive.unlink(missing_ok=True)
        if cancel is not None and cancel.is_set():
            raise FoamWinCancelled("the install was cancelled")
        report("install", 0, 0)
        run_setup(_package_root(unpacked), folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    script = installed(folder)
    if script is None:
        raise FoamWinInstallError(f"the setup script finished but {folder / ENV_SCRIPT} is missing")
    report("done", 1, 1)
    return {"path": str(script), "folder": str(folder), "release": chosen.to_json()}


def status() -> dict[str, Any]:  # Any: JSON
    """What a Settings page shows before anything is downloaded."""
    out: dict[str, Any] = {"supported": supported_platform(), "installed": None}
    if out["supported"]:
        found = installed()
        out["installed"] = str(found) if found is not None else None
        out["folder"] = str(default_install_dir())
    return out


# ------------------------------------------------------------------------------- command line


def _say(text: str, *, end: str = "\n", stream: Any = None) -> None:  # Any: a text stream
    """The command line's output path (the constitution allows no `print` elsewhere)."""
    out = stream or sys.stdout
    out.write(text + end)
    out.flush()


def _cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m stokes.engines.foamwin_install")
    parser.add_argument("--status", action="store_true", help="say whether FoamWin is installed")
    parser.add_argument("--yes", action="store_true", help="download and install without asking")
    parser.add_argument("--dir", type=Path, default=None, help="install folder (default: per user)")
    args = parser.parse_args(argv)
    if args.status:
        _say(json.dumps(status(), indent=1))
        return 0
    release = latest_release()
    _say(f"FoamWin {release.tag}: {ASSET}, {release.size / 1e6:.1f} MB from {release.url}")
    if not args.yes:
        answer = input("Download and install it? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            _say("Nothing was downloaded.")
            return 1

    def show(stage: str, done: int, total: int) -> None:
        if stage == "download" and total:
            _say(f"\rdownloading {done / 1e6:7.1f} / {total / 1e6:.1f} MB", end="")
        elif stage != "download":
            _say(f"\n{stage}...")  # plain dots: a Windows console code page garbles an ellipsis

    try:
        result = install(install_dir=args.dir, release=release, progress=show)
    except FoamWinInstallError as exc:
        _say(f"\n{exc.message}", stream=sys.stderr)
        return 2
    _say(f"\nInstalled FoamWin {release.tag}: {result['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
