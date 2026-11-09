"""The method catalogue (ADR-0018 decision 3): which methods each property takes, with their
parameters, the Foundation 14 selection and the Fluent method they map to.

:data:`METHODS` is a tuple of :class:`MethodSpec`, one per (property kind, method id): the same
method id means different formulas for different properties (kinetic theory gives a viscosity, a
specific heat or a conductivity), so a spec belongs to one property. ``material.methods`` returns
the table filtered by material kind and property. The formulas are in
``docs/engineering/materials-schema.md``; ``stokes.physics.materials.methods`` evaluates them, and
:attr:`MethodSpec.evaluate` resolves the evaluator lazily so that this module stays numpy-free.

"OF14" texts name the Foundation 14 selection and its keys; ``(rt)`` marks a selection Foundation
12-14 compile on the engine host on first use. "Fluent" names the Create/Edit Materials method;
``expr`` means the Fluent route writes a property expression. Both are what the compiler (ADR-0018
slice 3) and the panel show; nothing here writes a case.

Size: one table of about fifty entries, kept in one module so that a review compares it with
the contract's tables line by line.

Import rule: the standard library, ``stokes.errors``, ``model`` and ``errors``; no numpy at module
level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError
from stokes.physics.materials.errors import InvalidMethod
from stokes.physics.materials.model import KIND_PROPERTIES, MATERIAL_KINDS, PROPERTY_KINDS

if TYPE_CHECKING:
    from stokes.physics.materials.methods import Evaluator

RULES: tuple[str, ...] = ("any", "positive", "nonnegative", "nonzero")
"""Checks on a scalar parameter's SI value; arrays are checked per method by ``evaluate``."""

FLUID = ("fluid",)
SOLID = ("solid",)
BOTH = ("fluid", "solid")
TABLES_NOTE = "Held at the end values outside the table."


@dataclass(frozen=True)
class ParamUse:
    """A property-node parameter a method reads. ``required`` parameters must hold a valid value
    for the method to evaluate; optional ones are written by the compiler only."""

    name: str
    rule: str = "any"
    required: bool = True

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValidationError(f"unknown rule {self.rule!r} for {self.name!r}")


@dataclass(frozen=True)
class MethodSpec:
    """One method of one property kind."""

    id: str
    label: str
    property_kind: str
    material_kinds: tuple[str, ...]
    params: tuple[ParamUse, ...]
    evaluator: str
    of14: tuple[tuple[str, str], ...]
    fluent: str
    note: str = ""
    independent: str = "T"
    uses: tuple[str, ...] = ()
    """Material-level inputs: ``molar_mass``, ``T_ref``, ``p_ref``, ``p`` (the evaluation
    pressure), ``viscosity`` and ``specific_heat`` (the material's own, on the same grid)."""
    arrives: str = ""
    """Empty when the method can be chosen now; otherwise the release that brings it."""

    @property
    def available(self) -> bool:
        return not self.arrives

    @property
    def needs_molar_mass(self) -> bool:
        return "molar_mass" in self.uses

    @property
    def pressure_dependent(self) -> bool:
        return "p" in self.uses

    @property
    def param_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params)

    def of14_for(self, material_kind: str) -> str:
        for kind, text in self.of14:
            if kind == material_kind:
                return text
        return "—"

    @property
    def evaluate(self) -> Evaluator:
        """The numpy evaluator, imported on first use."""
        from stokes.physics.materials.methods import EVALUATORS

        if not self.available:
            # **Names a version, not an internal document** (claude-win2's review item 9, found
            # by the user-text gate once it read Python): the message read *"…arrives with
            # ADR-0018 slice 5"*, and neither half means anything to a user. The catalogue's
            # own `arrives` field carries the release, which is the half worth saying.
            raise InvalidMethod(
                f"{self.property_kind} method {self.id!r} is not available in this version"
                + (f"; it arrives in {self.arrives}" if self.arrives else "")
            )
        return EVALUATORS[self.evaluator]

    def to_json(self, material_kind: str) -> dict[str, Any]:  # Any: JSON
        return {
            "id": self.id,
            "label": self.label,
            "property": self.property_kind,
            "material_kinds": list(self.material_kinds),
            "params": [
                {"name": p.name, "rule": p.rule, "required": p.required} for p in self.params
            ],
            "independent": self.independent,
            "uses": list(self.uses),
            "needs_molar_mass": self.needs_molar_mass,
            "pressure_dependent": self.pressure_dependent,
            "of14": self.of14_for(material_kind),
            "fluent": self.fluent,
            "note": self.note,
            "available": self.available,
            "arrives": self.arrives or None,
        }


