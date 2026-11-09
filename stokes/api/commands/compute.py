"""The ``compute.*`` commands: which machine, and which OpenFOAM on it.

**The system settings, as commands** (the Settings spec §2; CLAUDE.md rule 4: behaviour is an API
**The system settings, as commands** (the Settings spec §2; CLAUDE.md rule 4: behaviour is
a script or an MCP caller is another.

Three things are stored, in one place:

* **which host** a mesh or run uses - a machine the user has already TRUSTED, named by its own name;
* **which install** on that host - the absolute path of an environment script;
* **whether to choose automatically**, which is the default and the reason a user needs no opinion.

**Nothing here runs an install.** `compute.add_install` records a path; it is validated as a path -
absolute, a real file, not a directory, not a symlink out of the tree - and only then offered to the
probe. The agent sources it inside the launcher it already uses, so a stored value cannot become a
way to execute something the launcher would not have run.

**The trust boundary is respected, not repeated.** A host must already be in the registry, so this
cannot introduce a machine; `host.trust` remains the only way to do that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.log import fields, get_logger
from stokes.workspace.paths import format_path

log = get_logger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.run.hosts import HostRegistry

#: Where the choice lives in the workspace document. **User scope only** - a project must not decide
#: Where the choice lives in the workspace document. **User scope only** - a project must not
COMPUTE_PATH: tuple[str, ...] = ("preferences", "compute")


def _store(ctx: CommandContext) -> Any:
    """The session's workspace service, created on first use.

    **`service_of` rather than a second constructor**: the workspace package keeps one service per
    session in the session's extensions, so a private instance here would read a different document
    from the Settings dialog's.
    """
    from stokes.api.commands.workspace import service_of

    return service_of(ctx.session)


def _stored(ctx: CommandContext) -> dict[str, Any]:
    """The `preferences.compute` of the EFFECTIVE document, or an empty mapping.

    **Effective, not the user layer**: a caller asking what is configured wants what a mesh would
    use, which is what the dialog shows. Writes still go to the user layer alone, because a project
    must not decide which machine it runs on.

    **The whole document, then the key.** `get(path, ...)` returns a WRAPPER and refuses a key that
    is not there - right for a caller asking about one setting, wrong for one asking "what is
    configured". Indexing the document makes an absent section an empty mapping, which is the honest
    answer before anything has been chosen.
    """
    document = _store(ctx).get(None, None).get("document", {})
    preferences = document.get("preferences", {}) if isinstance(document, dict) else {}
    found = preferences.get("compute", {}) if isinstance(preferences, dict) else {}
    return found if isinstance(found, dict) else {}


def _registry(ctx: CommandContext) -> HostRegistry:
    """The session's host registry (imported late: `stokes.run` pulls in the agent)."""
    from stokes.run.hosts import HostRegistry

    return HostRegistry.of(ctx.session)


def _known_host(ctx: CommandContext, host: str) -> str:
    """``host`` if the registry knows it, else a refusal naming what it does know.

    **A host this cannot introduce.** Adding a machine is `host.trust`, with its own fingerprint
    step; accepting an unknown name here would be a second, weaker way in.
    """
    known = {entry["name"] for entry in _registry(ctx).hosts()}
    if host not in known:
        raise ValidationError(
            f"no host called {host!r}; known hosts are: {', '.join(sorted(known)) or 'none'}",
            code="compute.unknown_host",
            details={"host": host, "known": sorted(known)},
        )
    return host


def default_host(ctx: CommandContext) -> str:
    """The host a mesh or run uses when the project does not name one.

    **The same rule `compute.get` shows**, without probing installs: an explicit host wins even
    when it is not currently available - the run is then `skipped` with the reason, which is the
    honest answer - and Auto takes the first host that answers. `local` is the last resort, so a
    machine with no probe at all still has a name for the result.

    **Read here, not stored into the project.** A project's Execution field is the explicit
    override; resolving an empty one at the moment of use keeps the user's Compute choice, and a
    project opened on another machine, from rewriting the document just to record a decision that
    belongs in Settings.
    """
    stored = _stored(ctx)
    host = str(stored.get("host") or "")
    if host and not bool(stored.get("auto", True)):
        return host
    registry = _registry(ctx)
    # **The common answer without probing the host list.** `hosts()` lists WSL distributions,
    # which spawns `wsl.exe -l -q`; a mesh start should not pay for that when this computer can
    # mesh. The Auto order is local first anyway (`compute.get` walks the same list), so this is
    # the same answer, not a shortcut that changes it.
    if registry.runner("local").available:
        return "local"
    for entry in registry.hosts():
        if entry.get("available"):
            return str(entry["name"])
    return host or "local"


