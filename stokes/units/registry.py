"""The pint unit registry, created on first use.

pint takes well over 100 ms to build a registry, so nothing at import time
touches it. Every module that needs units calls :func:`ureg`.
"""

from __future__ import annotations

from functools import cache
from typing import Any

Quantity = Any  # Any: pint ships no type stubs; a pint.Quantity at runtime


@cache
def ureg() -> Any:  # Any: pint.UnitRegistry
    """Return the process-wide registry with SI base units and offset handling."""
    import pint

    registry = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
    registry.formatter.default_format = "~C"  # compact symbols: "m/s", "kg/m**3"
    return registry


def quantity_type() -> type:
    """The runtime Quantity class, for isinstance checks."""
    import pint

    return pint.Quantity
