"""The ``geometry`` engine (ADR-0035 decision 25, ADR-0037): design points that rebuild the shape.

A design point is the base case with the point's inputs **set** on a private copy of the project,
so every expression that reads a varied quantity follows it, as an edit in the app would: a
project parameter, an operation's parameter, a vector's component (``…cyl.position.x``) or a
sketch's dimension (``…sk.dimensions.W``). Its responses may then name what the shape measures,
after the part: ``geometry.parts.duct.volume``, ``geometry.parts.duct.area``,
``geometry.parts.duct.extent.x``, ``…centroid.z``, ``…bbox_min.y`` (:mod:`stokes.geometry.measure`).
The engine rebuilds the implicit chain of each part a response names from the operation nodes
(``opplan.implicit_recipe``), meshes it with the program a run uses (``stokes.implicit.bodymesh``,
cached by content, so an unchanged part is meshed once), and measures the mesh. Every other name
is answered as the ``function`` engine answers it.

A point fails, with its reason, when a value does not fit (a count that is not a whole number), a
part cannot be rebuilt (a split plane that misses its body) or an expression cannot be evaluated;
the other points carry on. No project is ever written to: the live one cannot change.

Imports: ``stokes.model``, ``stokes.units``, the study layer, and lazily ``stokes.geometry``'s
headless planning and ``stokes.implicit`` (no OCP, no worker).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.errors import ExpressionError, StokesError
from stokes.explore.definition import ResponseDef, StudyDefinition, si_quantity
from stokes.explore.engine_function import (
    _NUMERIC_FAILURES,
    PointResolver,
    _targets,
    _value,
)
from stokes.explore.evaluate import (
    CancelToken,
    PointRequest,
    PointResult,
    ResultCallback,
    StartCallback,
)
from stokes.log import fields, get_logger
from stokes.model.project import Project
from stokes.units.dimensions import Dimension, dimension
from stokes.units.registry import Quantity

if TYPE_CHECKING:
    from stokes.geometry.measure import Measures

log = get_logger(__name__)


class GeometryEngine:
    """The ``Evaluator`` whose points rebuild and measure implicit parts."""

    def __init__(self, cache: Path | None = None) -> None:
        if cache is None:
            from stokes import workspace_root

            cache = workspace_root.scratch_root() / "implicit-study"
        self._cache = cache

    @property
    def name(self) -> str:
        return "geometry"

    def evaluate(
        self,
        study: StudyDefinition,
        points: Sequence[PointRequest],
        *,
        cancel: CancelToken | None = None,
        on_start: StartCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> list[PointResult]:
        base = dict(study.project)
        targets = _targets(Project.from_json(copy.deepcopy(base)), study)
        responses = [(r, dimension(r.dimension)) for r in study.responses]
        results: list[PointResult] = []
        for point in points:
            if cancel is not None and cancel.cancelled:
                break
            if on_start is not None:
                on_start(point.index)
            result = self._point(base, targets, responses, point)
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results

    def _point(
        self,
        base: Mapping[str, Any],  # Any: the project's JSON
        targets: Mapping[str, Any],  # Any: engine_function._Target
        responses: Sequence[tuple[ResponseDef, Dimension]],
        point: PointRequest,
    ) -> PointResult:
        try:
            project = Project.from_json(copy.deepcopy(dict(base)))
            for name, target in targets.items():
                if name not in point.inputs:
                    return PointResult(point.index, "failed", error=f"no value for {name}")
                node_id, param = target.key
                project.set_param(node_id, param, float(point.inputs[name]))
            resolver = MeasuringResolver(project, self._cache)
            outputs = {
                response.name: _value(
                    response.expression, resolver, dim, f"response {response.name}"
                )
                for response, dim in responses
            }
        except StokesError as exc:
            return PointResult(point.index, "failed", error=exc.message)
        except _NUMERIC_FAILURES as exc:
            log.debug("design point failed", exc_info=True, extra=fields(index=point.index))
            return PointResult(point.index, "failed", error=f"cannot evaluate the point: {exc}")
        return PointResult(point.index, "done", outputs)


def base_resolver(project: Project, cache: Path | None = None) -> MeasuringResolver:
    """Names against the base case, parts' measures included: what a response or a constraint
    is checked with when it is defined."""
    return MeasuringResolver(project, cache or GeometryEngine()._cache)


class MeasuringResolver(PointResolver):
    """Expression names for a point whose overrides are already set on ``project``: a part's
    measure (``geometry.parts.duct.volume``) from its rebuilt mesh, anything else as the
    ``function`` engine resolves it."""

    def __init__(self, project: Project, cache: Path) -> None:
        super().__init__(project, {})
        self._cache = cache
        self._measured: dict[str, Measures] = {}

    def __call__(self, ref: str) -> Quantity:
        found = self._measure(ref)
        return found if found is not None else super().__call__(ref)

    def _measure(self, ref: str) -> Quantity | None:
        segments = [s for s in ref.split(".") if s]
        for size in (1, 2):  # volume; extent.x
            if len(segments) <= size:
                continue
            node = self._project.find("/" + "/".join(segments[:-size]))
            if node is None or node.type != "part":
                continue
            measures = self._measures_of(node)
            got = measures.get(".".join(segments[-size:]))
            if got is None:
                raise ExpressionError(
                    f"a part measures volume, area, centroid, bbox_min, bbox_max and extent; "
                    f"not {'.'.join(segments[-size:])!r}",
                    details={"ref": ref},
                )
            return si_quantity(got[0], dimension(got[1]))
        return None

    def _measures_of(self, part: Any) -> Measures:  # Any: Node
        from stokes.geometry.errors import OperationInvalid
        from stokes.geometry.measure import measure
        from stokes.geometry.operations.implicit import body_of
        from stokes.geometry.opplan import implicit_recipe
        from stokes.implicit.bodymesh import mesh_body

        found = self._measured.get(part.id)
        if found is not None:
            return found
        if part.params.get("family") != "implicit":
            raise OperationInvalid(
                f"{part.path} is not made with Create: a study rebuilds implicit parts only",
                details={"part": part.id},
            )
        made = mesh_body(body_of(implicit_recipe(self._project, part)), self._cache)
        found = measure([(made.positions, made.triangles)])
        self._measured[part.id] = found
        return found
