"""Property providers (ADR-0018 decision 6): the built-in catalogue, CoolProp (HEOS and INCOMP),
Cantera and REFPROP through CoolProp, all behind the :class:`~.base.Provider` protocol.

:func:`get_provider` imports each lazily, so none of them loads with the API. The library
providers are :class:`~.remote.RemoteProvider` proxies of the materials worker process, which
alone imports CoolProp and Cantera (``coolprop.py``, ``cantera.py`` and ``refprop.py`` run
there); whether they are installed is read from package metadata, never by importing them.

Import rule: the standard library and this package's ``base`` and ``errors``.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from stokes.errors import ValidationError

if TYPE_CHECKING:
    from stokes.physics.materials.providers.base import Provider

PROVIDER_NAMES: tuple[str, ...] = ("builtin", "coolprop", "coolprop_incomp", "cantera", "refprop")
"""Every provider of v1, in the order searches list them."""
LIBRARY: tuple[str, ...] = ("coolprop", "coolprop_incomp", "cantera", "refprop")


@cache
def _builtin() -> Provider:
    from stokes.physics.materials.providers.builtin import BuiltinProvider

    return BuiltinProvider()


def get_provider(name: str) -> Provider:
    """The provider called ``name``: ``materials.provider_missing`` when its package is not
    installed, ``materials.refprop_missing`` when REFPROP cannot be found or loaded."""
    if name == "builtin":
        return _builtin()
    if name in LIBRARY:
        from stokes.physics.materials.providers import remote

        remote.require_installed(name)
        provider = remote.RemoteProvider(name)
        if name == "refprop":
            found = provider.available()
            if not found.available:
                from stokes.physics.materials.errors import RefpropMissing

                raise RefpropMissing(found.reason or "REFPROP is not available")
        return provider
    raise ValidationError(
        f"unknown provider {name!r}; known: {', '.join(PROVIDER_NAMES)}",
        details={"provider": name},
    )


def available_providers() -> list[str]:
    """Names of the providers that can answer here (REFPROP once it has been detected)."""
    from stokes.physics.materials.providers import remote

    names = ["builtin"]
    for name in LIBRARY:
        if not remote.installed(name):
            continue
        if name == "refprop":
            detection = remote.refprop_detection()
            if not detection or not detection.get("available"):
                continue
        names.append(name)
    return names