def _p(name: str, rule: str = "any", required: bool = True) -> ParamUse:
    return ParamUse(name, rule, required)


VALUE = _p("value", "positive")
SIGNED_VALUE = _p("value")
T0 = _p("reference_temperature", "positive")
EXPONENT = _p("exponent")
COEFFS = _p("coefficients")
TABLE = (_p("table_temperatures"), _p("table_values"))
SEGMENTS = (_p("breakpoints"), _p("degree", "nonnegative"), _p("coefficients"))
SF = _p("formation_entropy", required=False)
NN = "non-Newtonian law against strain rate; laminar only in Fluent"


def _of14(fluid: str = "—", solid: str = "—") -> tuple[tuple[str, str], ...]:
    return (("fluid", fluid), ("solid", solid))


_DENSITY: tuple[MethodSpec, ...] = (
    MethodSpec(
        "constant",
        "Constant",
        "density",
        BOTH,
        (VALUE,),
        "constant",
        _of14("rhoConst (rho)", "rhoConst (rho)"),
        "constant",
    ),
    MethodSpec(
        "ideal_gas",
        "Ideal gas",
        "density",
        FLUID,
        (),
        "ideal_gas",
        _of14("perfectGas (R from molWeight)"),
        "ideal-gas",
        "rho = p M / (R T) with the absolute pressure.",
        uses=("molar_mass", "p"),
    ),
    MethodSpec(
        "incompressible_ideal_gas",
        "Incompressible ideal gas",
        "density",
        FLUID,
        (),
        "incompressible_ideal_gas",
        _of14("incompressiblePerfectGas (pRef) (rt)"),
        "incompressible-ideal-gas",
        "rho = p_ref M / (R T): the material's p_ref, not the local pressure.",
        uses=("molar_mass", "p_ref"),
    ),
    MethodSpec(
        "boussinesq",
        "Boussinesq",
        "density",
        FLUID,
        (VALUE, _p("expansion_coefficient")),
        "boussinesq",
        _of14("Boussinesq (rho0, T0, beta)"),
        "boussinesq (therm_exp_coeff)",
        "rho0 (1 - beta (T - T_ref)) in the buoyancy term only; T_ref is the material's.",
        uses=("T_ref",),
    ),
    MethodSpec(
        "polynomial",
        "Polynomial",
        "density",
        FLUID,
        (COEFFS,),
        "polynomial",
        _of14("icoPolynomial (rhoCoeffs<8>) (rt), or a table"),
        "polynomial",
        "Sum of c_i T^i, at most 8 coefficients.",
    ),
    MethodSpec(
        "piecewise_linear",
        "Piecewise linear",
        "density",
        FLUID,
        TABLE,
        "piecewise_linear",
        _of14("icoTabulated (rho values)"),
        "piecewise-linear (at most 50 points)",
        TABLES_NOTE,
    ),
    MethodSpec(
        "piecewise_polynomial",
        "Piecewise polynomial",
        "density",
        FLUID,
        SEGMENTS,
        "piecewise_polynomial",
        _of14("— tabulated"),
        "piecewise-polynomial (3 ranges x 8)",
    ),
    MethodSpec(
        "rational_polynomial",
        "Rational polynomial",
        "density",
        FLUID,
        (_p("coefficients"),),
        "rational_polynomial",
        _of14("rPolynomial (C, 5)"),
        "expr (C3 = C4 = 0)",
        "1/rho = C0 + C1 T + C2 T^2 - C3 p - C4 p T.",
        uses=("p",),
    ),
    MethodSpec(
        "perfect_fluid",
        "Perfect fluid",
        "density",
        FLUID,
        (_p("value", "nonnegative"),),
        "perfect_fluid",
        _of14("perfectFluid (rho0, R) (rt)"),
        "expr",
        "rho = rho0 + p / (R T), R = R_u / M.",
        uses=("molar_mass", "p"),
    ),
    MethodSpec(
        "compressible_liquid",
        "Compressible liquid (Tait)",
        "density",
        FLUID,
        (VALUE, _p("bulk_modulus", "positive"), _p("exponent", "nonzero")),
        "compressible_liquid",
        _of14("adiabaticPerfectFluid (rho0, p0, gamma = n, B = K0/n - p0)"),
        "compressible-liquid",
        "rho = rho0 (1 + n (p - p_ref) / K0)^(1/n); water: 998.2 kg/m3, 2.2 GPa, 7.15.",
        uses=("p_ref", "p"),
    ),
    MethodSpec(
        "peng_robinson",
        "Peng-Robinson",
        "density",
        FLUID,
        (
            _p("critical_temperature", "positive"),
            _p("critical_pressure", "positive"),
            _p("critical_volume", "positive"),
            _p("acentric_factor"),
        ),
        "peng_robinson",
        _of14("PengRobinsonGas (Tc, Vc, Pc, omega) (rt)"),
        "real-gas-peng-robinson",
        "Cubic equation of state; the vapour (largest) root.",
        uses=("molar_mass", "p"),
    ),
    MethodSpec(
        "real_gas_table",
        "Real-gas table",
        "density",
        FLUID,
        (),
        "",
        _of14("rhoTabulated + hTabulated + tabulated"),
        "real-gas-nist or rgp-table",
        "(p, T) table from a library provider.",
        uses=("p",),
        arrives="v1.5",
    ),
)

