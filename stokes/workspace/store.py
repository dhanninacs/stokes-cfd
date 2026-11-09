"""The per-session workspace service (ADR-0015 §2).

One :class:`WorkspaceStore` per Session, kept in ``session.extensions["workspace"]``
by ``stokes.api.commands.workspace``. It reads the user's and the project's
workspace files (again only when they change on disk), builds the effective
workspace and performs the writes the ``workspace.*`` commands offer, calling
``notify("workspace.changed", {"scope": ..., "path": ...})`` after each. It follows the
session's project, the project layer and the carrying of its overrides on Save as, through the
session's extension hooks.

The workspace is not project model state: a write goes to its file at once, it is
not on the project's undo stack and ``project.json`` never holds it. API writes are
strict (a value that reading would drop is refused with WorkspaceInvalid); files
are lenient (bad values become warnings and the layer below shows through). The
API runs one command at a time (the server serialises them on its event loop), so
the store needs no locks.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stokes.workspace import catalog
from stokes.workspace.access import (
    assign,
    remove,
    resolve_existing,
    resolve_write,
    value_at,
)
from stokes.workspace.errors import ProjectFolderRequired, WorkspaceInvalid, WorkspaceIOError
from stokes.workspace.files import (
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    FileState,
    contained,
    depth_ok,
    keep_aside,
    parse_document,
    read_file,
    remove_file,
    signature,
    write_file,
)
from stokes.workspace.merge import (
    Effective,
    effective,
    path_under,
    project_file,
    source_of,
    stokes_home,
    under,
    user_file,
)
from stokes.workspace.paths import JSON, Loc, format_path, parse
from stokes.workspace.rules import Finding, json_copy
from stokes.workspace.schema import METADATA, SCHEMA_VERSION, SECTIONS, sanitise

Scope = Literal["user", "project"]
SCOPES: tuple[Scope, ...] = ("user", "project")
Notify = Callable[[str, dict[str, JSON]], None]


def _ignore(event: str, data: dict[str, JSON]) -> None:
    """The default ``notify``: a store without listeners."""


@dataclass(frozen=True)
class Layer:
    """One scope's file as last read, its sanitised document and what was noticed."""

    scope: Scope
    file: FileState
    clean: dict[str, JSON]
    findings: tuple[Finding, ...]


