"""The compiler's error codes (ADR-0008, T-212).

Two kinds, and the distinction is the one a user needs: **unsupported** is "Stokes
cannot express this
yet" (a turbulence model, a cyclic patch, a different engine) and **invalid** is "this
cannot be a case at
all" (a negative viscosity, a velocity inlet without a velocity, a patch name that is not a
word). The
first is a gap in the product; the second is a mistake in the request.
"""

from __future__ import annotations

from stokes.errors import StokesError


class CompileError(StokesError):
    """Base for every case-writing failure."""

    code = "compile.error"


class CompileUnsupported(CompileError):
    """This writer cannot express what was asked for: an engine, a turbulence model, a
    condition kind."""

    code = "compile.unsupported"


class CompileInvalid(CompileError):
    """A value that cannot be written into a case: out of range, missing, or not an OpenFOAM
    word."""

    code = "compile.invalid"
