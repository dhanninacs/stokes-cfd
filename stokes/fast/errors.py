"""The errors of Fast mode (ADR-0047). Codes are stable: clients branch on them."""

from __future__ import annotations

from stokes.errors import StokesError


class FastError(StokesError):
    code = "fast.error"


class FastUnavailable(FastError):
    """No ``wgpu`` package, or no hardware GPU adapter: Fast mode is not offered (decision 1)."""

    code = "fast.unavailable"


class FastInvalid(FastError):
    """A setting or a request that cannot be honoured: a fidelity out of range, an unknown field,
    a plane that misses the fluid."""

    code = "fast.invalid"


class FastNotImplicit(FastError):
    """The case's fluid is not a part made with Create; slice 1 needs its field (decision 3)."""

    code = "fast.not_implicit"


class FastUnsupportedBoundary(FastError):
    """A boundary kind Fast mode does not model yet (decision 4)."""

    code = "fast.unsupported_boundary"


class FastPortNotPlanar(FastError):
    """An inlet or an outlet whose faces are not one plane (decision 4)."""

    code = "fast.port_not_planar"


class FastTooFast(FastError):
    """The flow is too fast for the lattice at this fidelity: tau would fall below its limit
    (decision 5)."""

    code = "fast.too_fast_for_fidelity"


class FastNotRunning(FastError):
    """A request that needs a Fast run when there is none."""

    code = "fast.not_running"
