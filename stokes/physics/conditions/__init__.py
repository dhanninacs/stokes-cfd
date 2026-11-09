"""Boundary conditions (ADR-0022 decision 2): the condition catalogue and its errors.

Modules:

- ``catalogue``: the declarative table of condition kinds, ``KINDS``: for each kind its family,
  the ``boundary`` parameters it uses and when, the rules their values follow, the solver fields
  it fixes, the physics its parameters need, its tag colour and its viewport glyph.
  ``boundary.kinds``, the set-up check, the value panel and, in M3, the compiler read it.
- ``checks``: the set-up check, ``check_setup``: the readiness states and the sixteen findings of
  ``setup.check`` (T-037), one function each and pure over the project and its staleness.

The ``boundary`` and ``region`` node types are declared beside ``tag`` in
``stokes.geometry.model`` (ADR-0022 decision 2.1), with their vocabulary: the kind ids and the
choices of the boundary's enums, which the catalogue imports.

Contract for clients: ``docs/engineering/setup-schema.md``.

Import rule: headless. This module imports only ``stokes.errors``; the catalogue imports the
standard library, these errors and ``stokes.geometry.model`` (standard library only). ``checks``
adds ``stokes.model.QuantityValue`` at module level and reaches the set-up readers of
``stokes.api.commands`` inside the functions that need them, because importing those at the top
would re-enter that package. Nothing here imports Qt or OCP; the command modules load the
catalogue and the check.
"""

from __future__ import annotations

from stokes.errors import ValidationError


class ConditionError(ValidationError):
    """A condition kind or a boundary value that the catalogue refuses.

    A ``ValidationError``, as every refusal of bad input is (the materials' ``InvalidMethod``
    too), so clients that branch on the base class see it; the subclasses carry the codes."""

    code = "boundary.error"


class InvalidKind(ConditionError):
    """No condition kind has that id (``boundary.assign`` and ``boundary.set`` also refuse a kind
    that is not available yet, T-036)."""

    code = "boundary.invalid_kind"


class InvalidValue(ConditionError):
    """A value for a parameter the kind does not use, or one that breaks its rule; the message
    names the parameter and the rule."""

    code = "boundary.invalid_value"
