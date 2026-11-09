"""Typed errors of the materials subsystem (ADR-0018; the codes are in the contract,
docs/engineering/materials-schema.md).

They derive from ``stokes.errors.StokesError`` so that the API, the CLI and the UI report them like
every other error. Bad input (a method foreign to the property, a missing molar mass) is a
``ValidationError``; a provider that is absent or cannot answer is not.
"""

from __future__ import annotations

from stokes.errors import StokesError, ValidationError


class MaterialsError(StokesError):
    """Base class of runtime failures in ``stokes.physics.materials``."""

    code = "materials.error"


class ProviderMissing(MaterialsError):
    """The provider is not available in this installation."""

    code = "materials.provider_missing"


class RefpropMissing(MaterialsError):
    """REFPROP was not found or failed to load (library providers, ADR-0018 slice 2)."""

    code = "materials.refprop_missing"


class FluidUnknown(MaterialsError):
    """The provider has no such fluid, composition or basis."""

    code = "materials.fluid_unknown"


class PropertyUnavailable(MaterialsError):
    """The provider has no model for that property of that fluid."""

    code = "materials.property_unavailable"


class OutOfRange(MaterialsError):
    """A temperature or pressure lies outside what the provider or the material allows."""

    code = "materials.range"


class InvalidMethod(ValidationError):
    """The method does not apply to the property kind or the material kind, or is not available
    in this release."""

    code = "materials.invalid_method"


class NeedsMolarMass(ValidationError):
    """The method needs the material's molar mass, which is 0 (unknown)."""

    code = "materials.needs_molar_mass"


class KindMismatch(ValidationError):
    """A fluid material on a solid region or the reverse: ``region.create``, ``region.assign``
    and ``region.set`` refuse it (ADR-0022 decision 4.2)."""

    code = "materials.kind_mismatch"


class EvaluationFailed(MaterialsError):
    """A method could not be evaluated: its parameters hold an expression that failed, or the
    formula has no physical solution at a state (a cubic equation of state without a root)."""

    code = "materials.evaluation"


class FitTolerance(MaterialsError):
    """No candidate form met the tolerance (library fits, ADR-0018 slice 2)."""

    code = "materials.fit_tolerance"


class Busy(MaterialsError):
    """A background fit is running for that material (ADR-0018 slice 2)."""

    code = "materials.busy"


class StaleFit(MaterialsError):
    """A fit was made for another state of the material (it changed while a background fit
    ran), or a property waits for a refit."""

    code = "materials.stale_fit"


class Cancelled(MaterialsError):
    """A fit or a provider request was cancelled (``material.cancel_fit``, another project
    opened, or Ctrl-C in the CLI) before it finished; the properties it would have written stay
    as they were, stale."""

    code = "materials.cancelled"