def _install_problem(path: str) -> str | None:
    """Why a path cannot be an install location, or None when it can.

    **Checked here, on the machine that will use it** (the spec: "paths are checked on the HOST,
    never executed on the client"). Four rules, each for a reason:

    * **absolute**, because a relative path resolves against whatever the agent's working directory
      happens to be at the time it runs;
    * **an existing regular file**, because an environment SCRIPT is what gets sourced - a directory
      would be sourced as the empty string and a typo would look like a working install;
    * **not a symlink** pointing somewhere that is not a file, the same rule the staging whitelist
      uses: a link named ``env.sh`` pointing somewhere private is not an install;
    * **readable**, so the failure is "I cannot read that" now rather than at the first mesh.
    """
    import os
    from pathlib import Path

    target = Path(path)
    # **A table rather than a chain**, so each rule reads as a sentence: the first that applies is
    # the answer, and None means the path is usable.
    checks: tuple[tuple[bool, str], ...] = (
        (not target.is_absolute(), "the path must be absolute"),
        (not target.exists(), "there is nothing at that path"),
        (
            target.is_dir(),
            "that is a folder; give the environment script inside it, such as etc/bashrc",
        ),
        (not target.is_file(), "that is not a regular file"),
        (
            target.is_symlink() and not target.resolve().is_file(),
            "that link does not point at a file",
        ),
        (not os.access(target, os.R_OK), "that file cannot be read"),
    )
    for failed, why in checks:
        if failed:
            return why
    return None


@command(
    "compute.get",
    params=(),
    requires_project=False,
    mutates=False,
)
def _compute_state(ctx: CommandContext) -> dict[str, Any]:
    """What is configured, and what the hosts offer now.

    **One call for the whole section**, because its parts are useless apart: which host is chosen
    only
    means something next to the installs each host has. The reply also carries the **effective**
    choice - what a mesh would actually use right now - so a caller never has to re-derive the
    "Auto"
    rule.
    """
    stored = _stored(ctx)
    host = str(stored.get("host", "") or "")
    install = str(stored.get("install", "") or "")
    auto = bool(stored.get("auto", True)) or not host

    hosts = _registry(ctx).hosts()
    effective_host = host
    effective_install = install
    if auto:
        # **The preference order the UI shows**: a host that answers, and on it the newest supported
        # install. Derived here so a script and the dialog agree about what "Auto" picked.
        for entry in hosts:
            if entry.get("available"):
                effective_host = str(entry["name"])
                break
    # **An install the user chose is marked `user-added` here**, because this is the only layer that
    # knows it (claude-win2's 1630 checklist item 1). The agent scans a machine and cannot see a
    # preference; the UI reads the label off the reply. A stored path that the scan also found is
    # preference; the UI reads the label off the reply. A stored path that the scan also found
    # is still the user's choice, so it is marked by what was CHOSEN.
    installs: list[dict[str, Any]] = []
    if effective_host:
        reply_probe = _registry(ctx).probe(effective_host)
        record = reply_probe.get("record") or {}
        # **A chosen host with no chosen install uses that host's default**, which is also what a
        # mesh or run does. Leaving `effective_install` empty made the reply claim a host with no
        # OpenFOAM while `installs` listed one - measured in the Settings dialog after choosing WSL:
        # the install row showed `/opt/openfoam14/etc/bashrc` and the effective field was blank.
        if not effective_install:
            effective_install = str(record.get("default_install") or "")
        for entry in record.get("installs") or []:
            row = dict(entry)
            if install and row.get("bashrc") == install:
                row["found_by"] = "user-added"
            installs.append(row)
    return {
        "path": format_path(COMPUTE_PATH),
        "host": host,
        "install": install,
        "auto": auto,
        "effective_host": effective_host,
        "effective_install": effective_install,
        "hosts": hosts,
        "installs": installs,
    }


