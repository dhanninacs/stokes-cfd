"""The ``boolean`` operation: fuse, cut or common of bodies (ADR-0021 decision 6.3, as ADR-0020
decision 5 describes it).

The arguments are the inputs that are not tools; the tools are the parts ``tools`` names (the
framework loads them as inputs too). A fuse or a common without tools takes the first input as
the argument and the rest as tools; a cut needs tools. ``fuzzy`` is OCCT's fuzzy value in SI
metres; ``simplify`` merges the faces of a fuse or a cut that lie on one surface
(``ShapeUpgrade_UnifySameDomain``), and its history is merged into the boolean's, so tags follow
both steps. The one output is named ``<first argument>_<op>``.

OCP traps kept: ``BRepAlgoAPI_*`` have ``IsDone`` and no ``HasErrors``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from stokes.geometry.errors import OperationFailed, OperationInvalid
from stokes.geometry.operations.base import OpContext, OpInput, OpOutput, OpResult

OPS = ("fuse", "cut", "common")
SIMPLIFIED = ("fuse", "cut")
"""The ops whose result ``simplify`` unifies (ADR-0021 decision 6.3)."""


def run(ctx: OpContext) -> OpResult:
    """Fuse, cut or intersect the inputs with the tools."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    op = str(ctx.params.get("op", "fuse"))
    if op not in OPS:
        raise OperationInvalid(f"op must be one of {', '.join(OPS)}; got {op!r}")
    named = [str(p) for p in ctx.params.get("tools") or []]
    tools = [ctx.input(part) for part in named]
    arguments = [item for item in ctx.inputs if item.part not in named]
    if not arguments:
        raise OperationInvalid("a boolean needs at least one input that is not a tool")
    if not tools:
        if op == "cut":
            raise OperationInvalid("a cut needs tools: the parts that cut the inputs")
        if len(arguments) < 2:
            raise OperationInvalid(f"a {op} needs two bodies, or an input and tools")
        arguments, tools = arguments[:1], arguments[1:]
    fuzzy = ctx.mm(float(ctx.params.get("fuzzy", 0.0) or 0.0))
    if fuzzy < 0.0:
        raise OperationInvalid("the fuzzy tolerance cannot be negative")
    before = [ctx.check(item.shape) for item in (*arguments, *tools)]
    algo: Any = {"fuse": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "common": BRepAlgoAPI_Common}[
        op
    ]()  # Any: an OCP boolean
    algo.SetArguments(_shapes(arguments))
    algo.SetTools(_shapes(tools))
    if fuzzy > 0.0:
        algo.SetFuzzyValue(fuzzy)
    algo.SetToFillHistory(True)
    ctx.progress("boolean", 0.1, f"{op} of {len(arguments)} and {len(tools)} bodies")
    algo.Build()
    if not algo.IsDone():
        raise OperationFailed(
            f"OCCT could not {op} {arguments[0].name} with {', '.join(t.name for t in tools)}",
            details={"op": op},
        )
    shape = algo.Shape()
    history = algo.History()
    simplify = bool(ctx.params.get("simplify", True))
    if simplify and op in SIMPLIFIED:
        shape, history = _unify(ctx, shape, history)
    after = ctx.check(shape)
    if not after["faces"]:
        raise OperationFailed(f"the {op} left nothing", details={"op": op})
    warnings = [] if after["valid"] else [f"the result of the {op} is not a valid B-rep"]
    report = {
        "op": op,
        "arguments": [item.part for item in arguments],
        "tools": [item.part for item in tools],
        "simplified": simplify and op in SIMPLIFIED,
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput(f"{arguments[0].name}_{op}", shape, history, report={"check_after": after})
    ctx.progress("boolean", 1.0, f"{op} done")
    return OpResult([output], report=report, warnings=warnings)


def _shapes(items: Sequence[OpInput]) -> Any:  # Any: OCP List_TopoDS_Shape
    import OCP.collections as occ

    shapes = occ.List_TopoDS_Shape()
    for item in items:
        shapes.Append(item.shape)
    return shapes


def _unify(ctx: OpContext, shape: Any, history: Any) -> tuple[Any, Any]:  # Any: OCP objects
    """Merge the faces on one surface; the boolean's history followed by the unification's."""
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    ctx.progress("simplify", 0.7, "merging faces that lie on one surface")
    unify = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
    unify.Build()
    history.Merge(unify.History())
    return unify.Shape(), history
