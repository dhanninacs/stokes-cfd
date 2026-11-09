"""license.*, engine.* and app.* read-only commands: what the badges and the About page show."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stokes.api.registry import CommandContext, command
from stokes.errors import StokesError
from stokes.log import get_logger

log = get_logger(__name__)


@command("license.info", mutates=False, requires_project=False)
def license_info(ctx: CommandContext) -> dict[str, Any]:
    """The edition this session runs as and the features it unlocks."""
    features = ctx.session.features
    info = features.info
    return {
        "edition": "licensed" if info is not None else "free",
        "features": sorted(features.flags),
        "holder": info.holder if info is not None else None,
        "expires": info.expires.isoformat() if info is not None and info.expires else None,
        "id": info.id if info is not None else None,
    }


@command("engine.info", mutates=False, requires_project=False)
def engine_info(ctx: CommandContext) -> dict[str, Any]:
    """The solver engine attached to this session. None before milestone M3.

    M3, not M2: M2 is the agent, attachment and meshing milestone, while the solver engine
    (`foamRun`) is M3 — which is what ``stokes/engines`` and the roadmap's M3 row say. This said M2
    until 2026-10-06, and the Run button repeated it.
    """
    return {"attached": False, "name": None, "version": None}


#: The notices file, from the repository root. **The packaged copy is tried first**, so a shipped
#: build with no checkout can still show it (T-177).
_NOTICES_NAME = "THIRD-PARTY-NOTICES.md"
#: A cap on what `app.notices` will read. The file is a licence list, not data: a "notices" file of
#: a megabyte is a wrong file, and reading it into a JSON reply would be the whole cost of About.
MAX_NOTICES_BYTES = 1024 * 1024


@command("app.info", mutates=False, requires_project=False)
def app_info(ctx: CommandContext) -> dict[str, Any]:
    """Where Stokes keeps its files, and what it is running on. **What the About page reads.**

    **No machine name, no user name, no environment dump** (T-177; CLAUDE.md rule 3). `platform` is
    `platform.platform()`, which is the OS and its version - not `platform.node()`, which is the
    machine's own name and the thing the IP gate exists to keep out of a reply. The test asserts
    that no value contains either, so this is checked rather than intended.

    `home` and `workspace` DO contain the user's path, and that is deliberate: they are shown to
    their owner, and on Windows they carry the login name, so the test allows a path to be a path.

    **`hosted` is omitted, not guessed.** The brief asks whether the session runs under the desktop
    host; `stokes/app/host.py` has a `frameless` window option and **no session flag**, so there is
    nothing to report. Inventing one - a module named `stokes.app` being importable, say - would
    answer a question nobody asked with a fact that is not the one meant.
    """
    import platform

    from stokes import __version__
    from stokes.workspace.merge import stokes_home, user_file

    home = stokes_home()
    project = ctx.session.project_path
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "home": str(home),
        "workspace": {
            "user": str(user_file(home)),
            "project": str(project / "workspace.json") if project is not None else None,
        },
    }


@command("app.notices", mutates=False, requires_project=False)
def app_notices(ctx: CommandContext) -> dict[str, Any]:
    """The third-party notices, so a shipped build can show them.

    **Packaged first, then the repository.** A wheel has no checkout, so the file is mapped into the
    package (`pyproject.toml`, `force-include`); a development tree has no packaged copy, so the
    repository root is the fallback. `source` says which one answered, because "the notices" being
    the build's notices or the checkout's is a fact the About page can show and a reader may want.
    """
    del ctx
    packaged, repository = _notices_paths()
    for path, source in ((packaged, "packaged"), (repository, "repository")):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_NOTICES_BYTES:
                raise StokesError(
                    f"{path} is {size} bytes; the notices are capped at {MAX_NOTICES_BYTES}",
                    code="app.notices_too_large",
                    details={"path": str(path), "size": size},
                )
            return {"text": path.read_text(encoding="utf-8"), "source": source}
        except OSError as exc:
            log.warning("could not read the notices", extra={"path": str(path), "error": str(exc)})
    raise StokesError(
        f"no {_NOTICES_NAME} in either place: {packaged} or {repository}",
        code="app.notices_missing",
        details={"packaged": str(packaged), "repository": str(repository)},
    )


def _notices_paths() -> tuple[Path, Path]:
    """The packaged copy and the repository copy, in that order of preference.

    **Both are computed, never assumed to exist.** The packaged path goes through
    `importlib.resources`, which is what works from a zip or a wheel; the repository path is three
    parents up from this module (`stokes/api/commands/info.py` -> the root).
    """
    packaged = Path(__file__).resolve().parent.parent.parent / "app" / "assets" / _NOTICES_NAME
    repository = Path(__file__).resolve().parents[3] / _NOTICES_NAME
    return packaged, repository