_VISCOSITY: tuple[MethodSpec, ...] = (
    MethodSpec(
        "constant",
        "Constant",
        "viscosity",
        FLUID,
        (VALUE,),
        "constant",
        _of14("const (mu; kappa from the conductivity)"),
        "constant",
    ),
    MethodSpec(
        "sutherland",
        "Sutherland",
        "viscosity",
        FLUID,
        (_p("sutherland_coefficient", "positive"), _p("sutherland_temperature", "nonnegative")),
        "sutherland",
        _of14("sutherland (As, Ts); kappa by the Eucken relation"),
        "sutherland (two-coefficient)",
        "mu = A_s T^1.5 / (T + T_s); air 1.458e-6, 110.4 K.",
    ),
    MethodSpec(
        "power_law",
        "Power law",
        "viscosity",
        FLUID,
        (VALUE, T0, EXPONENT),
        "power_law",
        _of14("— fitted or tabulated"),
        "power-law (three-coefficient)",
        "mu = mu0 (T / T0)^n; air 1.716e-5, 273 K, 2/3.",
    ),
    MethodSpec(
        "polynomial",
        "Polynomial",
        "viscosity",
        FLUID,
        (COEFFS,),
        "polynomial",
        _of14("polynomial (muCoeffs<8>, kappaCoeffs<8>) (rt)"),
        "polynomial",
    ),
    MethodSpec(
        "log_polynomial",
        "Log-polynomial",
        "viscosity",
        FLUID,
        (COEFFS,),
        "log_polynomial",
        _of14("logPolynomial (muLogCoeffs<8>, kappaLogCoeffs<8>) (rt)"),
        "expr",
        "ln mu = sum of c_i (ln T)^i.",
    ),
    MethodSpec(
        "andrade",
        "Andrade",
        "viscosity",
        FLUID,
        (COEFFS,),
        "andrade",
        _of14("Andrade (muCoeffs, kappaCoeffs; 5 each) (rt)"),
        "expr",
        "ln mu = c0 + c1 T + c2 T^2 + c3 / (c4 + T).",
    ),
    MethodSpec(
        "wlf",
        "Williams-Landel-Ferry",
        "viscosity",
        FLUID,
        (VALUE, T0, COEFFS),
        "wlf",
        _of14("WLF (mu0, Tr, C1, C2, Pr) (rt)"),
        "expr",
        "ln(mu / mu0) = -C1 (T - T0) / (C2 + T - T0); coefficients C1, C2.",
    ),
    MethodSpec(
        "piecewise_linear",
        "Piecewise linear",
        "viscosity",
        FLUID,
        TABLE,
        "piecewise_linear",
        _of14("icoTabulated (mu and kappa tables)"),
        "piecewise-linear",
        TABLES_NOTE,
    ),
    MethodSpec(
        "piecewise_polynomial",
        "Piecewise polynomial",
        "viscosity",
        FLUID,
        SEGMENTS,
        "piecewise_polynomial",
        _of14("— tabulated"),
        "piecewise-polynomial",
    ),
    MethodSpec(
        "kinetic_theory",
        "Kinetic theory",
        "viscosity",
        FLUID,
        (_p("collision_diameter", "positive"), _p("well_depth_temperature", "positive")),
        "kinetic_theory_viscosity",
        _of14("— fitted"),
        "kinetic-theory (lennard_jones_length, lennard_jones_energy)",
        "Chapman-Enskog with the Neufeld collision integral; Fluent needs ideal-gas density.",
        uses=("molar_mass",),
    ),
    MethodSpec(
        "shear_power_law",
        "Power law (shear)",
        "viscosity",
        FLUID,
        (_p("consistency"), EXPONENT, _p("viscosity_min"), _p("viscosity_max")),
        "",
        _of14("generalisedNewtonian powerLaw (k, n, nuMin, nuMax)"),
        "non-newtonian-power-law",
        NN,
        independent="strain_rate",
        arrives="a later release",
    ),
    MethodSpec(
        "carreau",
        "Carreau",
        "viscosity",
        FLUID,
        (VALUE, _p("viscosity_infinite"), _p("time_constant"), EXPONENT, _p("transition_exponent")),
        "",
        _of14("generalisedNewtonian BirdCarreau (nuInf, k, n, a)"),
        "carreau",
        NN,
        independent="strain_rate",
        arrives="a later release",
    ),
    MethodSpec(
        "cross",
        "Cross",
        "viscosity",
        FLUID,
        (VALUE, _p("viscosity_infinite"), _p("time_constant"), EXPONENT),
        "",
        _of14("generalisedNewtonian CrossPowerLaw (nuInf, m, n)"),
        "cross",
        NN,
        independent="strain_rate",
        arrives="a later release",
    ),
    MethodSpec(
        "herschel_bulkley",
        "Herschel-Bulkley",
        "viscosity",
        FLUID,
        (VALUE, _p("yield_stress"), _p("consistency"), EXPONENT),
        "",
        _of14("generalisedNewtonian HerschelBulkley (tau0, k, n)"),
        "herschel-bulkley",
        NN,
        independent="strain_rate",
        arrives="a later release",
    ),
    MethodSpec(
        "casson",
        "Casson",
        "viscosity",
        FLUID,
        (_p("consistency"), _p("yield_stress"), _p("viscosity_min"), _p("viscosity_max")),
        "",
        _of14("generalisedNewtonian Casson (m, tau0, nuMin, nuMax)"),
        "—",
        NN,
        independent="strain_rate",
        arrives="a later release",
    ),
)

