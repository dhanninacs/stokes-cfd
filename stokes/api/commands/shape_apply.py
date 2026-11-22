"""``shape.apply``: put a shape optimisation's best point into the live project (ADR-0040).

Setting the variables alone is not enough for a part made with Create: its face ids are those of
its last run, so after the operations run again every boundary tag on it names faces of an older
version and meshing refuses it ("picked on an older version of the part"). Applying therefore

1. takes the rules that find the part's boundary tags again from the part as it is now
   (:func:`stokes.geometry.retag.rules_for`: a planar inlet or outlet by its plane and the
   nearest tagged centre, the walls as the rest), before anything changes;
2. sets the values with ``parameter.assign`` (one undo step);
3. runs the implicit operations that make the part again, in tree order, so the surface is the one
   the values describe (a project just opened does not re-run them on its own, ADR-0040);
4. finds the tags' faces on the rebuilt part and gives them back with ``tag.rematch``.

Steps 3 and 4 wait for the geometry worker, which the server's event loop must not do: with
``wait`` false they run on a thread and the end arrives as ``shape.applied``; the tags are written
where commands run (``GeometryStore.on_session_thread``).

Headless: the API and the model; the geometry modules lazily.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, ValidationError
from stokes.geometry.errors import MeshOnly, OperationFailed, OperationInvalid
from stokes.log import fields, get_logger

if TYPE_CHECKING:
    from stokes.api.session import Session

log = get_logger(__name__)

EVENT = "shape.applied"


def feeding_operations(project: Any, part: Any) -> list[str]:  # Any: Project, Node
    """The ids of the implicit operations that make ``part``, through their inputs and tools, in
    tree order (an operation's inputs come before it there)."""
    from stokes.geometry.model import OPERATIONS_PATH, is_implicit_operation

    wanted: set[str] = set()
    pending = [part]
    while pending:
        node = pending.pop()
        op = project.find(str(node.params.get("derived") or ""))
        if op is None or op.id in wanted or not is_implicit_operation(op.type):
            continue
        wanted.add(op.id)
        for ident in [*(op.params.get("inputs") or []), *(op.params.get("tools") or [])]:
            child = project.find(str(ident))
            if child is not None:
                pending.append(child)
    group = project.find(OPERATIONS_PATH)
    walked = list(group.walk()) if group is not None else []
    return [op.id for op in walked if op.id in wanted]


def _rules(session: Session, part: str) -> list[Any] | None:  # Any: retag.Rule
    """The part's retag rules, or None when it has no boundary tags (nothing goes stale)."""
    from stokes.api.shape_cycle import boundary_rules

    try:
        return boundary_rules(session, part)
    except OperationInvalid as exc:
        if "no boundary tags" in exc.message:
            return None
        raise


def _rebuild(session: Session, ops: list[str]) -> None:
    """Run each operation again and wait for it, in order; a failed run stops the apply."""
    from stokes.geometry.opstore import OperationStore

    store = OperationStore.of(session)
    project = session.require_project()
    for op_id in ops:
        store.start(op_id, reason="apply")
        if not store.wait(op_id):
            raise OperationFailed("the part took too long to rebuild", details={"op": op_id})
        node = project.get(op_id)
        if node.status == "failed":
            raise OperationFailed(
                f"{node.path}: {node.meta.get('error') or 'the operation failed'}",
                details={"op": op_id},
            )
    store.wait()  # downstream re-runs the changes started


def _retag(session: Session, part: str, rules: list[Any]) -> dict[str, int]:  # Any: Rule
    """Find each tag's faces on the rebuilt part and write them back; faces per tag name."""
    from stokes.geometry.retag import apply_rules
    from stokes.geometry.store import GeometryStore

    mesh = GeometryStore.of(session).mesh(part)
    if mesh is None:
        raise OperationFailed("the rebuilt part has no mesh", details={"part": part})
    found = apply_rules(mesh, rules)
    project = session.require_project()
    counts: dict[str, int] = {}
    for tag, ids in found.items():
        session.execute("tag.rematch", tag=tag, result=[{"part": part, "faces": ids}])
        counts[str(project.get(tag).name)] = len(ids)
    return counts


@command(
    "shape.apply",
    params=(
        ParamDef("part", "string", description="The implicit part: node id or path."),
        ParamDef(
            "values",
            "object",
            description="{reference: SI value}: the best point, as shape.optimise names its "
            "variables.",
        ),
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=False,
            description="Rebuild and re-find the tags before returning (true), or in the "
            "background with a shape.applied event at the end (false: the server stays "
            "responsive).",
        ),
    ),
)
def shape_apply(
    ctx: CommandContext, part: str, values: dict[str, Any], wait: bool = False
) -> dict[str, Any]:  # Any: JSON
    """Set a part's variables to an optimiser's point, rebuild the part and find its boundary tags
    again on the rebuilt surface, so the tags name its current faces and the case meshes."""
    from stokes.geometry.store import GeometryStore

    session = ctx.session
    project = session.require_project()
    node = project.get(part)
    if node.type != "part" or node.params.get("family") != "implicit":
        raise MeshOnly(
            f"{node.path} is not an implicit part: only parts made with Create are rebuilt",
            details={"part": node.id},
        )
    if not isinstance(values, dict) or not values:
        raise ValidationError("values is an object of reference: value")
    geometry = GeometryStore.of(session)
    if wait and geometry.blocks_session():
        raise ValidationError("wait=true would block the server; follow shape.applied instead")
    rules = _rules(session, node.id)
    ops = feeding_operations(project, node)
    session.execute("parameter.assign", values=values)
    # Replay applies the point and waits: the background half is not a separate record.
    ctx.journal_extra["wait"] = True
    part_id = node.id

    def finish(retag: Any) -> dict[str, Any]:  # Any: a callable running the retag
        _rebuild(session, ops)
        tags = retag() if rules else {}
        return {"part": part_id, "status": "done", "rebuilt": len(ops), "tags": tags or {}}

    if wait:
        return finish(lambda: _retag(session, part_id, rules or []))

    def run() -> None:
        cancel = threading.Event()
        try:
            out = finish(
                lambda: geometry.on_session_thread(
                    lambda: _retag(session, part_id, rules or []), cancel
                )
            )
            session.emit(EVENT, **out)
        except StokesError as exc:
            session.emit(EVENT, part=part_id, status="failed", error=exc.message, code=exc.code)
        except Exception:
            log.exception("shape apply failed", extra=fields(part=part_id))
            session.emit(
                EVENT,
                part=part_id,
                status="failed",
                error="applying the point failed unexpectedly; the log has the details",
                code="shape.internal",
            )

    threading.Thread(target=run, name=f"stokes-shape-apply-{part_id}", daemon=True).start()
    return {"part": part_id, "started": True, "rebuilding": len(ops)}