@command(
    "compute.set_default",
    params=(
        ParamDef(
            "host",
            "string",
            required=False,
            description="A trusted host, or empty for Auto.",
        ),
        ParamDef(
            "install",
            "string",
            required=False,
            description="The environment script to use on that host. Empty lets the host choose.",
        ),
    ),
    requires_project=False,
)
def _set_default(
    ctx: CommandContext, host: str | None = None, install: str | None = None
) -> dict[str, Any]:
    """Choose the machine and the install a mesh or run uses; empty means Auto.

    **Empty host means "choose for me", not "no host".** The spec wants the best available picked
    for
    the user, so clearing the choice has to mean something different from never having made one -
    which is why `auto` is stored rather than inferred from an empty string on read.
    """
    wanted_host = (host or "").strip()
    wanted_install = (install or "").strip()
    if wanted_host:
        _known_host(ctx, wanted_host)
    if wanted_install and not wanted_host:
        raise ValidationError(
            "an install belongs to a host: choose the host it is on as well",
            code="compute.install_needs_host",
        )
    store = _store(ctx)
    for key, value in (
        ("host", wanted_host),
        ("install", wanted_install),
        ("auto", not wanted_host),
    ):
        # **A DOTTED path** (`preferences.compute.host`), which is the spelling `parse` accepts. My
        # first version joined with `/`, and `parse` read the whole thing as ONE segment - so the
        # write asked for a key literally called `preferences/compute/host`.
        store.set(".".join((*COMPUTE_PATH, key)), value, "user", None)
    log.info(
        "compute default set", extra=fields(host=wanted_host or "auto", install=wanted_install)
    )
    return cast("dict[str, Any]", _compute_state(ctx))


@command(
    "compute.add_install",
    params=(
        ParamDef("host", "string", description="The trusted host the install is on."),
        ParamDef("path", "string", description="The environment script to use, absolute."),
        ParamDef(
            "select",
            "boolean",
            required=False,
            default=True,
            description="Use it straight away rather than only recording it.",
        ),
    ),
    requires_project=False,
)
def compute_add_install(
    ctx: CommandContext, host: str, path: str, select: bool = True
) -> dict[str, Any]:
    """Record an install location the user named, and optionally choose it.

    **The validation is the whole command.** Nothing is executed and nothing is stored until the
    path
    passes `_install_problem`: an install location becomes an environment script that the agent
    sources, so a value that reaches it must already be a file we can read. The refusal names what
    is
    wrong in words the user can act on - "that is a folder; give the environment script inside it" -
    rather than a code.

    **A bad path is refused, not stored.** Recording it and failing later at the first mesh would
    move the failure to wherever the user is not looking.
    """
    _known_host(ctx, host)
    problem = _install_problem(path)
    if problem is not None:
        raise ValidationError(
            f"{path}: {problem}",
            code="compute.bad_install",
            details={"host": host, "path": path, "problem": problem},
        )
    if select:
        return cast("dict[str, Any]", _set_default(ctx, host=host, install=path))
    return cast("dict[str, Any]", _compute_state(ctx))


@command(
    "compute.test",
    params=(
        ParamDef(
            "host",
            "string",
            required=False,
            description="The host to test; default: the chosen one.",
        ),
        ParamDef(
            "install",
            "string",
            required=False,
            description="An install to test before choosing it.",
        ),
    ),
    requires_project=False,
    mutates=False,
)
def compute_test(
    ctx: CommandContext, host: str | None = None, install: str | None = None
) -> dict[str, Any]:
    """Probe a host (and one install on it) and report what works, in plain words.

    **Read-only and bounded.** It re-runs `host.probe`, which reads a version and looks for the
    utilities; a test can be slow on a remote host but it cannot change anything, which is what
    makes
    the button safe next to a destructive one.

    **The reply says what a user needs to decide**, not a status code: the version, whether it is
    one
    Stokes drives, whether the utilities are there, and the cores and disk the spec asks for.
    """
    from stokes.run.hosts import HostRegistry

    chosen = _compute_state(ctx)
    wanted = (host or str(chosen.get("effective_host") or "")).strip()
    if not wanted:
        raise ValidationError(
            "no host to test: none is configured and nothing answered the probe",
            code="compute.no_host",
        )
    _known_host(ctx, wanted)
    probe = HostRegistry.of(ctx.session).probe(wanted, refresh=True)
    record = probe.get("record") or {}
    installs = list(record.get("installs") or [])
    wanted_install = (install or str(chosen.get("effective_install") or "")).strip()
    found = next((i for i in installs if i.get("bashrc") == wanted_install), None)
    if wanted_install and found is None:
        # **A test of something that is not there is a failure with a reason**, not an empty answer.
        return {
            "host": wanted,
            "available": False,
            "ok": False,
            "why": f"no install at {wanted_install} on {wanted}",
            "installs": installs,
        }
    return {
        "host": wanted,
        "available": bool(probe.get("available")),
        "ok": found is not None and bool(found.get("supported")),
        "why": (found or {}).get("why") or str(probe.get("why") or ""),
        "install": found,
        "installs": installs,
        "cores": record.get("cpu_count"),
        "vtk": record.get("vtk"),
    }
