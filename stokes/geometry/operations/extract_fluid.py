"""The ``extract_fluid`` operation: the fluid body out of solid CAD (ADR-0021 decision 6.1).

``cavity`` takes the internal voids of closed bodies: for every solid of every input, the shells
other than ``BRepClass3d.OuterShell_s`` are voids, each made a solid and oriented so its volume is
positive; cavities below ``min_volume`` are skipped and each remaining one is an output named
``<input>_fluid``, ``_fluid_2``, .... ``region`` collects the faces that bound the fluid — every
face of the inputs, the faces of the ``caps`` tag, and, with ``auto_cap``, a planar face on each
closed free-edge loop the bodies still have — and asks ``BOPAlgo_MakerVolume`` for the closed
volume; the solid that holds the ``seed`` point is the fluid, and when none does the run fails
with ``geometry.seed_outside`` and lists every candidate's volume and centroid. The faces the
volume maker adds are the output's ``cap_tag`` tag, so the ends of a pipe come out ready to be
named inlet and outlet.

Units: shapes are in mm; ``fuzzy``, ``seed`` and ``min_volume`` arrive in SI and
:meth:`~stokes.geometry.operations.base.OpContext.mm` and its siblings convert them. The report is
SI (metres, cubic metres), as ``ctx.check`` reports it.

OCP traps kept: ``BOPAlgo_MakerVolume`` has ``HasErrors`` (the ``BRepAlgoAPI_*`` have ``IsDone``
only); ``BRepClass3d.OuterShell_s`` and ``BRepBuilderAPI_MakeSolid`` want a ``TopoDS_Solid``
and a ``TopoDS_Shell``, which ``TopoDS``'s statics give.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import GeometryError, OperationFailed, OperationInvalid, SeedOutside
from stokes.geometry.operations.base import OpContext, OpOutput, OpResult

MODES = ("region", "cavity")
FREE_BOUNDS_MM = 1e-4
"""The free-edge search's tolerance in millimetres when ``fuzzy`` is 0: 0.1 µm."""
CLASSIFIER_MM = 1e-6
"""The tolerance ``BRepClass3d_SolidClassifier`` classifies the seed point with, in mm."""
MM_PER_M = 1000.0
MM3_PER_M3 = MM_PER_M**3


def run(ctx: OpContext) -> OpResult:
    """Extract the fluid: the cavities of the closed bodies, or the region around the seed."""
    mode = str(ctx.params.get("mode", "region"))
    if mode not in MODES:
        raise OperationInvalid(
            f"mode must be one of {', '.join(MODES)}; got {mode!r}", details={"mode": mode}
        )
    fuzzy = ctx.mm(_number(ctx.params.get("fuzzy"), "fuzzy"))
    if fuzzy < 0.0:
        raise OperationInvalid("the fuzzy tolerance cannot be negative", details={"fuzzy": fuzzy})
    if mode == "cavity":
        return _cavity(ctx)
    return _region(ctx, fuzzy)


# --------------------------------------------------------------------- cavity
def _cavity(ctx: OpContext) -> OpResult:
    """One output per inner void of every input solid (ADR-0021 decision 6.1)."""
    before = [ctx.check(item.shape) for item in ctx.inputs]
    min_volume = ctx.mm3(_number(ctx.params.get("min_volume"), "min_volume"))
    outputs: list[OpOutput] = []
    cavities: list[dict[str, Any]] = []  # Any: JSON, the run's report
    warnings: list[str] = []
    for index, item in enumerate(ctx.inputs):
        ctx.progress("cavity", index / len(ctx.inputs), f"looking for cavities in {item.name}")
        made = 0
        for void in _voids(item.shape):
            volume, centroid = _mass_properties(void)
            if volume < min_volume:
                continue
            made += 1
            name = f"{item.name}_fluid" if made == 1 else f"{item.name}_fluid_{made}"
            after = ctx.check(void)
            cavities.append({"name": name, "volume": after["volume"], "centroid": centroid})
            if not after["valid"]:
                warnings.append(f"the fluid of {name} is not a valid B-rep")
            outputs.append(
                OpOutput(name, void, _identity_history(void), report={"check_after": after})
            )
        if made == 0:
            raise GeometryError(
                f"no closed cavity in {item.name}",
                details={"part": item.part, "min_volume": min_volume / MM3_PER_M3},
            )
    ctx.progress("cavity", 1.0, f"{len(outputs)} cavity(ies)")
    return OpResult(
        outputs,
        report={
            "mode": "cavity",
            "check_before": before,
            "check_after": outputs[0].report["check_after"],
            "cavities": cavities,
            "capped": 0,
            "uncapped": 0,
        },
        warnings=warnings,
    )


