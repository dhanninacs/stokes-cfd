"""Geometry operations (ADR-0021 decision 8): one module per operation type, each with
``run(ctx) -> OpResult`` and nothing else public.

The framework around them: :mod:`.base` holds the contract (``OpContext``, ``OpInput``,
``OpOutput``, ``OpResult``), :mod:`.faces` the face numbering and the history map, and this
module the light helpers both processes share: which types are built, the ids and versions an
output gets, and the seed a run's versions derive from. The worker job that runs a module is
``stokes.geometry.worker_main._operation_job``; the session side is
:mod:`stokes.geometry.opstore`; the node types are in :mod:`stokes.geometry.model`.

Units: operations work in millimetres. The shape provider scales every input to mm, parameters
arrive in SI and ``OpContext.mm`` converts them, and outputs are written with ``units: "mm"``.

Import rule: the standard library and ``stokes.model.ids`` here, so the API process can ask what
is built without loading OCP; the operation modules import OCP inside their functions.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import OperationInvalid
from stokes.model.ids import stable_id

if TYPE_CHECKING:
    from stokes.geometry.operations.base import OpContext, OpResult

PACKAGE = __name__
BASIS_LENGTH = 12
"""Hex characters of a version (basis), as an imported part's (``fingerprints.basis_of``)."""


def _known(op_type: str) -> bool:
    # Imported here: a process whose first import is this package (the worker) must load
    # stokes.model before stokes.geometry.model, which stokes.model's schema imports in turn.
    from stokes.geometry.model import OPERATION_TYPE_NAMES

    return op_type in OPERATION_TYPE_NAMES


def is_built(op_type: str) -> bool:
    """Whether the module of ``op_type`` exists (found without importing it)."""
    if not _known(op_type):
        return False
    return importlib.util.find_spec(f"{PACKAGE}.{op_type}") is not None


def load(op_type: str) -> Callable[[OpContext], OpResult]:
    """The ``run`` function of an operation type; :class:`OperationInvalid` when the type is
    unknown or its module is not built yet (T-025 to T-028 add them)."""
    if not _known(op_type):
        raise OperationInvalid(f"unknown operation type {op_type!r}", details={"type": op_type})
    if not is_built(op_type):
        raise OperationInvalid(
            f"the {op_type} operation is not built yet", details={"type": op_type}
        )
    module = importlib.import_module(f"{PACKAGE}.{op_type}")
    run: Callable[[OpContext], OpResult] = module.run
    return run


def output_id(op_id: str, index: int) -> str:
    """The node id of output ``index`` of the operation ``op_id``: derived from the op's, so a
    replayed journal rebuilds the same ids (ADR-0021 decision 8.2)."""
    return stable_id("n", f"{op_id}/output/{index}")


def tag_id(op_id: str, name: str) -> str:
    """The node id of a tag the operation ``op_id`` creates on its outputs (``OpOutput.tags``)."""
    return stable_id("n", f"{op_id}/tag/{name}")


def seed_of(payload: Any) -> str:  # Any: JSON
    """The hash a run's output versions derive from: the canonical JSON of what the run reads
    (type, SI parameters, the inputs and their versions, the members of referenced tags)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(text.encode("ascii"), usedforsecurity=False).hexdigest()


def output_basis(seed: str, version: str, index: int) -> str:
    """The version (basis) of output ``index`` of a run: its seed, the OCP version the worker
    ran, and the index (ADR-0021 decision 8.4). A changed parameter or input, or another OCCT,
    gives a new basis, and the tag members picked on the old one go stale."""
    text = f"{seed}\0{version}\0{index}"
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:BASIS_LENGTH]
