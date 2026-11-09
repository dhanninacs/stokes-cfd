"""A session: one open project, its undo stack, events, journal and licence features.

Extensions. Optional subsystems keep their per-session services in ``Session.extensions`` (the
geometry store, the design tables, the workspace), so the session imports none of them. A service
that has to follow the project implements the hooks of :class:`SessionExtension`; the ``project.*``
commands call them through :meth:`Session.notify_extensions` once the session's state has changed.
Two more hooks are optional and called from elsewhere: ``on_event`` by :meth:`Session.emit` after
every event (the operations service hears ``node.changed`` there, ADR-0021 decision 8.8, without a
dependency of the model on it), and ``settle`` by :meth:`Session.settle`, which journal replay
calls after every record so that background work a record started (an operation's run) has ended
before the next record reads its result (ADR-0022 decision 10.1).
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, Protocol

from stokes.api import legacy
from stokes.api.events import EventBus
from stokes.api.journal import Journal
from stokes.api.registry import CommandContext, CommandRegistry
from stokes.api.summaries import announce
from stokes.errors import LicenceFeatureMissing, NoProject
from stokes.license import Features
from stokes.log import fields, get_logger
from stokes.model import Project, UndoStack

log = get_logger(__name__)

Hook = Literal["project_opened", "project_saved", "project_closed", "project_copied"]
"""The hooks :meth:`Session.notify_extensions` accepts: the methods of :class:`SessionExtension`.

**`project_copied` is not `project_saved`** (Opus F3). Save a copy writes a second folder while
the session STAYS in the original, and a store that keeps a folder of its own - the study store's
tables, the workspace store's overrides - must not follow the copy: telling them `project_saved`
pointed them at the copy, so the next table the user wrote from the original session landed in
it. `project_copied` says "a snapshot of this project now exists at ``path``" and leaves the
session's own folder alone."""


class SessionExtension(Protocol):
    """A subsystem's per-session service, as kept in :attr:`Session.extensions`.

    Every hook is optional: an extension implements the ones it needs and
    :meth:`Session.notify_extensions` skips the rest, which is why this class describes the
    signatures and does not have to be inherited. A hook runs after the session's state has
    changed and before the matching event (``project.opened``, ``project.saved``,
    ``project.closed``) is emitted, so anything told of the change finds the service in step with
    it. It runs on the thread that ran the command and should return quickly: slow work belongs on
    a thread of its own. An exception it raises is logged and goes no further.
    """

    def project_opened(self, project: Project, path: Path | None) -> None:
        """``project`` is the session's project now: created (``project.new``, with ``path``
        None until it has a folder) or opened. It replaces any earlier project, which gets no
        ``project_closed``: whatever belonged to that one is dropped here."""
        ...

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        """``project`` was written to the folder ``path``. ``previous`` is where the session had
        it before: None when it had no folder yet, ``path`` for a plain save, another folder for
        Save as, which is when files that live beside the project move with it."""
        ...

    def project_closed(self) -> None:
        """The session has no project any more (``project.close``)."""
        ...

    def project_copied(self, project: Project, path: Path, previous: Path | None = None) -> None:
        """A SNAPSHOT of ``project`` now exists at ``path``; the session stays where it is.

        **Not `project_saved`**: a store that keeps a folder of its own must not follow the copy
        (Opus F3). Implemented by the stores that contribute files to the copy - geometry, run,
        mesh, selection - and not by the stores that own a folder, which is the whole point.
        ``previous`` is the folder the copy came from; None means the session's own.
        """
        ...

    def on_event(self, name: str, data: dict[str, Any]) -> None:  # Any: JSON payload
        """Every event the session emits, after its listeners, with the data they got (node
        events carry their summaries). It runs on the thread that emitted, a command's or a
        background job's, and must return at once; what it raises is logged."""
        ...

    def settle(self, timeout: float | None, coalesce: bool = False) -> None:
        """Return once the background work that the commands so far started, and that a later
        command may read, has ended (an operation's run), or ``timeout`` seconds have passed.
        Journal replay calls it between records; what it raises stops the replay.

        ``coalesce`` lets work that is still waiting on its own debounce wait for that debounce:
        a burst of changes in one window then becomes ONE run instead of one per change. A caller
        that must see the result in the same record does not pass it."""
        ...