_SPECIFIC_HEAT: tuple[MethodSpec, ...] = (
    MethodSpec(
        "constant",
        "Constant",
        "specific_heat",
        BOTH,
        (VALUE,),
        "constant",
        _of14("hConst (Cp, hf), or eConst (Cv)", "eConst (Cv = c_p, hf)"),
        "constant",
    ),
    MethodSpec(
        "polynomial",
        "Polynomial",
        "specific_heat",
        BOTH,
        (COEFFS, SF),
        "polynomial",
        _of14("hPolynomial (CpCoeffs<8>, hf, sf) (rt)", "ePolynomial (CvCoeffs<8>, hf, sf)"),
        "polynomial",
    ),
    MethodSpec(
        "janaf",
        "JANAF (NASA-7)",
        "specific_heat",
        FLUID,
        (COEFFS, _p("breakpoints"), SF),
        "janaf",
        _of14("janaf (Tlow, Thigh, Tcommon, lowCpCoeffs, highCpCoeffs)"),
        "piecewise-polynomial (two ranges, scaled by R/M)",
        "c_p/R = a1 + a2 T + a3 T^2 + a4 T^3 + a5 T^4; 7 low then 7 high coefficients; "
        "breakpoints T_low, T_common, T_high.",
        uses=("molar_mass",),
    ),
    MethodSpec(
        "nasa9_polynomial",
        "NASA-9 polynomial",
        "specific_heat",
        FLUID,
        (COEFFS, _p("breakpoints"), SF),
        "nasa9_polynomial",
        _of14("— refitted to janaf"),
        "nasa-9-piecewise-polynomial",
        "c_p/R = a1/T^2 + a2/T + a3 + a4 T + a5 T^2 + a6 T^3 + a7 T^4; 9 coefficients per "
        "segment, at most 8 segments.",
        uses=("molar_mass",),
    ),
    MethodSpec(
        "power",
        "Power",
        "specific_heat",
        BOTH,
        (VALUE, T0, EXPONENT),
        "power_law",
        _of14("hPower (c0, n0, Tref, hf) (rt)", "ePower (c0, n0, Tref)"),
        "polynomial (refit)",
        "c_p = c0 (T / T0)^n0.",
    ),
    MethodSpec(
        "piecewise_linear",
        "Piecewise linear",
        "specific_heat",
        BOTH,
        (*TABLE, SF),
        "piecewise_linear",
        _of14("hIcoTabulated (Cp values, hf, sf)", "eIcoTabulated (Cv values)"),
        "piecewise-linear",
        TABLES_NOTE,
    ),
    MethodSpec(
        "piecewise_polynomial",
        "Piecewise polynomial",
        "specific_heat",
        BOTH,
        SEGMENTS,
        "piecewise_polynomial",
        _of14("— tabulated, or janaf for a JANAF pair", "— tabulated"),
        "piecewise-polynomial",
    ),
    MethodSpec(
        "kinetic_theory",
        "Kinetic theory",
        "specific_heat",
        FLUID,
        (_p("degrees_of_freedom", "nonnegative"),),
        "kinetic_theory_specific_heat",
        _of14("written as hConst"),
        "kinetic-theory",
        "c_p = (R / M)(f + 2) / 2.",
        uses=("molar_mass",),
    ),
)