def _voids(shape: Any) -> list[Any]:  # Any: OCP TopoDS_Shape in, TopoDS_Solid out
    """The inner voids of the solids of ``shape``: every shell of a solid other than its outer
    shell, made a solid and oriented so ``GProp`` gives it a positive volume."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepClass3d import BRepClass3d
    from OCP.BRepLib import BRepLib
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopoDS import TopoDS

    found: list[Any] = []  # Any: TopoDS_Solid
    for solid in _subshapes(shape, TopAbs_ShapeEnum.TopAbs_SOLID):
        outer = BRepClass3d.OuterShell_s(TopoDS.Solid(solid))
        for shell in _subshapes(solid, TopAbs_ShapeEnum.TopAbs_SHELL):
            if shell.IsSame(outer):
                continue
            void = BRepBuilderAPI_MakeSolid(TopoDS.Shell(shell)).Solid()
            BRepLib.OrientClosedSolid_s(void)
            found.append(void)
    return found


def _identity_history(shape: Any) -> Any:  # Any: OCP TopoDS_Shape in, BRepTools_History out
    """A history recording every face of ``shape`` as modified into itself.

    A void's faces are the input's inner faces unchanged, so this is what carries a tag the user
    made on the inside walls of a body onto the body's fluid.
    """
    from OCP.BRepTools import BRepTools_History
    from OCP.TopAbs import TopAbs_ShapeEnum

    history = BRepTools_History()
    for face in _subshapes(shape, TopAbs_ShapeEnum.TopAbs_FACE):
        history.AddModified(face, face)
    return history


# --------------------------------------------------------------------- region
def _region(ctx: OpContext, fuzzy: float) -> OpResult:
    """The volume around the seed point, once the openings are capped (ADR-0021 decision 6.1)."""
    import OCP.collections as occ
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopoDS import TopoDS

    from stokes.geometry.operations.faces import face_list

    before = [ctx.check(item.shape) for item in ctx.inputs]
    seed = _point(ctx, "seed")
    arguments, auto_caps, user_caps, uncapped = _arguments(ctx, fuzzy)
    maker: Any = BOPAlgo_MakerVolume()  # Any: an OCP BOPAlgo_MakerVolume
    listed = occ.List_TopoDS_Shape()
    for face in arguments:
        listed.Append(face)
    maker.SetArguments(listed)
    maker.SetIntersect(True)
    maker.SetAvoidInternalShapes(True)
    maker.SetFuzzyValue(fuzzy)
    maker.SetToFillHistory(True)
    ctx.progress("region", 0.4, f"making the volume from {len(arguments)} face(s)")
    maker.Perform()
    if maker.HasErrors():
        raise OperationFailed(
            "OCCT could not make a volume from the faces of the inputs",
            details={"faces": len(arguments)},
        )
    solids = [TopoDS.Solid(s) for s in _subshapes(maker.Shape(), TopAbs_ShapeEnum.TopAbs_SOLID)]
    if not solids:
        raise OperationFailed(
            "OCCT made no closed volume from the faces of the inputs",
            details={"faces": len(arguments)},
        )
    chosen, candidates = _choose(solids, seed)
    ctx.progress("region", 0.8, f"{len(solids)} candidate volume(s)")
    if chosen is None:
        raise SeedOutside(
            "the seed point is outside every volume the faces bound; pick a point inside the fluid",
            details={"seed": [v / MM_PER_M for v in seed], "candidates": candidates},
        )
    after = ctx.check(chosen)
    history = maker.History()
    faces = face_list(chosen)
    cap_ids = _cap_ids(chosen, faces, history, ctx, [*auto_caps, *user_caps])
    cap_tag = str(ctx.params.get("cap_tag") or "caps")
    warnings: list[str] = []
    if not after["valid"]:
        warnings.append("the extracted fluid is not a valid B-rep")
    if uncapped:
        warnings.append(
            f"{uncapped} closed free-edge loop(s) are not planar and were left open; the cap "
            f"operation can close them"
        )
    ctx.progress("region", 1.0, "the fluid is ready")
    return OpResult(
        [
            OpOutput(
                f"{ctx.inputs[0].name}_fluid",
                chosen,
                history,
                {cap_tag: [faces[i] for i in cap_ids]} if cap_ids else {},
                {"check_after": after},
            )
        ],
        report={
            "mode": "region",
            "check_before": before,
            "check_after": after,
            "candidates": candidates,
            "capped": len(auto_caps),
            "uncapped": uncapped,
        },
        warnings=warnings,
    )


def _arguments(ctx: OpContext, fuzzy: float) -> tuple[list[Any], list[Any], list[Any], int]:
    """The faces the fluid volume is made from: every face of the inputs, the faces of the
    ``caps`` tag, and a planar cap on each closed free-edge loop when ``auto_cap``. Also the
    auto-caps, the tag's caps, and how many loops were left open."""
    from stokes.geometry.operations.faces import face_list

    ctx.progress("region", 0.1, "collecting the faces that bound the fluid")
    body: list[Any] = []  # Any: TopoDS_Face
    for item in ctx.inputs:
        body.extend(face_list(item.shape))
    user_caps = ctx.faces(_members(ctx))
    auto_caps: list[Any] = []  # Any: TopoDS_Face
    uncapped = 0
    if bool(ctx.params.get("auto_cap", True)):
        auto_caps, uncapped = _planar_caps(ctx, fuzzy)
    arguments = [*body, *user_caps, *auto_caps]
    if not arguments:
        raise OperationInvalid("there are no faces to make the fluid volume from")
    return arguments, auto_caps, user_caps, uncapped


