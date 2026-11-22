"""The errors of implicit geometry (ADR-0035). Codes are stable: clients branch on them."""

from __future__ import annotations

from stokes.errors import StokesError


class ImplicitError(StokesError):
    code = "implicit.error"


class ImplicitInvalid(ImplicitError):
    """A graph or a feature that cannot be built: a non-finite or out-of-range constant, an unknown
    operation, a dimension that is not positive."""

    code = "implicit.invalid"


class ImplicitTooLarge(ImplicitError):
    """The graph has more nodes than the limit of decision 4."""

    code = "implicit.too_large"


class ImplicitUnbounded(ImplicitError):
    """The body has no finite bounding box, so there is no cube to mesh it in (decision 21)."""

    code = "implicit.unbounded"


class ImplicitTooFine(ImplicitError):
    """The resolution asked for needs a deeper octree than the mesher allows (decision 21)."""

    code = "implicit.too_fine"


class MesherMissing(ImplicitError):
    """The ``stokes-implicit-mesh`` program is not installed or built (decision 13)."""

    code = "implicit.mesher_missing"


class MesherFailed(ImplicitError):
    """The mesher ran but failed, timed out, or wrote output that does not pass the checks of
    decision 17."""

    code = "implicit.mesher_failed"


class MesherCancelled(ImplicitError):
    """The run was cancelled (a newer request superseded it, or the user stopped it)."""

    code = "implicit.cancelled"
