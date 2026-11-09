"""Mesher drivers: one module per mesher, each turning a snapshot into case files (ADR-0024).

A driver is **pure**: a snapshot in, a `CaseTree` and a step list out. It runs nothing, knows no
host and touches no disk, which is what lets T-207 call one from a command and hand the result to a
job.

**One driver per mesher, selected by the setting** (the owner's 1045 decision). This module is the
one place mapping a `mesh_settings.mesher` value to the module that serves it, so the service never
imports a driver by name. Importing `blockmesh` directly and unconditionally is what made
`mesher: snappy` a value the UI honoured, the result node recorded, and the backend ignored - the
demo kept meshing a bounding box while the tree said snappy.

Every driver module exposes the same public surface:

``write(snapshot) -> CaseTree``
    the case files, as ``{relative path: text}``.
``steps(snapshot) -> list[dict]``
    the utilities to run, in order.
``cell_count(snapshot) -> int``
    the cell count the background block alone will produce, for the result node's summary.
    **One int, not the per-axis trio** - blockMesh's own ``cells`` returns the trio for its
    ``hex`` line, and a driver whose ``cell_count`` returned a tuple broke 23 tests.
``patch_types(snapshot) -> dict[str, str]``
    patch name -> its boundary type, for the quality card.
"""

from __future__ import annotations

from functools import lru_cache
from types import ModuleType

from stokes.mesh.errors import MeshNotBuilt

#: Mesher id -> the module that serves it. **A mesher with no entry cannot be meshed**, which is a
#: different fact from the catalogue's `built`: the catalogue says whether a driver exists, this
#: says where it lives. Kept as module NAMES rather than imports so `import stokes.mesh.drivers`
#: does not pull every driver - and its dictionaries - into the `import stokes.api` budget.
DRIVER_MODULES: dict[str, str] = {
    "blockmesh": "stokes.mesh.drivers.blockmesh",
    "snappy": "stokes.mesh.drivers.snappy",
    # **One module, four volume fills** (ADR-0032): cartesian, tet, poly and poly-hexcore all read
    # the same joined surface and differ only in the utilities they run.
    "cfmesh": "stokes.mesh.drivers.cfmesh",
    "cfmesh_tet": "stokes.mesh.drivers.cfmesh",
    "cfmesh_poly": "stokes.mesh.drivers.cfmesh",
    "polyhex": "stokes.mesh.drivers.cfmesh",
}


def driver_for(mesher: str) -> ModuleType:
    """The driver module for ``mesher``, imported on demand.

    Refuses an unknown or unimplemented mesher **by name, listing what can be driven**: a user who
    picked cfMesh from a catalogue entry that says "not built yet" must not meet an AttributeError
    from deep inside a job.
    """
    name = DRIVER_MODULES.get(mesher)
    if name is None:
        known = ", ".join(sorted(DRIVER_MODULES))
        # **`MeshNotBuilt`, not `MeshInvalid` with a code in `details`.** The class carries the
        # code (`mesh.not_built`), and putting it in `details` leaves the error reporting the
        # class's `mesh.invalid` instead - which the front end would branch on wrongly. Measured:
        # the same mistake in a test assertion showed `mesh.invalid` where `mesh.not_built` was
        # intended.
        raise MeshNotBuilt(
            f"no driver for mesher {mesher!r}; this build can mesh with: {known}",
            details={"mesher": mesher},
        )
    return _import(name)


@lru_cache(maxsize=8)
def _import(name: str) -> ModuleType:
    """Import a driver module once per process.

    Cached because the service asks for the same driver on every generate and a module import is
    not free; it also makes the identity stable, so a test can assert which module was chosen.
    """
    from importlib import import_module

    return import_module(name)


def meshers_with_drivers() -> tuple[str, ...]:
    """Every mesher that can actually be run, for a message or a test."""
    return tuple(sorted(DRIVER_MODULES))
