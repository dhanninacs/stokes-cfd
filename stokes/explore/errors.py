"""Typed errors of the exploration numerics.

They derive from ``stokes.errors.StokesError`` so that the API, the CLI and the UI report them
like every other error (CLAUDE.md section 3). Bad input is a ``ValidationError``; a runtime
failure of an algorithm is not.
"""

from __future__ import annotations

from stokes.errors import StokesError, ValidationError


class ExploreError(StokesError):
    """Base class of runtime failures in ``stokes.explore``."""

    code = "explore.error"


class InvalidInput(ValidationError):
    """An array has the wrong shape, contains NaN where it must not, or a value is out of range."""

    code = "explore.invalid_input"


class DomainError(ValidationError):
    """A variable domain is malformed (bounds, counts, duplicate levels)."""

    code = "explore.domain"


class SamplerError(ValidationError):
    """A sampler cannot produce the requested design (too few variables, a discrete variable
    where a continuous one is needed, too many points)."""

    code = "explore.sampler"


class SurrogateError(ExploreError):
    """A surrogate cannot be fitted or evaluated (too few points, singular system, not fitted)."""

    code = "explore.surrogate"


class SensitivityError(ExploreError):
    """A sensitivity analysis cannot be carried out on the data it was given."""

    code = "explore.sensitivity"


class ParetoError(ValidationError):
    """Objective data for the Pareto utilities is malformed."""

    code = "explore.pareto"


class OptimiseError(ExploreError):
    """An optimisation driver cannot start or cannot return a result (every evaluation failed)."""

    code = "explore.optimise"


class Cancelled(ExploreError):
    """Raised by an evaluator to stop a run cleanly; the driver returns what it has so far."""

    code = "explore.cancelled"


class MissingDependency(ExploreError):
    """An optional package of the ``explore`` extra is not installed."""

    code = "explore.missing_dependency"