def _choose(solids: list[Any], seed: list[float]) -> tuple[Any, list[dict[str, Any]]]:
    """The solid that holds ``seed`` (or None) and every candidate's volume and centroid, SI."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_State

    chosen: Any = None  # Any: TopoDS_Solid
    candidates: list[dict[str, Any]] = []  # Any: JSON
    for solid in solids:
        volume, centroid = _mass_properties(solid)
        candidates.append({"volume": volume / MM3_PER_M3, "centroid": centroid})
        classifier = BRepClass3d_SolidClassifier(solid)
        classifier.Perform(gp_Pnt(*seed), CLASSIFIER_MM)
        if chosen is None and classifier.State() == TopAbs_State.TopAbs_IN:
            chosen = solid
    return chosen, candidates


def _planar_caps(ctx: OpContext, fuzzy: float) -> tuple[list[Any], int]:
    """A face on each closed free-edge loop of the inputs that ``BRepLib_FindSurface`` finds
    planar, and how many loops it did not: those stay open (that is the ``cap`` operation).
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepLib import BRepLib_FindSurface
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for item in ctx.inputs:
        builder.Add(compound, item.shape)
    tolerance = fuzzy if fuzzy > 0.0 else FREE_BOUNDS_MM
    bounds = ShapeAnalysis_FreeBounds(compound, tolerance, False, False)
    caps: list[Any] = []  # Any: TopoDS_Face
    uncapped = 0
    for wire in _subshapes(bounds.GetClosedWires(), TopAbs_ShapeEnum.TopAbs_WIRE):
        if not BRepLib_FindSurface(wire, -1, True).Found():
            uncapped += 1
            continue
        caps.append(BRepBuilderAPI_MakeFace(TopoDS.Wire(wire), True).Face())
    ctx.progress("region", 0.3, f"capped {len(caps)} opening(s), left {uncapped} open")
    return caps, uncapped


