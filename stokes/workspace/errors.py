"""Workspace errors (ADR-0015). Each carries a stable code; every client sees the same object."""

from __future__ import annotations

from stokes.errors import StokesError, ValidationError


class WorkspaceInvalid(ValidationError):
    """A value, a document or a parameter does not satisfy the workspace schema."""

    code = "workspace.invalid"


class WorkspacePathError(ValidationError):
    """A path is malformed or addresses nothing the schema or the document knows."""

    code = "workspace.path"


class UnknownPreset(StokesError):
    code = "workspace.unknown_preset"


class ProjectFolderRequired(StokesError):
    """Project scope needs an open project that has been saved to a ``.stokes`` folder."""

    code = "workspace.no_project_folder"


class WorkspaceIOError(StokesError):
    """A workspace or preset file could not be written."""

    code = "workspace.io"