class WorkspaceStore:
    """Effective workspace and every write to it, for one session.

    ``home`` overrides the user's Stokes folder (otherwise ``$STOKES_HOME`` or
    ``~/.stokes``, read at each use); ``project_dir`` is the project folder the
    session has now, kept current through the session's extension hooks
    (:meth:`project_opened`, :meth:`project_saved`, :meth:`project_closed`).
    """

    def __init__(
        self,
        *,
        home: Path | None = None,
        notify: Notify | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._home = home
        self._notify: Notify = notify or _ignore
        self._project_dir = project_dir
        self._cache: dict[Path, Layer] = {}

    @property
    def home(self) -> Path:
        return self._home if self._home is not None else stokes_home()

    # ---------------------------------------------------------------------------------- reading
    def file_for(self, scope: Scope, project_dir: Path | None) -> Path:
        if scope == "user":
            return user_file(self.home)
        if project_dir is None:
            raise ProjectFolderRequired(
                "project scope needs a project saved to a folder: open one, or save this one "
                "first (project.save path=...)"
            )
        return project_file(project_dir)

    def layer(self, scope: Scope, project_dir: Path | None = None) -> Layer:
        """The scope's file, read again only when its size or modification time changed."""
        path = self.file_for(scope, project_dir)
        cached = self._cache.get(path)
        if cached is not None and cached.file.signature == signature(path):
            return cached
        state = read_file(path, scope, within=project_dir if scope == "project" else None)
        clean: dict[str, JSON] = {}
        findings: list[Finding] = []
        if state.exists and state.readable:
            clean, findings = sanitise(state.document, scope)
        layer = Layer(scope, state, clean, (*state.findings, *findings))
        self._cache[path] = layer
        return layer

    def _layers(self, project_dir: Path | None) -> list[Layer]:
        layers = [self.layer("user")]
        if project_dir is not None:
            layers.append(self.layer("project", project_dir))
        return layers

    def effective(self, project_dir: Path | None) -> Effective:
        """Defaults, then the user's document, then the project's overrides (with a project).

        **A layout that lacks a built-in panel gains it here.** The panel list is the BUILD's, and a
        stored document is older than it by definition — a workspace saved before `mesh`,
        `solve` and
        `results` existed would otherwise hide three panels the client can render, with no way for a
        user to reach them. This is the merge boundary, so it is where the two lists meet.
        """
        layers = self._layers(project_dir)
        findings = [finding for layer in layers for finding in layer.findings]
        return effective([(layer.scope, layer.clean) for layer in layers], findings)

    def get(self, path: str | None, project_dir: Path | None) -> dict[str, JSON]:
        """The effective document with sources and warnings, or one value of it."""
        result = self.effective(project_dir)
        if path is None:
            return {
                "version": SCHEMA_VERSION,
                "document": result.document,
                "sources": result.sources,
                "warnings": _dicts(result.findings),
                "files": self._files(project_dir),
            }
        loc = resolve_existing(result.document, parse(path))
        canonical = format_path(loc)
        return {
            "path": canonical,
            "value": json_copy(value_at(result.document, loc)),
            "source": source_of(result.sources, loc),
            "sources": {k: v for k, v in result.sources.items() if path_under(k, canonical)},
            "warnings": _dicts(f for f in result.findings if under(f, loc)),
        }

    def _files(self, project_dir: Path | None) -> dict[str, JSON]:
        files: dict[str, JSON] = {"user": _file_info(self.layer("user").file), "project": None}
        if project_dir is not None:
            files["project"] = _file_info(self.layer("project", project_dir).file)
        return files

    def export(
        self, scope: str, sections: Sequence[str] | None, project_dir: Path | None
    ) -> dict[str, JSON]:
        """A document as JSON text: the effective workspace, or one scope's sanitised overrides."""
        if scope == "effective":
            document = self.effective(project_dir).document
        elif scope in SCOPES:
            document = {"version": SCHEMA_VERSION, **self.layer(_scope(scope), project_dir).clean}
        else:
            raise WorkspaceInvalid(
                f"scope must be effective, user or project; got {scope!r}", details={"scope": scope}
            )
        if sections is not None:
            wanted = _section_names(sections, document)
            document = {
                "version": SCHEMA_VERSION,
                **{s: document[s] for s in wanted if s in document},
            }
        return {
            "scope": scope,
            "sections": [key for key in document if key not in METADATA],
            "text": json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        }

    def presets(self) -> dict[str, JSON]:
        presets, notes = catalog.list_all(self.home)
        return {"presets": [preset.summary() for preset in presets], "warnings": _dicts(notes)}

    # ---------------------------------------------------------------------------------- writing
    def set(self, path: str, value: JSON, scope: str, project_dir: Path | None) -> dict[str, JSON]:
        """Set one value in a scope's document; refused when reading would drop it."""
        where = _scope(scope)
        segments = parse(path)
        value = _api_value(value)
        layer, view, lower = self._view(where, project_dir)
        target = resolve_write(view, segments)
        document = json_copy(layer.file.document) if layer.file.readable else {}
        assign(document, target, value, view)
        clean = self._check(document, target.root, layer, lower)
        if _has(clean, target.root):
            _put(document, target.root, value_at(clean, target.root))
        notes = self._write(layer, project_dir, document)
        canonical = format_path(target.loc)
        self._notify("workspace.changed", {"scope": where, "path": canonical})
        result = self.effective(project_dir)
        source = source_of(result.sources, target.loc)
        reply: dict[str, JSON] = {
            "scope": where,
            "path": canonical,
            "value": json_copy(_maybe(document, target.loc)),
            "effective": json_copy(_maybe(result.document, target.loc)),
            "source": source,
            "warnings": _dicts([*notes, *(f for f in result.findings if under(f, target.root))]),
        }
        if source not in (where, "default"):
            reply["note"] = (
                f"the {source} scope overrides {canonical}; set it there to change what is shown"
            )
        return reply

    def reset(self, scope: str, path: str | None, project_dir: Path | None) -> dict[str, JSON]:
        """Delete a scope's document, or remove one key from it so the layer below shows through."""
        where = _scope(scope)
        layer = self.layer(where, project_dir)
        if path is None:
            removed = remove_file(layer.file.path)
            self._cache.pop(layer.file.path, None)
            if removed:
                self._notify("workspace.changed", {"scope": where, "path": None})
            return {"scope": where, "path": None, "removed": removed}
        segments = parse(path)
        document = json_copy(layer.file.document) if layer.file.readable else {}
        loc = remove(document, segments)
        if loc is None:
            return {"scope": where, "path": format_path(segments), "removed": False}
        self._write(layer, project_dir, document)
        canonical = format_path(loc)
        self._notify("workspace.changed", {"scope": where, "path": canonical})
        return {"scope": where, "path": canonical, "removed": True}

    def apply_preset(self, name: str, scope: str, project_dir: Path | None) -> dict[str, JSON]:
        """Replace, in a scope, every section the preset carries."""
        where = _scope(scope)
        preset = catalog.find(name, self.home)
        if not preset.document:
            raise WorkspaceInvalid(
                f"preset {name!r} holds no valid section to apply",
                details={"warnings": _dicts(preset.findings)},
            )
        notes = self._replace_sections(where, project_dir, preset.document)
        reply = self._replaced(
            where, project_dir, list(preset.document), [*preset.findings, *notes]
        )
        return {"name": preset.name, "title": preset.title, **reply}

    def import_text(self, text: str, scope: str, project_dir: Path | None) -> dict[str, JSON]:
        """Import a document (an export, a preset file or a theme file): its sections replace the
        scope's, with invalid values left out and reported."""
        where = _scope(scope)
        try:
            raw = parse_document(text)
        except WorkspaceInvalid as exc:
            raise WorkspaceInvalid(f"the imported text {exc.message}") from exc
        clean, findings = sanitise(raw, "import")
        if not clean:
            raise WorkspaceInvalid(
                "the imported text holds no workspace section to import",
                details={"sections": list(SECTIONS), "warnings": _dicts(findings)},
            )
        notes = self._replace_sections(where, project_dir, clean)
        return self._replaced(where, project_dir, list(clean), [*findings, *notes])

    def save_preset(
        self, name: str, title: str | None, description: str | None, project_dir: Path | None
    ) -> dict[str, JSON]:
        """Save the effective workspace as the user preset ``name``."""
        document = self.effective(project_dir).document
        path = catalog.save(
            name, document, self.home, title=title or "", description=description or ""
        )
        self._notify("workspace.changed", {"scope": "presets", "path": name})
        return {
            "name": name,
            "path": str(path),
            "sections": [key for key in document if key not in METADATA],
        }

    # ---------------------------------------------------------------------------------- the project
    # The session's extension hooks (``stokes.api.session.SessionExtension``): the project layer
    # follows the session's project folder through them. ``project`` is typed ``object`` because
    # this package may not import ``stokes.model``; the workspace is not model state and never
    # looks at it.
    def project_opened(self, project: object, path: Path | None) -> None:
        """Session hook: a project replaced the open one, now in ``path`` (None: no folder yet)."""
        self.project_changed(path)

    def project_saved(self, project: object, path: Path, previous: Path | None) -> None:
        """Session hook: the project was written to ``path``, having been in ``previous``.

        ``previous`` is what the session says, not what this store last heard: a store made during
        the save already counts the new folder as current, and still has to carry the overrides of
        the old one."""
        self._project_dir = previous
        self.project_changed(path, saved=True)

    def project_copied(self, project: object, path: Path, previous: Path | None = None) -> None:
        """Session hook: a snapshot exists at ``path``. **This store does nothing, on purpose.**

        It keeps `self._project_dir`, so following the copy would write the next override made
        from the ORIGINAL session into the snapshot (Opus F3). The copy's own overrides are
        already in the copied folder."""
        del project, path, previous

    def project_closed(self) -> None:
        """Session hook: no project is open, so there is no project layer."""
        self.project_changed(None)

    def project_changed(self, project_dir: Path | None, *, saved: bool = False) -> None:
        """Follow the session's project folder.

        After a save to a new folder the project's overrides are carried along (unless the new
        folder has its own), so Save as keeps them. Listeners hear ``workspace.changed`` with
        scope ``project`` when either folder holds overrides.
        """
        previous, self._project_dir = self._project_dir, project_dir
        if previous == project_dir:
            return
        old = project_file(previous) if previous is not None else None
        new = project_file(project_dir) if project_dir is not None else None
        if saved and old is not None and new is not None and project_dir is not None:
            self._carry(old, new, previous, project_dir)
        if (old is not None and old.is_file()) or (new is not None and new.is_file()):
            self._notify("workspace.changed", {"scope": "project", "path": None})

    def _carry(self, old: Path, new: Path, previous: Path | None, project_dir: Path) -> None:
        if not old.is_file() or new.exists() or not contained(new, project_dir):
            return
        state = read_file(old, "project", within=previous)
        if state.readable and state.exists:
            write_file(new, state.document)

    # ---------------------------------------------------------------------------------- helpers
    def _view(
        self, scope: Scope, project_dir: Path | None
    ) -> tuple[Layer, dict[str, JSON], list[Layer]]:
        """The scope's layer, what a user of that scope sees (defaults and the layers up to it,
        with the cross-section rules applied), and the layers below it."""
        layer = self.layer(scope, project_dir)
        lower = [self.layer("user")] if scope == "project" else []
        layers = [(item.scope, item.clean) for item in (*lower, layer)]
        return layer, effective(layers).document, lower

    def _check(
        self, document: dict[str, JSON], root: Loc, layer: Layer, lower: Sequence[Layer]
    ) -> dict[str, JSON]:
        """Refuse a write whose result reading would drop, or that breaks a rule of the whole."""
        clean, findings = sanitise(document, layer.scope)
        known = {(f.code, f.path) for item in (*lower, layer) for f in item.findings}
        # Reading drops invalid values and duplicate entries, and only reports unknown keys:
        # a write may introduce none of the three.
        bad = [
            f
            for f in findings
            if (f.kind != "notice" or f.code == "workspace.duplicate")
            and under(f, root)
            and (f.code, f.path) not in known
        ]
        if not bad:
            layers = [(item.scope, item.clean) for item in lower] + [(layer.scope, clean)]
            late = effective(layers).findings
            bad = [
                f for f in late if f.kind == "invalid" and f.scope == "effective" and under(f, root)
            ]
        if bad:
            first = bad[0]
            where = format_path(first.path) or "the document"
            more = f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""
            raise WorkspaceInvalid(
                f"{where}: {first.message}{more}", details={"warnings": _dicts(bad)}
            )
        return clean

    def _write(
        self, layer: Layer, project_dir: Path | None, document: dict[str, JSON]
    ) -> list[Finding]:
        """Write a scope's document (version first); an unreadable old file is kept aside."""
        path = layer.file.path
        if layer.scope == "project" and (project_dir is None or not contained(path, project_dir)):
            raise WorkspaceIOError(f"{path} resolves outside the project folder; not written")
        notes: list[Finding] = []
        if layer.file.exists and not layer.file.readable and path.exists():
            aside = keep_aside(path)
            notes.append(
                Finding(
                    "workspace.kept_aside",
                    "notice",
                    (),
                    f"the unreadable {path.name} was kept as {aside.name}",
                    layer.scope,
                )
            )
        body = {key: value for key, value in document.items() if key != "version"}
        write_file(path, {"version": SCHEMA_VERSION, **body})
        self._cache.pop(path, None)
        return notes

    def _replace_sections(
        self, scope: Scope, project_dir: Path | None, sections: dict[str, JSON]
    ) -> list[Finding]:
        layer = self.layer(scope, project_dir)
        document = json_copy(layer.file.document) if layer.file.readable else {}
        document.update(json_copy(sections))
        notes = self._write(layer, project_dir, document)
        self._notify("workspace.changed", {"scope": scope, "path": None})
        return notes

    def _replaced(
        self,
        scope: Scope,
        project_dir: Path | None,
        sections: list[str],
        findings: Sequence[Finding],
    ) -> dict[str, JSON]:
        result = self.effective(project_dir)
        overridden: list[str] = []
        if scope == "user" and project_dir is not None:
            overrides = self.layer("project", project_dir).clean
            overridden = [section for section in sections if section in overrides]
        late = [f for f in result.findings if f.scope == "effective"]
        return {
            "scope": scope,
            "sections": sections,
            "overridden": overridden,
            "warnings": _dicts([*findings, *late]),
        }


# ------------------------------------------------------------------------------ module helpers
def _scope(scope: str) -> Scope:
    if scope not in SCOPES:
        raise WorkspaceInvalid(
            f"scope must be user or project; got {scope!r}", details={"scope": scope}
        )
    return scope


def _api_value(value: JSON) -> JSON:
    """A value from a caller as plain JSON, within the document limits."""
    if not depth_ok(value):
        raise WorkspaceInvalid(f"the value nests deeper than {MAX_DEPTH} levels")
    try:
        copied = json_copy(value)
    except ValueError as exc:
        raise WorkspaceInvalid(f"the value is not JSON: {exc}") from exc
    if len(json.dumps(copied, ensure_ascii=False)) > MAX_DOCUMENT_BYTES:
        raise WorkspaceInvalid(f"the value is larger than {MAX_DOCUMENT_BYTES} bytes")
    return copied


def _section_names(sections: Sequence[str], document: dict[str, JSON]) -> list[str]:
    names: list[str] = []
    for section in sections:
        if not isinstance(section, str) or (section not in SECTIONS and section not in document):
            raise WorkspaceInvalid(
                f"no section {section!r}; sections are {', '.join(SECTIONS)}",
                details={"sections": list(SECTIONS)},
            )
        if section not in METADATA and section not in names:
            names.append(section)
    return names


def _has(document: JSON, loc: Loc) -> bool:
    node = document
    for segment in loc:
        in_object = isinstance(node, dict) and isinstance(segment, str) and segment in node
        in_list = isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node)
        if not (in_object or in_list):
            return False
        node = node[segment]
    return True


def _maybe(document: JSON, loc: Loc) -> JSON:
    return value_at(document, loc) if _has(document, loc) else None


def _put(document: dict[str, JSON], loc: Loc, value: JSON) -> None:
    """Replace the value at a path of keys that exists in ``document``."""
    node: JSON = document
    for segment in loc[:-1]:
        node = node[segment]
    node[loc[-1]] = value


def _file_info(state: FileState) -> dict[str, JSON]:
    return {"path": str(state.path), "exists": state.exists, "readable": state.readable}


def _dicts(findings: Iterable[Finding]) -> list[JSON]:
    return [finding.to_dict() for finding in findings]
