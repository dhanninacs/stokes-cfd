"""The boundary condition table: what each kind of patch says in `0/U` and `0/p`
(docs/05 §2, T-212).

One place for the mapping, because it is the part a reviewer must be able to check against the
documentation line by line, and because a condition written with the wrong `type` is a case that
runs and
means something else — the worst kind of bug in a CFD tool.

Every `type` word here was verified against the installed tutorials or `$FOAM_ETC` (see
`.coordination/T200-INVENTORY.md`); none is from memory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import Dict, Entry, Raw, Word
from stokes.compile.snapshot import (
    CONSTRAINT_KINDS,
    SYMMETRY_PATCH_TYPE,
    SYMMETRY_PATCH_TYPE_NONPLANAR,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.compile.snapshot import PatchCondition


def _vector(values: tuple[float, float, float]) -> Raw:
    """A vector through `Raw`: the printer has no vector node, and `uniform (0 0 0)` is one line."""
    x, y, z = values
    return Raw(f"uniform ({x} {y} {z})")


def velocity_field(condition: PatchCondition, rho: float = 1.0) -> Dict:
    """The `0/U` entry for one patch.

    A mass-flow inlet is `flowRateInletVelocity` with `massFlowRate constant <kg/s>` and a
    `rhoInlet` (keywords read from `fluid/squareBend` in the OpenFOAM 14 tutorials): the solver
    takes the density from the field where there is one and from `rhoInlet` where there is not,
    which is the incompressible case.
    """
    kind = condition.kind
    if kind == "mass_flow_inlet":
        if condition.mass_flow is None:
            raise CompileInvalid(f"the inlet {condition.name!r} has no mass flow")
        return Dict(
            (
                Entry("type", Word("flowRateInletVelocity")),
                Entry("massFlowRate", Raw(f"constant {condition.mass_flow}")),
                Entry("rhoInlet", rho),
            )
        )
    if kind == "velocity_inlet":
        if condition.velocity is None:
            # `validate_patch` refuses this too; kept here so the function is total on its own.
            raise CompileInvalid(f"the inlet {condition.name!r} has no velocity")
        return Dict(
            (Entry("type", Word("fixedValue")), Entry("value", _vector(condition.velocity)))
        )
    if kind == "pressure_outlet":
        # **`pressureInletOutletVelocity`**: at an outlet the flow can reverse, and this
        # condition lets
        # it come back in instead of the solver failing or inventing a value.
        return Dict(
            (
                Entry("type", Word("pressureInletOutletVelocity")),
                Entry("value", _vector((0.0, 0.0, 0.0))),
            )
        )
    if kind in ("wall", "unassigned"):
        return Dict((Entry("type", Word("noSlip")),))
    if kind == "slip_wall":
        return Dict((Entry("type", Word("slip")),))
    if kind in CONSTRAINT_KINDS:
        return Dict((Entry("type", Word(condition.patch_type)),))
    raise CompileUnsupported(
        f"no velocity condition for patch kind {kind!r}",
        details={"code": "compile.unsupported", "kind": kind, "patch": condition.name},
    )


def pressure_field(condition: PatchCondition, rho: float) -> Dict:
    """The `0/p` entry for one patch.

    **`p` in an incompressible solver is `p/rho`**, so a gauge pressure in pascals is divided by the
    density before it is written. Writing the pascals directly is a factor-of-rho error in the
    answer
    rather than a crash, which is exactly why the division is here rather than left to the caller.
    """
    kind = condition.kind
    if kind in ("velocity_inlet", "mass_flow_inlet"):
        return Dict((Entry("type", Word("zeroGradient")),))
    if kind == "pressure_outlet":
        if condition.pressure is None:
            raise CompileInvalid(f"the outlet {condition.name!r} has no pressure")
        if rho <= 0:
            raise CompileInvalid(f"rho must be positive to convert {condition.name!r}'s pressure")
        return Dict(
            (
                Entry("type", Word("fixedValue")),
                Entry("value", Raw(f"uniform {condition.pressure / rho}")),
            )
        )
    if kind in ("wall", "unassigned"):
        return Dict((Entry("type", Word("zeroGradient")),))
    if kind == "slip_wall":
        return Dict((Entry("type", Word("slip")),))
    if kind in CONSTRAINT_KINDS:
        return Dict((Entry("type", Word(condition.patch_type)),))
    raise CompileUnsupported(
        f"no pressure condition for patch kind {kind!r}",
        details={"code": "compile.unsupported", "kind": kind, "patch": condition.name},
    )


#: A `symmetry` condition may use OpenFOAM's planar `symmetryPlane` or non-planar `symmetry` mesh
#: patch. The field patch type must match the mesh patch type.
ALLOWED_PATCH_TYPES: dict[str, tuple[str, ...]] = {
    "velocity_inlet": ("patch",),
    "mass_flow_inlet": ("patch",),
    "pressure_outlet": ("patch",),
    "wall": ("wall", "patch"),
    "slip_wall": ("wall", "patch"),
    "symmetry": (SYMMETRY_PATCH_TYPE, SYMMETRY_PATCH_TYPE_NONPLANAR),
    # The 2D front/back (ADR-0031): the kind IS the patch type, set by the solve snapshot.
    "empty": ("empty",),
    "wedge": ("wedge",),
    # A default patch the mesher made and nobody assigned a condition to.
    # `noSlip`/`zeroGradient` is what
    # OpenFOAM's own tutorials use for one, and a patch with NO entry is a FOAM FATAL at start.
    "unassigned": ("patch", "wall"),
}


def validate_patch(condition: PatchCondition, rho: float) -> None:
    """Refuse a patch whose kind cannot be expressed, or whose patch type contradicts its kind."""
    if condition.kind not in ALLOWED_PATCH_TYPES:
        raise CompileUnsupported(
            f"patch {condition.name!r} has kind {condition.kind!r}, "
            "which this writer cannot express",
            details={"code": "compile.unsupported", "kind": condition.kind},
        )
    allowed = ALLOWED_PATCH_TYPES[condition.kind]
    if condition.patch_type not in allowed:
        raise CompileInvalid(
            f"patch {condition.name!r} is a {condition.patch_type!r} in the mesh, so a "
            f"{condition.kind!r} condition does not belong on it (expected {', '.join(allowed)})",
            details={
                "code": "compile.invalid",
                "patch": condition.name,
                "patch_type": condition.patch_type,
                "kind": condition.kind,
            },
        )
    if condition.kind == "velocity_inlet" and condition.velocity is None:
        raise CompileInvalid(
            f"the velocity inlet {condition.name!r} has no velocity vector",
            details={"code": "compile.invalid", "patch": condition.name},
        )
    if condition.kind == "mass_flow_inlet" and not (
        condition.mass_flow is not None and 0 < condition.mass_flow < float("inf")
    ):
        raise CompileInvalid(
            f"the mass-flow inlet {condition.name!r} needs a mass flow above zero",
            details={"code": "compile.invalid", "patch": condition.name},
        )
    if condition.kind == "pressure_outlet":
        if condition.pressure is None:
            raise CompileInvalid(
                f"the pressure outlet {condition.name!r} has no pressure",
                details={"code": "compile.invalid", "patch": condition.name},
            )
        if rho <= 0:
            raise CompileInvalid(
                f"rho must be positive to convert {condition.name!r}'s pressure to p/rho",
                details={"code": "compile.invalid", "rho": rho},
            )
