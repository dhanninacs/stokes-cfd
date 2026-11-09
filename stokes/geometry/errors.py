"""Errors of CAD import and the geometry store (ADR-0014).

Standard library only, so the API layer can raise and catch them without loading numpy or OCP.
"""

from __future__ import annotations

from stokes.errors import StokesError


class GeometryError(StokesError):
    """Base class for geometry import and mesh errors."""

    code = "geometry.error"


class CadUnavailable(GeometryError):
    """The optional ``cad`` extra (cadquery-ocp) is not installed."""

    code = "geometry.cad_missing"


class GeometryFileError(GeometryError):
    """A CAD file is missing, unreadable, too large or not a STEP file OCCT can translate."""

    code = "geometry.unreadable"


class GeometryWorkerError(GeometryError):
    """The worker process failed, stopped unexpectedly or timed out."""

    code = "geometry.worker_failed"


class ImportCancelled(GeometryError):
    """The import was cancelled before it finished."""

    code = "geometry.cancelled"


class MeshFormatError(GeometryError):
    """A mesh transport buffer or cache file is malformed."""

    code = "geometry.bad_mesh"


class MeshUnavailable(GeometryError):
    """A part has no display mesh yet (its import is running, failed or was never cached)."""

    code = "geometry.no_mesh"


class GeometryUnsupported(GeometryError):
    """The operation needs data this release does not keep (a part's B-rep, face colours)."""

    code = "geometry.unsupported"


class SelectionError(GeometryError):
    """Tag members that cannot be used: unknown faces, malformed lists (ADR-0020)."""

    code = "tag.invalid"


class SelectionStale(SelectionError):
    """A tag's face ids refer to an earlier version of a part; ``tag.rematch`` first."""

    code = "tag.stale"


class OperationFailed(GeometryError):
    """An operation ran and OCCT could not make its result (ADR-0021 decision 8.7)."""

    code = "geometry.op_failed"


class OperationInvalid(GeometryError):
    """An operation cannot run as asked: its inputs, its parameters, a module not built yet, or
    a derived input whose B-rep is gone until its own operation runs again."""

    code = "geometry.op_invalid"


class MeshOnly(GeometryError):
    """A mesh-only part (STL, OBJ, 3MF) where a B-rep is needed (ADR-0021 decision 1.3)."""

    code = "geometry.mesh_only"


class SeedOutside(OperationFailed):
    """No solid of a fluid extraction contains the seed point (ADR-0021 decision 6.1)."""

    code = "geometry.seed_outside"
