"""The method catalogue and its evaluators (ADR-0018 decision 3; contract "The method catalogue"):
completeness against the node schema, the defaults, hand-computed values of the formulas, and the
validation that keeps malformed parameters away from the evaluators."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
import pytest

from stokes.errors import ValidationError
from stokes.model import registry
from stokes.physics.materials import catalogue, model
from stokes.physics.materials.errors import InvalidMethod, NeedsMolarMass
from stokes.physics.materials.evaluate import Definition, State, evaluate_all, evaluate_one
from stokes.physics.materials.methods import EVALUATORS, R_UNIVERSAL, collision_integral

R = 8.31446261815324  # J/(mol K), exact
NON_NEWTONIAN = {"shear_power_law", "carreau", "cross", "herschel_bulkley", "casson"}


def run(
    prop: str,
    method: str,
    values: Mapping[str, float] | None = None,
    arrays: Mapping[str, Sequence[float]] | None = None,
    *,
    kind: str = "fluid",
    molar_mass: float = 0.0,
    t_ref: float = 300.0,
    p_ref: float = 101325.0,
    t: Sequence[float] = (300.0,),
    p: float = 101325.0,
    viscosity: Sequence[float] | None = None,
    specific_heat: Sequence[float] | None = None,
) -> np.ndarray:
    definition = Definition(
        prop, method, dict(values or {}), {k: tuple(v) for k, v in (arrays or {}).items()}
    )
    state = State(kind, T_ref=t_ref, p_ref=p_ref, molar_mass=molar_mass)
    grid = np.asarray(t, dtype=np.float64)
    return evaluate_one(
        definition,
        state,
        grid,
        np.full_like(grid, p),
        viscosity=None if viscosity is None else np.asarray(viscosity, dtype=np.float64),
        specific_heat=None
        if specific_heat is None
        else np.asarray(specific_heat, dtype=np.float64),
    )


# ---------------------------------------------------------------- completeness


def test_the_catalogue_and_the_node_schema_agree() -> None:
    assert set(model.METHOD_IDS) == {m.id for m in catalogue.METHODS}
    names = {p.name for p in registry.get("property").params}
    for spec in catalogue.METHODS:
        assert spec.property_kind in model.PROPERTY_KINDS
        assert set(spec.param_names) <= names, spec.id
        assert set(spec.material_kinds) <= set(model.MATERIAL_KINDS)
    keys = [(m.property_kind, m.id) for m in catalogue.METHODS]
    assert len(keys) == len(set(keys))


def test_every_method_has_an_evaluator_or_says_when_it_arrives() -> None:
    for spec in catalogue.METHODS:
        if spec.available:
            assert spec.evaluator in EVALUATORS, spec
            assert callable(spec.evaluate)
        else:
            assert spec.arrives and not spec.evaluator, spec
            with pytest.raises(InvalidMethod):
                _ = spec.evaluate
    unavailable = {m.id for m in catalogue.METHODS if not m.available}
    assert unavailable == NON_NEWTONIAN | {"real_gas_table"}


def test_every_method_names_its_openfoam_selection_and_fluent_method() -> None:
    for spec in catalogue.METHODS:
        assert spec.fluent, spec.id
        for kind in spec.material_kinds:
            assert spec.of14_for(kind), (spec.property_kind, spec.id, kind)
        expected = "strain_rate" if spec.id in NON_NEWTONIAN else "T"
        assert spec.independent == expected
        entry = spec.to_json(spec.material_kinds[0])
        assert entry["property"] == spec.property_kind and entry["id"] == spec.id


def test_every_property_of_every_kind_has_a_valid_default() -> None:
    for kind, props in model.KIND_PROPERTIES.items():
        for prop in props:
            default = catalogue.DEFAULT_METHOD[(prop, kind)]
            spec = catalogue.method(prop, default)
            catalogue.check_choice(spec, kind)
    assert set(model.KIND_PROPERTIES["solid"]) < set(model.KIND_PROPERTIES["fluid"])
    assert [m.id for m in catalogue.catalogue("solid", "density")] == ["constant"]
    assert catalogue.catalogue("solid", "viscosity") == []


def test_lookups_refuse_unknown_and_foreign_methods() -> None:
    with pytest.raises(InvalidMethod) as unknown:
        catalogue.method("density", "sutherland")
    assert unknown.value.code == "materials.invalid_method"
    with pytest.raises(InvalidMethod):
        catalogue.check_choice(catalogue.method("density", "ideal_gas"), "solid")
    with pytest.raises(InvalidMethod, match="a later release"):
        catalogue.check_choice(catalogue.method("viscosity", "carreau"), "fluid")
    with pytest.raises(InvalidMethod):
        catalogue.catalogue("mixture")
    with pytest.raises(InvalidMethod):
        catalogue.catalogue("fluid", "emissivity")


# ---------------------------------------------------------------- density


def test_ideal_gas_and_incompressible_ideal_gas() -> None:
    air = 0.02896546
    rho = run("density", "ideal_gas", molar_mass=air, t=(300.0, 600.0), p=101325.0)
    assert rho == pytest.approx([101325.0 * air / (R * 300.0), 101325.0 * air / (R * 600.0)])
    assert rho[0] == pytest.approx(1.17663, rel=1e-5)
    twice = run("density", "ideal_gas", molar_mass=air, p=202650.0)
    assert twice[0] == pytest.approx(2 * rho[0])
    frozen = run("density", "incompressible_ideal_gas", molar_mass=air, p=202650.0)
    assert frozen[0] == pytest.approx(rho[0])  # p_ref, not the local pressure
    assert R_UNIVERSAL == R


def test_boussinesq_polynomial_and_rational_polynomial() -> None:
    rho = run(
        "density",
        "boussinesq",
        {"value": 1.2, "expansion_coefficient": 3.3e-3},
        t=(300.0, 310.0),
        t_ref=300.0,
    )
    assert rho == pytest.approx([1.2, 1.2 * (1 - 0.033)])
    poly = run("density", "polynomial", arrays={"coefficients": [1000.0, -0.05, 0.003]})
    assert poly[0] == pytest.approx(1000.0 - 15.0 + 270.0)
    # The water example of OpenFOAM's coolingSphere tutorial, C0..C4.
    c = [0.001278, -2.1055e-06, 3.9689e-09, 4.3772e-13, -2.0225e-16]
    t, p = 300.0, 1.0e5
    rational = run("density", "rational_polynomial", arrays={"coefficients": c}, t=(t,), p=p)
    inverse = c[0] + c[1] * t + c[2] * t**2 - c[3] * p - c[4] * p * t
    assert rational[0] == pytest.approx(1.0 / inverse)


def test_piecewise_linear_holds_the_end_values() -> None:
    table = {"table_temperatures": [280.0, 300.0, 320.0], "table_values": [1.0, 2.0, 4.0]}
    values = run("density", "piecewise_linear", arrays=table, t=(270.0, 290.0, 310.0, 330.0))
    assert values == pytest.approx([1.0, 1.5, 3.0, 4.0])


def test_piecewise_polynomial_takes_each_segment_and_extends_the_ends() -> None:
    arrays = {"breakpoints": [250.0, 300.0, 350.0], "coefficients": [1.0, 0.01, 2.0, 0.0]}
    values = run(
        "density",
        "piecewise_polynomial",
        {"degree": 1},
        arrays,
        t=(240.0, 260.0, 300.0, 320.0, 400.0),
    )
    assert values == pytest.approx([3.4, 3.6, 2.0, 2.0, 2.0])


def test_tait_is_the_adiabatic_perfect_fluid_law() -> None:
    rho0, k0, n, p0 = 998.2, 2.2e9, 7.15, 101325.0
    p = 5.0e7
    tait = run(
        "density",
        "compressible_liquid",
        {"value": rho0, "bulk_modulus": k0, "exponent": n},
        p_ref=p0,
        p=p,
    )
    b = k0 / n - p0  # OpenFOAM adiabaticPerfectFluid: rho0 ((p + B) / (p0 + B))^(1/gamma)
    assert tait[0] == pytest.approx(rho0 * ((p + b) / (p0 + b)) ** (1.0 / n), rel=1e-12)
    at_reference = run(
        "density",
        "compressible_liquid",
        {"value": rho0, "bulk_modulus": k0, "exponent": n},
        p_ref=p0,
        p=p0,
    )
    assert at_reference[0] == pytest.approx(rho0)


def test_perfect_fluid() -> None:
    m = 0.018015
    rho = run("density", "perfect_fluid", {"value": 1000.0}, molar_mass=m, p=2.0e5)
    assert rho[0] == pytest.approx(1000.0 + 2.0e5 / (R / m * 300.0))


def test_peng_robinson_solves_its_equation_and_tends_to_the_ideal_gas() -> None:
    m, tc, pc, omega = 0.0160428, 190.564, 4.5992e6, 0.01142  # methane
    params = {
        "critical_temperature": tc,
        "critical_pressure": pc,
        "critical_volume": 6.15e-3,
        "acentric_factor": omega,
    }
    rs = R / m
    for p in (1.0e5, 5.0e6):
        rho = run("density", "peng_robinson", params, molar_mass=m, t=(300.0,), p=p)[0]
        v = 1.0 / rho
        kappa = 0.37464 + 1.54226 * omega - 0.26992 * omega**2
        alpha = (1 + kappa * (1 - math.sqrt(300.0 / tc))) ** 2
        a = 0.45724 * (rs * tc) ** 2 / pc
        b = 0.07780 * rs * tc / pc
        recovered = rs * 300.0 / (v - b) - a * alpha / (v * v + 2 * b * v - b * b)
        assert recovered == pytest.approx(p, rel=1e-9)
    low = run("density", "peng_robinson", params, molar_mass=m, p=1.0e3)[0]
    assert low == pytest.approx(1.0e3 / (rs * 300.0), rel=1e-3)


# ---------------------------------------------------------------- viscosity


def test_sutherland_for_air_at_300_k() -> None:
    mu = run(
        "viscosity",
        "sutherland",
        {"sutherland_coefficient": 1.458e-6, "sutherland_temperature": 110.4},
    )
    assert mu[0] == pytest.approx(1.458e-6 * 300.0**1.5 / 410.4, rel=1e-14)
    assert mu[0] == pytest.approx(1.846e-5, rel=1e-3)


def test_power_law_log_polynomial_andrade_and_wlf() -> None:
    power = run(
        "viscosity",
        "power_law",
        {"value": 1.716e-5, "reference_temperature": 273.0, "exponent": 2.0 / 3.0},
    )
    assert power[0] == pytest.approx(1.716e-5 * (300.0 / 273.0) ** (2.0 / 3.0))
    log = run("viscosity", "log_polynomial", arrays={"coefficients": [-10.0, 0.5]})
    assert log[0] == pytest.approx(math.exp(-10.0) * 300.0**0.5)
    c = [-20.0, 0.01, -1e-5, 500.0, -50.0]
    andrade = run("viscosity", "andrade", arrays={"coefficients": c})
    assert andrade[0] == pytest.approx(
        math.exp(c[0] + c[1] * 300 + c[2] * 9e4 + c[3] / (c[4] + 300))
    )
    wlf = run(
        "viscosity",
        "wlf",
        {"value": 1.0e3, "reference_temperature": 290.0},
        {"coefficients": [17.4, 51.6]},
    )
    assert wlf[0] == pytest.approx(1.0e3 * math.exp(-17.4 * 10.0 / (51.6 + 10.0)))


def test_kinetic_theory_viscosity_with_the_neufeld_integral() -> None:
    m, sigma, eps = 0.0280134, 3.798e-10, 71.4  # nitrogen, Lennard-Jones parameters
    mu = run(
        "viscosity",
        "kinetic_theory",
        {"collision_diameter": sigma, "well_depth_temperature": eps},
        molar_mass=m,
    )[0]
    t_star = 300.0 / eps
    omega = (
        1.16145 * t_star**-0.14874
        + 0.52487 * math.exp(-0.77320 * t_star)
        + 2.16178 * math.exp(-2.43787 * t_star)
    )
    assert float(collision_integral(np.array([t_star]))[0]) == pytest.approx(omega)
    assert mu == pytest.approx(2.67e-6 * math.sqrt(28.0134 * 300.0) / (3.798**2 * omega))
    assert mu == pytest.approx(1.789e-5, rel=0.02)  # NIST: 17.89 uPa s


# ---------------------------------------------------------------- specific heat


# Cantera 3.2.0 air.yaml, species N2 (NASA-7, 300 K to 1000 K to 5000 K), and Cantera's c_p.
N2_LOW = [3.298677, 1.4082404e-03, -3.963222e-06, 5.641515e-09, -2.444854e-12, -1020.8999, 3.950372]
N2_HIGH = [2.92664, 1.4879768e-03, -5.68476e-07, 1.0097038e-10, -6.753351e-15, -922.7977, 5.980528]


def test_janaf_reproduces_cantera_for_nitrogen() -> None:
    cp = run(
        "specific_heat",
        "janaf",
        arrays={"coefficients": N2_LOW + N2_HIGH, "breakpoints": [300.0, 1000.0, 5000.0]},
        molar_mass=0.028014,
        t=(300.0, 1500.0),
    )
    assert cp == pytest.approx([1037.8911357957468, 1242.4266981940143], rel=1e-9)


def test_nasa9_power_and_kinetic_theory_specific_heat() -> None:
    m = 0.039948  # argon
    nasa9 = run(
        "specific_heat",
        "nasa9_polynomial",
        arrays={"coefficients": [0, 0, 2.5, 0, 0, 0, 0, 0, 0], "breakpoints": [200.0, 6000.0]},
        molar_mass=m,
    )
    assert nasa9[0] == pytest.approx(2.5 * R / m)
    kinetic = run("specific_heat", "kinetic_theory", {"degrees_of_freedom": 3}, molar_mass=m)
    assert kinetic[0] == pytest.approx(nasa9[0])
    power = run(
        "specific_heat",
        "power",
        {"value": 900.0, "reference_temperature": 300.0, "exponent": 0.1},
        kind="solid",
        t=(600.0,),
    )
    assert power[0] == pytest.approx(900.0 * 2.0**0.1)


# ---------------------------------------------------------------- conductivity


def test_conductivity_forms() -> None:
    k = run(
        "thermal_conductivity",
        "exponential",
        {"value": 50.0, "reference_temperature": 300.0, "exponent": -0.5},
        kind="solid",
        t=(1200.0,),
    )
    assert k[0] == pytest.approx(25.0)
    aniso = run(
        "thermal_conductivity",
        "anisotropic",
        arrays={"principal_values": [1.0, 2.0, 30.0]},
        kind="solid",
        t=(300.0, 400.0),
    )
    assert aniso.shape == (3, 2) and aniso[:, 1].tolist() == [1.0, 2.0, 30.0]
    m, mu, cp = 0.0289647, 1.85e-5, 1006.0
    kinetic = run(
        "thermal_conductivity",
        "kinetic_theory",
        molar_mass=m,
        viscosity=[mu],
        specific_heat=[cp],
    )
    rs = R / m
    assert kinetic[0] == pytest.approx(15 / 4 * rs * mu * (4 / 15 * cp * m / R + 1 / 3))


def test_kinetic_theory_conductivity_takes_the_materials_own_properties() -> None:
    m = 0.039948
    definitions = {
        "viscosity": Definition(
            "viscosity",
            "sutherland",
            {"sutherland_coefficient": 1.9e-6, "sutherland_temperature": 144.0},
        ),
        "specific_heat": Definition("specific_heat", "kinetic_theory", {"degrees_of_freedom": 3}),
        "thermal_conductivity": Definition("thermal_conductivity", "kinetic_theory"),
    }
    t = np.array([300.0, 600.0])
    values = evaluate_all(
        definitions, State("fluid", molar_mass=m), ["thermal_conductivity"], t, np.full_like(t, 1e5)
    )
    mu = 1.9e-6 * t**1.5 / (t + 144.0)
    cp = 2.5 * R / m
    expected = 15 / 4 * (R / m) * mu * (4 / 15 * cp * m / R + 1 / 3)
    assert values["thermal_conductivity"] == pytest.approx(expected)


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("prop", "method", "values", "arrays"),
    [
        ("density", "polynomial", {}, {"coefficients": [1.0] * 9}),
        ("density", "polynomial", {}, {"coefficients": []}),
        ("density", "rational_polynomial", {}, {"coefficients": [1.0, 2.0, 3.0]}),
        ("density", "constant", {"value": 0.0}, {}),
        ("density", "compressible_liquid", {"value": 1.0, "bulk_modulus": 1e9}, {}),
        (
            "density",
            "piecewise_linear",
            {},
            {"table_temperatures": [300.0, 290.0], "table_values": [1.0, 2.0]},
        ),
        (
            "density",
            "piecewise_linear",
            {},
            {"table_temperatures": [290.0, 300.0], "table_values": [1.0]},
        ),
        ("specific_heat", "polynomial", {}, {"coefficients": [math.inf]}),
        (
            "density",
            "piecewise_polynomial",
            {"degree": 1},
            {"breakpoints": [250.0, 300.0], "coefficients": [1.0]},
        ),
        ("viscosity", "sutherland", {"sutherland_coefficient": -1.0}, {}),
        ("viscosity", "wlf", {"value": 1.0, "reference_temperature": 300.0}, {"coefficients": []}),
    ],
)
def test_malformed_parameters_are_refused(
    prop: str,
    method: str,
    values: dict[str, float],
    arrays: dict[str, list[float]],
) -> None:
    with pytest.raises(ValidationError):
        run(prop, method, values, arrays)


def test_shape_rules_of_janaf_nasa9_and_anisotropic() -> None:
    with pytest.raises(ValidationError, match="14 coefficients"):
        run(
            "specific_heat",
            "janaf",
            arrays={"coefficients": N2_LOW, "breakpoints": [300.0, 1000.0, 5000.0]},
            molar_mass=0.028,
        )
    with pytest.raises(ValidationError, match="T_low, T_common and T_high"):
        run(
            "specific_heat",
            "janaf",
            arrays={"coefficients": N2_LOW + N2_HIGH, "breakpoints": [300.0, 5000.0]},
            molar_mass=0.028,
        )
    with pytest.raises(ValidationError, match="9 coefficients per segment"):
        run(
            "specific_heat",
            "nasa9_polynomial",
            arrays={"coefficients": [0.0] * 9, "breakpoints": [200.0, 1000.0, 6000.0]},
            molar_mass=0.028,
        )
    with pytest.raises(ValidationError, match="three positive"):
        run(
            "thermal_conductivity",
            "anisotropic",
            arrays={"principal_values": [1.0, 2.0]},
            kind="solid",
        )


def test_methods_that_need_the_molar_mass_refuse_zero() -> None:
    for prop, method, values in (
        ("density", "ideal_gas", {}),
        ("density", "incompressible_ideal_gas", {}),
        ("density", "perfect_fluid", {"value": 1.0}),
        ("specific_heat", "kinetic_theory", {"degrees_of_freedom": 3}),
    ):
        with pytest.raises(NeedsMolarMass) as caught:
            run(prop, method, values)
        assert caught.value.code == "materials.needs_molar_mass"


def test_a_method_foreign_to_the_material_kind_is_refused() -> None:
    with pytest.raises(InvalidMethod):
        run("density", "ideal_gas", kind="solid", molar_mass=0.027)
    with pytest.raises(InvalidMethod):
        run("viscosity", "carreau", {"value": 1.0})