_CONDUCTIVITY: tuple[MethodSpec, ...] = (
    MethodSpec(
        "constant",
        "Constant",
        "thermal_conductivity",
        BOTH,
        (VALUE,),
        "constant",
        _of14("kappa in const; ignored with sutherland", "constIsoSolid (kappa)"),
        "constant",
    ),
    MethodSpec(
        "anisotropic",
        "Anisotropic",
        "thermal_conductivity",
        SOLID,
        (_p("principal_values"),),
        "anisotropic",
        _of14(solid="constAnisoSolid (kappa (k1 k2 k3)) with the anisotropic transport"),
        "orthotropic",
        "Three conductivities along the region's coordinate axes.",
    ),
    MethodSpec(
        "polynomial",
        "Polynomial",
        "thermal_conductivity",
        BOTH,
        (COEFFS,),
        "polynomial",
        _of14("polynomial (paired with mu) (rt)", "polynomialSolid (kappaCoeffs<8>)"),
        "polynomial",
    ),
    MethodSpec(
        "log_polynomial",
        "Log-polynomial",
        "thermal_conductivity",
        BOTH,
        (COEFFS,),
        "log_polynomial",
        _of14("logPolynomial (paired) (rt)", "— tabulated"),
        "expr",
        "ln kappa = sum of c_i (ln T)^i.",
    ),
    MethodSpec(
        "exponential",
        "Exponential",
        "thermal_conductivity",
        BOTH,
        (VALUE, T0, EXPONENT),
        "power_law",
        _of14("— tabulated", "exponentialSolid (kappa0, n0, Tref)"),
        "expr",
        "kappa = kappa0 (T / T0)^n0.",
    ),
    MethodSpec(
        "piecewise_linear",
        "Piecewise linear",
        "thermal_conductivity",
        BOTH,
        TABLE,
        "piecewise_linear",
        _of14("icoTabulated (paired)", "tabulatedSolid (kappa values)"),
        "piecewise-linear",
        TABLES_NOTE,
    ),
    MethodSpec(
        "piecewise_polynomial",
        "Piecewise polynomial",
        "thermal_conductivity",
        BOTH,
        SEGMENTS,
        "piecewise_polynomial",
        _of14("— tabulated", "— tabulated"),
        "piecewise-polynomial",
    ),
    MethodSpec(
        "kinetic_theory",
        "Kinetic theory",
        "thermal_conductivity",
        FLUID,
        (),
        "kinetic_theory_conductivity",
        _of14("— fitted"),
        "kinetic-theory",
        "kappa = (15/4)(R/M) mu ((4/15) c_p M / R + 1/3) from the material's own viscosity "
        "and specific heat.",
        uses=("molar_mass", "viscosity", "specific_heat"),
    ),
)

