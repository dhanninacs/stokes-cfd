"""Helpers for the operation tests (ADR-0021 decision 8; T-024 to T-028).

``run_op`` runs an operation module in this process on fixture shapes, the way the worker runs
it, so a module's geometry is tested without a session or a worker. ``create_and_wait`` creates
an operation through a session and returns ``operation.info`` once its run has ended; ``box_part``
and ``cylinder_part`` make input parts with the ``primitive`` operation. Imported only by tests
that skipped themselves when OCP is missing. Shapes are in millimetres, the unit operations work
in; parameters are SI.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from stokes.api import Session
from stokes.geometry.operations import load
from stokes.geometry.operations.base import OpContext, OpInput, OpResult


def defaults(op_type: str) -> dict[str, Any]:  # Any: JSON
    """The SI defaults of an operation type's own parameters, as a run receives them."""
    from stokes.geometry.model import OPERATION_COMMON
    from stokes.model import registry

    found: dict[str, Any] = {}
    for spec in registry.get(op_type).params:
        if spec.name in OPERATION_COMMON:
            continue
        value = spec.default
        if spec.kind == "array":
            value = [float(v) for v in value or ()]
        elif spec.kind == "quantity":
            value = float(value or 0.0)
        found[spec.name] = value
    return found


def part_id(index: int) -> str:
    """The made-up node id ``run_op`` gives input ``index``."""
    return f"n_{index:08x}"


def run_op(
    op_type: str,
    shapes: Sequence[Any | tuple[str, Any]],  # Any: OCP TopoDS_Shape in mm
    params: dict[str, Any] | None = None,  # Any: SI JSON values
    *,
    progress: list[tuple[str, float, str]] | None = None,
) -> OpResult:
    """Run the module of ``op_type`` on ``shapes`` (shapes, or (name, shape) pairs; input ``i``
    is part ``part_id(i)``), with its defaults under ``params``."""
    inputs = []
    for index, item in enumerate(shapes):
        name, shape = item if isinstance(item, tuple) else (f"body_{index}", item)
        inputs.append(OpInput(part_id(index), name, shape))
    merged = {**defaults(op_type), **(params or {})}

    def report(stage: str, fraction: float, message: str) -> None:
        if progress is not None:
            progress.append((stage, fraction, message))

    return load(op_type)(OpContext(merged, inputs, report))


def create_and_wait(
    session: Session,
    op_type: str,
    inputs: Sequence[str] = (),
    params: dict[str, Any] | None = None,  # Any: JSON values
    **extra: Any,  # Any: further operation.create parameters (name, auto_run, node_id)
) -> dict[str, Any]:  # Any: JSON
    """``operation.create`` with ``wait``: the operation's info once its run has ended."""
    info: dict[str, Any] = session.execute(
        "operation.create",
        type=op_type,
        inputs=list(inputs),
        params=dict(params or {}),
        wait=True,
        **extra,
    )
    return info


def box_part(
    session: Session,
    size: tuple[float, float, float] = (0.02, 0.02, 0.02),
    at: tuple[float, float, float] = (0.0, 0.0, 0.0),
    **extra: Any,  # Any: further operation.create parameters
) -> str:
    """A box part (SI lengths) made by a ``primitive`` operation; its part id."""
    info = create_and_wait(
        session,
        "primitive",
        params={"shape": "box", "size": list(size), "position": list(at)},
        **extra,
    )
    assert info["status"] == "ok", info["error"]
    outputs: list[str] = info["outputs"]
    return outputs[0]


def cylinder_part(
    session: Session,
    radius: float = 0.004,
    length: float = 0.03,
    at: tuple[float, float, float] = (0.01, 0.01, -0.005),
    **extra: Any,  # Any: further operation.create parameters
) -> str:
    """A cylinder part along z (SI lengths) made by a ``primitive`` operation; its part id."""
    params = {"shape": "cylinder", "size": [radius, length, 0.0], "position": list(at)}
    info = create_and_wait(session, "primitive", params=params, **extra)
    assert info["status"] == "ok", info["error"]
    outputs: list[str] = info["outputs"]
    return outputs[0]