def _cap_ids(
    shape: Any,  # Any: OCP TopoDS_Shape
    faces: list[Any],  # Any: TopoDS_Face, in face-id order
    history: Any,  # Any: the volume maker's BRepTools_History
    ctx: OpContext,
    sources: list[Any],  # Any: TopoDS_Face, the faces this run added to seal the openings
) -> list[int]:
    """The face ids of the output that are caps: the faces the run added as arguments, and every
    output face that comes from no input face at all (a cap the volume maker rebuilt).

    The face the maker returns for an argument is sometimes the argument's own TShape and
    sometimes a rebuilt one (both seen on OCP 8.0.1), so the history names it and ``IsSame``
    (through :func:`_images`) is the fallback.
    """
    from stokes.geometry.operations.faces import face_table, map_history

    table = face_table(shape)
    found = _images(shape, sources, history, table)
    mapped = {
        i
        for per_input in map_history(history, ctx.inputs, shape).values()
        for ids in per_input.values()
        for i in ids
    }
    return sorted(found | (set(range(len(faces))) - mapped))


def _images(
    shape: Any,  # Any: OCP TopoDS_Shape
    faces: list[Any],  # Any: TopoDS_Face
    history: Any,  # Any: BRepTools_History or None
    table: Any,  # Any: stokes.geometry.operations.faces.FaceTable, for the output
) -> set[int]:
    """The output face ids ``faces`` became: the history's modified and generated faces, or the
    face itself when the history does not know it (:func:`faces.ids_of` asks ``IsSame``)."""
    from OCP.TopAbs import TopAbs_ShapeEnum

    from stokes.geometry.operations.faces import ids_of

    found: set[int] = set()
    for face in faces:
        successors: list[Any] = []  # Any: TopoDS_Shape
        if history is not None and not history.IsRemoved(face):
            successors = [
                s
                for s in (*history.Modified(face), *history.Generated(face))
                if s.ShapeType() == TopAbs_ShapeEnum.TopAbs_FACE
            ]
        found.update(ids_of(shape, successors or [face], table=table))
    return found


# --------------------------------------------------------------------- helpers
def _subshapes(shape: Any, kind: Any) -> list[Any]:  # Any: OCP shape, TopAbs_ShapeEnum; shapes out
    """The sub-shapes of ``shape`` of one kind, in ``TopExp_Explorer`` order with repeats."""
    from OCP.TopExp import TopExp_Explorer

    found: list[Any] = []  # Any: TopoDS_Shape
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    return found


def _mass_properties(shape: Any) -> tuple[float, list[float]]:  # Any: OCP TopoDS_Shape
    """A solid's volume in mm^3 and its centre of mass in metres (SI, as the report wants)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    volume = float(props.Mass())
    if volume < 0.0:  # a shell OCCT could not orient: report the magnitude, not a negative
        volume = -volume
    centre = props.CentreOfMass()
    return volume, [centre.X() / MM_PER_M, centre.Y() / MM_PER_M, centre.Z() / MM_PER_M]


def _point(ctx: OpContext, name: str) -> list[float]:
    """A three-length parameter in mm."""
    raw = ctx.params.get(name)
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise OperationInvalid(f"{name} must be three lengths", details={"parameter": name})
    return [ctx.mm(_number(v, name)) for v in raw]


def _members(ctx: OpContext) -> list[dict[str, Any]]:  # Any: JSON
    """The stored members of the ``caps`` tag, which the core resolves as ``caps_members``."""
    raw = ctx.params.get("caps_members")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OperationInvalid(
            "caps_members must be a list of tag members", details={"parameter": "caps"}
        )
    return [m for m in raw if isinstance(m, dict)]


def _number(value: Any, name: str) -> float:
    """A scalar parameter as a float; anything else is :class:`OperationInvalid`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OperationInvalid(f"{name} must be a number", details={"parameter": name})
    return float(value)
