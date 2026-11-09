"""The ``host.*`` commands: which machines can mesh, and what they have (T-144c).

**Not `engine.*`.** `engine.info` means the solver engine, which is M3's; these are about the
machines
Stokes runs OpenFOAM *on*. Two different questions, so they get two different names.

Both are read-only and neither needs a project: the host list is what a user sees before they have
opened anything, and asking whether a machine can mesh does not depend on what is open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.log import fields, get_logger
from stokes.run.errors import UnknownHost

log = get_logger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only, so `stokes.run` stays out of the module load
    from stokes.run.hosts import HostRegistry


def _registry(ctx: CommandContext) -> HostRegistry:
    """The session's host registry.

    Imported inside the function because `stokes.run` pulls in the agent, and `import
    stokes.api` has a
    300 ms budget it must not spend on a module most sessions never use.
    """
    from stokes.run.hosts import HostRegistry

    return HostRegistry.of(ctx.session)


@command("host.list", params=(), requires_project=False, mutates=False)
def host_list(ctx: CommandContext) -> dict[str, Any]:
    """Every mesh host this session can use.

    In M2 that is `local` alone; the SSH runner (T-159a) joins the list without changing this
    command or
    its reply.
    """
    return {"hosts": _registry(ctx).hosts()}


@command(
    "host.probe",
    params=(
        ParamDef("host", "string", required=False, default="local", description="The host to ask."),
        ParamDef(
            "refresh",
            "boolean",
            required=False,
            default=False,
            description="Ask again rather than using the remembered answer.",
        ),
    ),
    requires_project=False,
    mutates=False,
)
def host_probe(ctx: CommandContext, host: str = "local", refresh: bool = False) -> dict[str, Any]:
    """What a host has: its OpenFOAM installs, its utilities and its resources.

    **A host that cannot mesh answers rather than raises** — ``available: False`` with a reason
    — so a
    caller can show it and a mesh driver can write `skipped`. An *unknown* host raises
    `run.unknown_host`, because that is a mistake in the call rather than a fact about a machine.
    """
    return _registry(ctx).probe(host, refresh=refresh)


@command(
    "host.fingerprint",
    params=(ParamDef("host", "string", description="The host whose key to show."),),
    requires_project=False,
    mutates=False,
)
def host_fingerprint(ctx: CommandContext, host: str) -> dict[str, Any]:
    """The host's key as `SHA256:…`, **for a person to compare** before trusting it.

    **Shown, never trusted.** The value is what a user checks against what whoever runs the machine
    told them; a client that fetched this and confirmed on the user's behalf would be trusting the
    network, which is exactly what the confirmation exists to avoid.
    """
    from stokes.run.ssh import host_key_fingerprint

    record = _ssh_record(ctx, host)
    return {"host": host, "fingerprint": host_key_fingerprint(record)}


@command(
    "host.trust",
    params=(
        ParamDef("host", "string", description="The host to trust."),
        ParamDef(
            "fingerprint",
            "string",
            description="The SHA256 the user checked. A different key is REFUSED.",
        ),
        ParamDef(
            "replace",
            "boolean",
            required=False,
            default=False,
            description="Replace an existing DIFFERENT key for this host. Refused without it.",
        ),
    ),
    requires_project=False,
)
def host_trust(
    ctx: CommandContext, host: str, fingerprint: str, replace: bool = False
) -> dict[str, Any]:
    """Add the host's key to our `known_hosts`, having checked it is the one confirmed.

    **The comparison is the point**: `host.trust` without it would write whatever the network
    **The comparison is the point**: `host.trust` without it would write whatever the network

    **A changed key is its own error** (`host.key_changed`), because an unknown key may be a
    machine nobody has visited and a changed one is what an interception looks like - telling them
    apart is the difference between "carry on" and "stop".
    """
    from stokes.run.ssh import trust_host

    record = _ssh_record(ctx, host)
    # **The reply says which it did** (Opus finding 4): a trust that REPLACED an existing key is a
    # different event from one that added a new host, and a caller must be able to show that.
    result = trust_host(record, fingerprint, replace=replace)
    log.info(
        "a host key was trusted",
        extra=fields(host=host, fingerprint=result["fingerprint"], replaced=result["replaced"]),
    )
    return {"trusted": True, **result}


def _ssh_record(ctx: CommandContext, name: str) -> Any:
    """The `SshHost` for a name, or `run.unknown_host`.

    **`local` is not an ssh host**, and a caller asking to trust it has confused two things - so the
    refusal says which names are ssh hosts rather than reporting a missing file.
    """
    from stokes.run.ssh_hosts import read_hosts

    for record in read_hosts():
        if record.name == name:
            return record
    known = ", ".join(record.name for record in read_hosts()) or "(none)"
    raise UnknownHost(
        f"no ssh host {name!r}; the hosts file names: {known}",
        details={"code": "run.unknown_host", "host": name},
    )


@command(
    "host.add",
    params=(
        ParamDef("name", "string", description="A short name for the list, such as buildbox."),
        ParamDef("host", "string", description="The machine's address or name."),
        ParamDef("user", "string", description="The user to connect as."),
        ParamDef("port", "integer", required=False, default=22, description="The SSH port."),
        ParamDef(
            "key",
            "string",
            required=False,
            description="An identity file, or empty for the agent's own key.",
        ),
        ParamDef(
            "python",
            "string",
            required=False,
            default="python3",
            description="The interpreter on that machine.",
        ),
    ),
    requires_project=False,
)
def host_add(
    ctx: CommandContext,
    name: str,
    host: str,
    user: str,
    port: int = 22,
    key: str | None = None,
    python: str = "python3",
) -> dict[str, Any]:
    """Record a remote host in `hosts.json`, **WITHOUT trusting it**.

    **Adding and trusting are two steps on purpose** (the Settings spec §2, and `host.trust`'s own
    design): this writes the address, and the key is confirmed separately by a person reading a
    fingerprint. A single command that did both would have to trust whatever the network answered,
    which is the whole thing the fingerprint step exists to prevent.

    **Every field goes through the module's own validator**, so what lands in the file is what the
    runner can use: a name that becomes a path segment, an address and user that cannot be read as
    an
    ssh option, a port in range, an identity path that is a file. Nothing here builds a value
    itself.
    """
    from stokes.run import ssh_hosts
    from stokes.run.ssh_hosts import SshHost

    checked = SshHost(
        name=ssh_hosts.check_name(name),
        host=ssh_hosts.check_host(host),
        user=ssh_hosts.check_host(user, field="user"),
        port=_check_port(port),
        key=ssh_hosts.check_key(key) or "",
        python=ssh_hosts.check_python(python),
        added=_stamp(),
    )
    existing = {record.name: record for record in ssh_hosts.read_hosts()}
    if checked.name in existing:
        raise ValidationError(
            f"there is already a host called {checked.name!r}; remove it first to change it",
            code="host.exists",
            details={"host": checked.name},
        )
    ssh_hosts.write_hosts([*existing.values(), checked])
    # **The registry re-reads the file**, or the new host would not appear until the next restart.
    _registry(ctx).refresh_hosts()
    return {"added": checked.name, "target": checked.target, "trusted": False}


@command(
    "host.remove",
    params=(
        ParamDef("host", "string", description="The host to forget."),
        ParamDef(
            "fingerprint",
            "boolean",
            required=False,
            default=False,
            description="Also remove its key from our known_hosts.",
        ),
    ),
    requires_project=False,
)
def host_remove(ctx: CommandContext, host: str, fingerprint: bool = False) -> dict[str, Any]:
    """Forget a host, and optionally its key.

    **The key is a separate decision** (the `fingerprint` flag): the record is an address the user
    typed, and the key is a trust decision they made. Removing the second silently would mean that
    re-adding the same machine asks for confirmation again with no sign anything changed - and
    removing only the first is the common case, "I no longer want this in the list".

    `local` cannot be removed: it is not a record, it is this machine.
    """
    from stokes.run import ssh_hosts

    record = _ssh_record(ctx, host)
    kept = [entry for entry in ssh_hosts.read_hosts() if entry.name != record.name]
    ssh_hosts.write_hosts(kept)
    removed_key = False
    if fingerprint:
        removed_key = _forget_key(record)
    _registry(ctx).refresh_hosts()
    log.info("host removed", extra=fields(host=record.name, key=removed_key))
    return {"removed": record.name, "key_removed": removed_key}


def _forget_key(record: Any) -> bool:
    """Drop this host's line from OUR `known_hosts`; whether anything was dropped.

    **`ssh-keygen -R` against the private file**, which is the same tool `host.trust` uses to write
    it - so the file's format stays ssh's business rather than ours.
    """
    import shutil
    import subprocess

    from stokes.run.ssh_hosts import known_hosts_path

    target = known_hosts_path()
    if not target.is_file():
        return False
    # **Resolved to an absolute path, and refused when absent.** `ssh-keygen` on PATH is a partial
    # path (`S607`), which is a real hazard: a directory earlier in PATH could supply it. And an
    # absent tool is "could not remove", not a crash - the record is gone either way, which is what
    # the caller asked for.
    tool = shutil.which("ssh-keygen")
    if tool is None:
        return False
    name = record.host if record.port == 22 else f"[{record.host}]:{record.port}"
    before = target.read_text(encoding="utf-8")
    try:
        subprocess.run(  # noqa: S603 - an absolute path, an argv list, no shell
            [tool, "-R", name, "-f", str(target)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return target.read_text(encoding="utf-8") != before


def _check_port(port: int) -> int:
    """The port, or a refusal.

    **The same rule the reader applies** (`_read_record`), so a value this accepts cannot be one the
    next read rejects.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValidationError(
            f"{port!r} is not a port; use 1 to 65535",
            code="host.bad_port",
            details={"port": port},
        )
    return port


def _stamp() -> str:
    """The timestamp a host record carries.

    **The model's own `_now`**, imported here rather than copied: a host's `added` and a project's
    `created` are the same kind of value, and two spellings of "now" would drift.
    """
    from stokes.model.project import _now

    return _now()
