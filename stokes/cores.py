"""MPI rank counts shared by the solver, the mesher and the case writers.

The value is execution policy, not physics, so it lives above both `stokes.compile`
and `stokes.run`: the compiler writes the decomposition dictionary, the runner
records the snapshot, and the API validates one edit in the same place. Keeping it
out of `stokes.run` also keeps `stokes.compile` inside its layering rule and
`import stokes.api` from loading the agent.
"""

from __future__ import annotations

from stokes.errors import ValidationError

#: The largest rank count a settings field accepts. MPI itself allows more, but a
#: desktop mesh or solve above this is a mistake the user wants named, not run.
MAX_CORES = 256


def core_count(value: object) -> int:
    """A positive integer MPI rank count; never truncate a fractional request."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CORES:
        raise ValidationError(
            f"cores must be an integer from 1 to {MAX_CORES}; got {value!r}",
            details={"code": "run.invalid_cores"},
        )
    return value


def validate_core_count(_node: object, value: object) -> int:
    """Model validator, also protecting direct parameter writes and journal replay."""
    return core_count(value)
