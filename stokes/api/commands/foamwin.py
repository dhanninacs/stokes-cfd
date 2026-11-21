"""The ``host.foamwin*`` commands: give a Windows computer an engine (ADR-0037).

`host.foamwin` says whether this computer can take FoamWin, whether it already has it and what a
download would fetch (the release, the archive and its size), **without downloading anything**.
`host.foamwin_install` starts the download and install on a thread and returns at once; progress
arrives as events, and a finished install is in the default folder that discovery searches, so the
next `host.probe` of `local` lists it with no setting. `host.foamwin_cancel` stops it.

Events:

    host.foamwin_progress   {stage, done, total}   release, download (bytes), unpack, install
    host.foamwin_installed  {path, folder, release}
    host.foamwin_failed     {error}                 a StokesError dict (code, message, details)

One install at a time per process: a second `host.foamwin_install` while one runs is refused.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError
from stokes.log import fields, get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_running: dict[str, Any] = {}  # Any: {"cancel": threading.Event, "thread": threading.Thread}

#: At most this many progress events a second, as the agent coalesces its own (constitution §5).
_PROGRESS_INTERVAL_S = 0.1


class FoamWinBusy(StokesError):
    code = "host.foamwin_busy"


@command(
    "host.foamwin",
    params=(
        ParamDef(
            "release",
            "boolean",
            required=False,
            default=True,
            description="Also ask GitHub which release a download would fetch (one small request).",
        ),
    ),
    requires_project=False,
    mutates=False,
)
def host_foamwin(ctx: CommandContext, release: bool = True) -> dict[str, Any]:  # Any: JSON
    """Whether this computer can install FoamWin, whether it has it, and what would be fetched."""
    from stokes.engines import foamwin_install as fw

    out = fw.status()
    out["running"] = bool(_running)
    if out["supported"] and out["installed"] is None and release:
        try:
            out["release"] = fw.latest_release().to_json()
        except StokesError as exc:
            out["release_error"] = exc.to_dict()
    return out


@command(
    "host.foamwin_install",
    params=(
        ParamDef(
            "replace",
            "boolean",
            required=False,
            default=False,
            description="Install over a FoamWin already in the default folder.",
        ),
    ),
    requires_project=False,
    mutates=False,
)
def host_foamwin_install(ctx: CommandContext, replace: bool = False) -> dict[str, Any]:  # Any: JSON
    """Start downloading and installing FoamWin; progress and the outcome arrive as events."""
    from stokes.engines import foamwin_install as fw

    if not fw.supported_platform():
        raise fw.FoamWinUnsupported(
            "FoamWin is the native Windows OpenFOAM; this computer is not Windows"
        )
    session = ctx.session
    cancel = threading.Event()
    with _lock:
        if _running:
            raise FoamWinBusy("FoamWin is already being installed")
        _running["cancel"] = cancel

    last = [0.0]

    def progress(stage: str, done: int, total: int) -> None:
        now = time.monotonic()
        if stage == "download" and done < total and now - last[0] < _PROGRESS_INTERVAL_S:
            return
        last[0] = now
        session.emit("host.foamwin_progress", stage=stage, done=done, total=total)

    def work() -> None:
        try:
            result = fw.install(progress=progress, cancel=cancel, replace=replace)
        except StokesError as exc:
            log.warning("foamwin install failed", extra=fields(code=exc.code))
            session.emit("host.foamwin_failed", error=exc.to_dict())
        except Exception as exc:  # the thread must report, not die silently
            log.exception("foamwin install crashed")
            session.emit(
                "host.foamwin_failed",
                error={"code": "engine.foamwin_install_failed", "message": str(exc), "details": {}},
            )
        else:
            session.emit("host.foamwin_installed", **result)
        finally:
            with _lock:
                _running.clear()

    thread = threading.Thread(target=work, name="stokes-foamwin-install", daemon=True)
    with _lock:
        _running["thread"] = thread
    thread.start()
    return {"started": True}


@command("host.foamwin_cancel", params=(), requires_project=False, mutates=False)
def host_foamwin_cancel(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """Stop a running FoamWin download or unpack; the setup step itself runs to its end."""
    with _lock:
        cancel = _running.get("cancel")
    if cancel is None:
        return {"cancelled": False}
    cancel.set()
    return {"cancelled": True}
