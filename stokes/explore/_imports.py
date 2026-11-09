"""Lazy access to scipy and to the optional packages of the ``explore`` extra.

Importing at the point of use keeps ``import stokes.explore`` cheap and turns a missing optional
package into a typed error with the install hint. The returned modules are typed ``Any`` on
purpose: scipy ships no type information that mypy accepts, and pymoo, SALib and scikit-learn ship
none at all, so everything reached through them is checked at run time and by the tests.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from stokes.explore.errors import MissingDependency

_PIP_NAMES = {"pymoo": "pymoo", "SALib": "SALib", "sklearn": "scikit-learn"}


def scipy_module(module: str) -> Any:  # Any: scipy is untyped for mypy
    """Import a scipy submodule, for example ``scipy_module("scipy.stats.qmc")``."""
    return import_module(module)


def require(module: str, *, feature: str) -> Any:  # Any: third-party module without stubs
    """Import ``module`` (a dotted path) or raise ``MissingDependency`` naming the extra."""
    try:
        return import_module(module)
    except ImportError as exc:
        top = module.split(".", maxsplit=1)[0]
        package = _PIP_NAMES.get(top, top)
        raise MissingDependency(
            f"{feature} needs the optional package {package!r}; "
            "install it with: pip install 'stokes-cfd[explore]'",
            details={"package": package, "extra": "explore", "feature": feature},
        ) from exc


def try_require(module: str, *, feature: str) -> Any | None:  # Any: third-party module
    """Like ``require`` but returns ``None`` when the package is absent (for fallbacks)."""
    try:
        return require(module, feature=feature)
    except MissingDependency:
        return None