class Session:
    def __init__(
        self,
        registry: CommandRegistry | None = None,
        *,
        journal_path: Path | None = None,
        features: Features | None = None,
    ) -> None:
        if registry is None:
            from stokes.api import default_registry

            registry = default_registry()
        self.registry = registry
        self.project: Project | None = None
        self.project_path: Path | None = None
        #: The project's revision as of the last write to disk, or -1 when it has never been
        #: saved. **The dirty test**, and it is a comparison rather than a boolean because the model
        #: is the one that knows: `Project.revision` counts every `_mark`, which is every path
        #: that changes the model, and a save sets this to match. A flag the front end kept would go
        #: wrong
        #: the first time it was not consulted - and a stale "there is nothing to lose" is exactly
        #: the failure the Close prompt exists to prevent.
        self.saved_revision: int = -1
        #: When unsaved work from an EARLIER session was found beside the project (its recovery
        #: copy, ISO time), set by `project.open` and cleared once the user recovers, discards,
        #: saves or closes. Not the copy this session's own autosave writes, which is no news.
        self.recovery_at: str | None = None
        self.undo = UndoStack()
        self.events = EventBus()
        self.journal = Journal(journal_path)
        self.features = features or Features.free()
        # Per-session services owned by optional subsystems (geometry meshes, the workspace),
        # keyed by subsystem name, so they never need to touch this class. One that implements
        # the SessionExtension hooks hears when the project is opened, saved and closed.
        self.extensions: dict[str, Any] = {}
        self._depth = 0

    def require_project(self) -> Project:
        if self.project is None:
            raise NoProject("no project is open; run project.new or project.open first")
        return self.project

    def execute(self, command: str, /, **params: Any) -> Any:  # Any: JSON in, JSON out
        """Validate, run and journal a command. Nested calls are not journaled twice.

        ``command`` is positional-only so that commands may have a parameter
        called ``name``. Old names from before a rename are accepted
        (:mod:`stokes.api.legacy`): the command's, its parameters' and node paths'.
        They are replaced before validation, so the journal records current names.
        """
        definition = self.registry.get(command)
        name = definition.name
        if definition.feature and not self.features.has(definition.feature):
            raise LicenceFeatureMissing(
                f"command {name!r} needs the {definition.feature!r} feature",
                details={"feature": definition.feature, "command": name},
            )
        clean = definition.validate(legacy.rewrite_params(legacy.rename_parameters(name, params)))
        if definition.requires_project:
            self.require_project()
        ctx = CommandContext(self)
        self._depth += 1
        try:
            result = definition.handler(ctx, **clean)
        finally:
            self._depth -= 1
        if definition.mutates and self._depth == 0:
            self.journal.append(name, {**clean, **ctx.journal_extra})
        log.debug("command", extra=fields(command=name, mutates=definition.mutates))
        return result

    def project_opened_data(self) -> dict[str, Any] | None:
        """The payload of ``project.opened`` for the open project, or ``None`` with none open.

        One builder for the two places that announce it: ``project.open``/``project.new`` emit it
        when the project changes, and the server sends it to a client that connects while a project
        is already open - that client missed the emit and would otherwise show the start screen.
        """
        if self.project is None:
            return None
        return {"name": self.project.name, "path": str(self.project_path or "")}

    def emit(self, event: str, **data: Any) -> None:  # Any: JSON payload
        """Emit ``event``. A node event carries the summary of its node (and of the nodes the
        change reached), see :func:`stokes.api.summaries.announce`, so that a client can patch its
        copy of the tree. This is the one place that does it: the events of the commands and of the
        geometry store, which emits from its import threads, all come through here."""
        if self.project is not None:
            try:
                data = announce(self.project, event, data)
            except Exception:
                # The event is still worth sending: a client that finds no summary fetches the tree.
                log.exception("node event without a summary", extra=fields(event=event))
        self.events.emit(event, **data)
        for name, extension in list(self.extensions.items()):
            try:
                hook = getattr(extension, "on_event", None)
                if callable(hook):
                    hook(event, data)
            except Exception:
                log.exception(
                    "session extension failed", extra=fields(extension=name, hook="on_event")
                )

    @contextlib.contextmanager
    def hold_changes(self) -> Iterator[None]:
        """Hold re-runs for the block, then start what the block left pending.

        Replay uses it to walk a stretch of change-only records as one debounce window instead of
        forcing a run per record; an extension with no such concept simply sees the block.
        """
        with contextlib.ExitStack() as stack:
            for extension in list(self.extensions.values()):
                hook = getattr(extension, "hold_debounce", None)
                if callable(hook):
                    stack.enter_context(hook())
            yield

    def settle(self, timeout: float | None = None, *, coalesce: bool = False) -> None:
        """Wait for the background work that later commands may read (an operation's run): the
        ``settle`` hook of every extension that has one. Journal replay calls it after every
        record, so a record that follows an operation finds its outputs (ADR-0022 10.1).

        ``coalesce`` is passed on to the hooks: replay uses it so a burst of changes recorded inside
        one debounce window runs the operation once, as it did when the journal was written, rather
        than once per record."""
        for extension in list(self.extensions.values()):
            hook = getattr(extension, "settle", None)
            if not callable(hook):
                continue
            # An extension written before this parameter: ask what it accepts rather than catching
            # the TypeError, which would also swallow a real one from inside the hook.
            accepts = "coalesce" in inspect.signature(hook).parameters or any(
                p.kind is inspect.Parameter.VAR_KEYWORD
                for p in inspect.signature(hook).parameters.values()
            )
            if accepts:
                hook(timeout, coalesce=coalesce)
            else:
                hook(timeout)

    def notify_extensions(self, hook: Hook, **kwargs: Any) -> None:  # Any: the hook's arguments
        """Call ``hook`` with ``kwargs`` on every extension that has it.

        One that raises is logged and skipped: a failing subsystem must not fail the command
        that told it of the project change, nor keep the others from hearing of it.
        """
        for name, extension in list(self.extensions.items()):  # a hook may add an extension
            try:
                method = getattr(extension, hook, None)
                if callable(method):
                    method(**kwargs)
            except Exception:
                log.exception("session extension failed", extra=fields(extension=name, hook=hook))
