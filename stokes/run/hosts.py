"""The per-session host registry (ADR-0005, T-144c).

One entry for `local` in M2; the SSH runner (T-159a) joins it with the same `Runner` protocol
and the
same two commands, so a mesh driver never learns which kind of host it is using.

## Why the record is cached

`host.probe` **sources a bashrc** — a process launch — so it is asked for once and remembered. A
mesh
driver that needs to know whether a host can mesh should not pay for that on every call, and the
answer
changes only when the install does, which is what `refresh=True` is for.

## Why `close` is not a `project_closed` hook

**A host outlives a project.** The agent, its loop and its port belong to the process, not to
the document
open in it; closing them when a project closes would make opening a second project re-source an
OpenFOAM
environment and start a second loop. So the registry closes runners only from `atexit`, through each
runner's own `atexit` registration.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

from stokes.log import fields, get_logger

from .errors import RunnerUnavailable, UnknownHost
from .local import LocalRunner
from .runner import Runner
from .ssh_hosts import read_hosts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session

log = get_logger(__name__)

#: The key this registry lives under in `Session.extensions`.
EXTENSION = "hosts"

#: The hosts M2 knows about. `local` is the whole list; SSH arrives with T-159a.
HOST_NAMES: tuple[str, ...] = ("local",)

#: The prefix a WSL distribution's host name carries, so a WSL row is distinguishable from an SSH
#: host a user typed (the Settings spec §2 lists them as different kinds).
#:
#: **A prefix rather than a stored record**: a WSL distro is discovered from the machine, so it must
#: not need an entry in `hosts.json` - a user who installs a new distro sees it after a Rescan, and
#: one who uninstalls it stops seeing it, with no file to edit in either case.
WSL_PREFIX = "wsl:"

#: The `host.probe` record keys that say what an install can run (ADR-0029, T-304).
INVENTORY_KEYS: tuple[str, ...] = (
    "solver_modules",
    "utilities",
    "libraries",
    "etc_functions",
    "etc_case_dicts",
    "tutorials_root",
)


def wsl_host_names() -> list[str]:
    """`wsl:<distro>` for every WSL distribution the machine has; empty off Windows.

    **The probe is the only place that knows about Windows**, and it already exists
    (`stokes_agent.probe.wsl_distros`, which decodes `wsl.exe -l -q`'s UTF-16). This names them for
    the host list; it does not run anything in them.
    """
    try:
        from stokes_agent.probe import wsl_distros
    except ImportError:  # pragma: no cover - the agent is installed on the machines that need it
        return []
    return [f"{WSL_PREFIX}{name}" for name in wsl_distros()]


def distro_of(name: str) -> str | None:
    """The distribution a `wsl:<distro>` name refers to, or None for any other host."""
    return name[len(WSL_PREFIX) :] if name.startswith(WSL_PREFIX) else None


_lock = threading.Lock()


class HostRegistry:
    """The hosts one session can use, and the runners behind them."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._runners: dict[str, Runner] = {}
        self._records: dict[str, dict[str, Any]] = {}
        #: The host file's records, read once. None means "not read yet", which is why an empty
        #: dict and "no file" are different states here.
        self._ssh_cache: dict[str, Any] | None = None
        #: The machine's WSL distributions, asked for once (`_wsl_names`). None: not asked yet.
        self._wsl_cache: list[str] | None = None
        self._guard = threading.Lock()

    @classmethod
    def of(cls, session: Session) -> HostRegistry:
        """The session's registry, created on first use, like `GeometryStore.of`."""
        with _lock:
            found = session.extensions.get(EXTENSION)
            if not isinstance(found, cls):
                found = cls(session)
                session.extensions[EXTENSION] = found
            return found

    def runner(self, name: str) -> Runner:
        """The runner for ``name``, built once. An unknown name is `run.unknown_host`.

        **`local` is built here; every other name comes from `hosts.json`** (T-159a). Reading the
        file on each call would re-read a small file per mesh query, so the records are cached like
        the probe results are - and `refresh_hosts` is how a caller says the file changed.
        """
        if name == "local":
            with self._guard:
                existing = self._runners.get(name)
                if existing is None:
                    existing = LocalRunner()
                    self._runners[name] = existing
                return existing
        distro = distro_of(name)
        if distro is not None:
            with self._guard:
                existing = self._runners.get(name)
                if existing is None:
                    from stokes.run.wsl_runner import WslRunner

                    existing = WslRunner(distro)
                    self._runners[name] = existing
                return existing
        record = self._ssh_records().get(name)
        if record is None:
            known = ", ".join(self.host_names())
            raise UnknownHost(
                f"no host {name!r}; known: {known}",
                details={"code": "run.unknown_host", "host": name, "known": self.host_names()},
            )
        with self._guard:
            existing = self._runners.get(name)
            if existing is None:
                from stokes.run.ssh_runner import SshRunner

                existing = SshRunner(record)
                self._runners[name] = existing
            return existing

    def host_names(self) -> list[str]:
        """`local` and every host in the file, in a stable order.

        **A broken `hosts.json` is reported, not fatal**: a session whose host list cannot be read
        must still open its project and mesh locally. So the error becomes one entry's `why` rather
        than an exception out of a listing.
        """
        names = list(HOST_NAMES)
        try:
            names += [record.name for record in read_hosts()]
        except Exception as exc:
            log.warning("the host list could not be read", extra=fields(error=str(exc)[:200]))
        # **WSL distributions are discovered, not stored** (the Settings spec §5): each installed
        # distro is a host the machine has, so the list must not need a `hosts.json` entry that a
        # user has to create by hand. Off Windows this adds nothing.
        names += self._wsl_names()
        return names

    def _wsl_names(self) -> list[str]:
        """The WSL hosts, asked for once per registry.

        **Once, because asking runs `wsl.exe`** - a process start, on the server's event loop, on
        every `host.list`, and the mesh panel asks for `host.list` as soon as a client connects.
        Measured before this cache the first screen waited on it (see `wsl_distros`). A distribution
        installed during the session shows after `refresh_hosts`, which the settings' Refresh calls.
        """
        with self._guard:
            if self._wsl_cache is not None:
                return list(self._wsl_cache)
        names = wsl_host_names()
        with self._guard:
            self._wsl_cache = names
        return list(names)

    def _ssh_records(self) -> dict[str, Any]:
        """The file's records, read once and cached. Never raises: an unreadable file is empty."""
        with self._guard:
            if self._ssh_cache is not None:
                return self._ssh_cache
        try:
            records = {record.name: record for record in read_hosts()}
        except Exception as exc:
            log.warning("the host list could not be read", extra=fields(error=str(exc)[:200]))
            records = {}
        with self._guard:
            self._ssh_cache = records
        return records

    def refresh_hosts(self) -> None:
        """Forget the cached host list and its runners, so the next call re-reads the file.

        **The runners go too**, because a record can change under a name: a different user, key or
        port for `buildbox` must not keep talking to the old one through a connection built from the
        previous record.
        """
        with self._guard:
            self._ssh_cache = None
            self._wsl_cache = None
            for name in [n for n in self._runners if n != "local"]:
                runner = self._runners.pop(name)
                with contextlib.suppress(Exception):
                    runner.close()

    def hosts(self) -> list[dict[str, Any]]:
        """Every host, with whether it can be used and what it last reported.

        An entry carries the record when one has been fetched, and **None otherwise** — which is
        different
        from "the host has nothing". A client that wants the record asks for it, and one that
        only wants
        the list does not pay for a probe.
        """
        out: list[dict[str, Any]] = []
        # **`host_names()`, not the constant.** `HOST_NAMES` is only `local`, so walking it meant
        # a host added through `host.add` was written to `hosts.json` and then **invisible**:
        # measured, `host.list` still returned `['local']` after a successful add. `host_names()`
        # is the method that reads the file, and it was already the one `runner()` uses.
        records = self._ssh_records()
        for name in self.host_names():
            runner = self.runner(name)
            entry: dict[str, Any] = {
                "name": name,
                "runner": runner.name,
                "available": runner.available,
                "why": runner.why,
                "record": self._records.get(name),
            }
            # **The address, because a name alone does not identify a host.** `buildbox` says
            # nothing about which machine it is, and a list of names is not something a user can
            # check - the UI showed `buildbox @` with nothing after it, measured. `local` has no
            # record and no address, which is correct: it is this machine.
            record = records.get(name)
            if record is not None:
                entry["host"] = record.host
                entry["user"] = record.user
                entry["port"] = record.port
            out.append(entry)
        return out

    def probe(self, name: str, refresh: bool = False) -> dict[str, Any]:
        """Ask a host what it has, with the answer remembered.

        **An unavailable host is a normal answer, not an error**: it returns
        ``{"available": False, "why": …}`` so `mesh.generate` (T-207) can write `skipped`
        without having
        to catch anything. An *unknown* host still raises, because that is a mistake in the caller.
        """
        runner = self.runner(name)
        if not runner.available:
            return {"host": name, "available": False, "why": runner.why, "record": None}
        with self._guard:
            cached = self._records.get(name)
            if cached is not None and not refresh:
                return {"host": name, "available": True, "why": "", "record": cached}
        try:
            record = runner.call("host.probe", refresh=refresh, timeout=60.0)
        except RunnerUnavailable as exc:
            # **A runner that was available when listed but failed to start is still an answer, not
            # an exception.** This is the WSL case: the distribution is installed, but its
            # interpreter is missing a module or the distro is gone. The UI shows the reason on the
            # row, and `mesh.generate` can write `skipped`, instead of a toast over a host list that
            # still says the host is fine.
            return {"host": name, "available": False, "why": str(exc), "record": None}
        with self._guard:
            self._records[name] = record
        return {"host": name, "available": True, "why": "", "record": record}

    def inventory(self, name: str = "local") -> dict[str, Any] | None:
        """What the host's default install can run (T-304), or None when it cannot be known.

        The six inventory keys of the probe record (`INVENTORY_KEYS`). **None, not empty lists, when
        the host is unavailable or its agent predates T-304**: "this install has no modules" and "we
        could not ask" classify a tutorial differently (`missing_module` against `unverified`).
        """
        answer = self.probe(name)
        record = answer.get("record")
        if not answer.get("available") or not isinstance(record, dict):
            return None
        if not all(key in record for key in INVENTORY_KEYS):
            return None
        return {key: record[key] for key in INVENTORY_KEYS}

    def close(self) -> None:
        """Close every runner. Idempotent, and called from `atexit` rather than a project hook."""
        with self._guard:
            runners = list(self._runners.values())
            self._runners.clear()
            self._records.clear()
        for runner in runners:
            runner.close()