_SPECIES: tuple[MethodSpec, ...] = (
    MethodSpec(
        "constant",
        "Constant",
        "molar_mass",
        BOTH,
        (_p("value", "nonnegative"),),
        "constant",
        _of14("specie { molWeight } in kg/kmol", "specie { molWeight }, unused with rhoConst"),
        "molecular_weight",
        "0 means unknown.",
    ),
    MethodSpec(
        "constant",
        "Constant",
        "formation_enthalpy",
        FLUID,
        (SIGNED_VALUE,),
        "constant",
        _of14("hf"),
        "formation_enthalpy",
        "0 unless the case burns.",
    ),
)

METHODS: tuple[MethodSpec, ...] = _DENSITY + _VISCOSITY + _SPECIFIC_HEAT + _CONDUCTIVITY + _SPECIES

DEFAULT_METHOD: dict[tuple[str, str], str] = {
    (prop, kind): "constant" for kind in MATERIAL_KINDS for prop in KIND_PROPERTIES[kind]
}
"""The method a new property starts with, per (property kind, material kind)."""

_BY_KEY: dict[tuple[str, str], MethodSpec] = {(m.property_kind, m.id): m for m in METHODS}


def method(property_kind: str, method_id: str) -> MethodSpec:
    """The catalogue entry, or :class:`InvalidMethod` when the property has no such method."""
    found = _BY_KEY.get((property_kind, method_id))
    if found is None:
        known = ", ".join(m.id for m in methods_for(property_kind))
        raise InvalidMethod(
            f"{property_kind} has no method {method_id!r}; known: {known or 'none'}",
            details={"property": property_kind, "method": method_id},
        )
    return found


def methods_for(property_kind: str, material_kind: str | None = None) -> list[MethodSpec]:
    """The methods of one property, for one material kind when given (unavailable ones too)."""
    return [
        m
        for m in METHODS
        if m.property_kind == property_kind
        and (material_kind is None or material_kind in m.material_kinds)
    ]


def check_choice(spec: MethodSpec, material_kind: str) -> None:
    """Refuse a method the material kind does not take or that is not available yet."""
    if material_kind not in spec.material_kinds:
        raise InvalidMethod(
            f"{spec.property_kind} method {spec.id!r} is for {' and '.join(spec.material_kinds)} "
            f"materials, not {material_kind}",
            details={"property": spec.property_kind, "method": spec.id, "kind": material_kind},
        )
    if not spec.available:
        raise InvalidMethod(
            f"{spec.property_kind} method {spec.id!r} arrives with {spec.arrives}",
            details={"property": spec.property_kind, "method": spec.id, "arrives": spec.arrives},
        )


def catalogue(material_kind: str, property_kind: str | None = None) -> list[MethodSpec]:
    """``material.methods``: every method a material kind's properties take, in table order."""
    if material_kind not in MATERIAL_KINDS:
        raise InvalidMethod(
            f"material kind must be one of {', '.join(MATERIAL_KINDS)}; got {material_kind!r}"
        )
    kinds = KIND_PROPERTIES[material_kind]
    if property_kind is not None:
        if property_kind not in PROPERTY_KINDS:
            raise InvalidMethod(
                f"property must be one of {', '.join(PROPERTY_KINDS)}; got {property_kind!r}"
            )
        kinds = tuple(k for k in kinds if k == property_kind)
    return [m for m in METHODS if m.property_kind in kinds and material_kind in m.material_kinds]
