# Materials

**Format 1** · ADR-0018 · node types: `stokes/physics/materials/model.py` · catalogue:
`stokes/physics/materials/catalogue.py` · providers: `stokes/physics/materials/providers/` ·
fits: `stokes/physics/materials/fit.py` · checks: `stokes/physics/materials/checks.py` ·
commands: `stokes/api/commands/material.py` · compile: `stokes/compile/of14/materials.py` ·
sources: `docs/research/materials/` (Fluent `[fu pN]`, OpenFOAM, CoolProp/Cantera, PyFluent notes)

A material is a node under `/physics/materials` whose child `property` nodes each carry one
*method* (constant, polynomial, ideal gas, Sutherland, ...) with that method's parameters. A
library material adds where its numbers came from (provider, fluid, composition, state) and its
property methods are *fitted* from the library over a temperature range at a reference pressure;
the library is the source of truth and the fitted coefficients are a derived, regenerable
artefact kept in the project so that compiling never needs the library. Regions reference
materials by id. This document is the contract for the Python core, the compiler and the
Materials panel.

All values in parameters, events and results are **SI** (`QuantityValue` for quantities, SI
numbers in arrays); results name their dimension and the project's display unit so the front end
converts for display. Temperature is kelvin everywhere inside the model.

## Nodes

```
/physics/materials            group, part of the skeleton (cannot be removed or renamed)
  <material>                  material
    density                   property (kind density)
    viscosity                 property (fluid only)
    specific_heat             property
    thermal_conductivity      property
    molar_mass                property (fluid required; solid optional)
    formation_enthalpy        property (fluid only; 0 unless combustion)
```

A material is created with one `property` child per kind its `kind` takes (table below), in this
order, named after the property kind, with ids derived from the material's; the `material.*`
commands own them, so `physics.materials.water.viscosity.value` is a stable expression reference
(the resolver takes the full path from the root; `materials.water...` does not resolve). `node.*`
and `parameter.set` work on every parameter and are undoable. *Slice 1:* `stokes.model` has no
rule yet that stops `node.remove`, `node.rename` or `node.add` on a material's children, or a
`parameter.set` of a property's `kind` or `dimension`; `material.check` reports any such change
as `materials.structure` and undo puts it back. Enforcing it needs an owned-children flag in
`NodeSpec` honoured by `Project.add`, `remove` and `rename` (a model change, decisions log).

### `material`

| Parameter | Kind | Meaning |
|---|---|---|
| `kind` | enum `fluid` \| `solid` | `mixture` is reserved (ADR-0018 slice 6); regions accept only the kind they are (`materials.kind_mismatch`) |
| `source` | enum `user` \| `builtin` \| `library` | who owns the property methods: the user, the built-in catalogue, or a provider fit (`material.refit` regenerates) |
| `provider` | enum `none` \| `builtin` \| `coolprop` \| `coolprop_incomp` \| `cantera` \| `refprop` | which provider evaluates the material; `none` for user materials |
| `fluid` | string | the provider's fluid id: CoolProp `Water`, `R134a`, `Nitrogen&Oxygen`, `R410A.mix`; INCOMP `MEG`, `LiBr`, `DowQ`; Cantera `air.yaml`, `gri30.yaml`, a user YAML path; REFPROP `WATER`, `R32&R125`; built-in `water_liquid`, `aluminium` |
| `composition` | string | fractions for mixtures and solutions: `0.79,0.21` (one per `&`-separated component, in `fluid` order), `0.5` for a solution, Cantera `O2:0.21,N2:0.78,AR:0.01`; empty for pure fluids |
| `basis` | enum `mass` \| `mole` \| `volume` | basis of `composition`: HEOS, REFPROP and Cantera take `mole` (Cantera also `mass`); INCOMP solutions take `mass` or `volume` (never `mole`) |
| `phase` | enum `auto` \| `liquid` \| `gas` \| `supercritical` | phase the provider is asked for; `auto` takes the stable phase at (`T_ref`, `p_ref`) and refuses a range that crosses saturation (`materials.range`); `liquid`/`gas` force the phase (CoolProp `specify_phase`) and allow metastable extrapolation, which the fit report flags |
| `T_ref` | quantity temperature | reference temperature: Boussinesq `T0` (Fluent's operating temperature), power-law `T0`, the temperature at which `methods: constant` reads a library, centre of the default range; default 300 K |
| `p_ref` | quantity pressure | reference (operating) pressure at which library properties are evaluated, the `incompressible_ideal_gas` `pRef`, the Tait `p0`; default 101325 Pa; may be an expression (`p_op`), and the compiler warns when it differs from the case's operating pressure (M3) |
| `T_min`, `T_max` | quantity temperature | the fit and validity range; defaults by `material.add_from_library` (see Fits) |
| `tolerance` | number | maximum relative deviation a fit may have (default 0.01); `material.refit` refuses above it |
| `max_degree` | int | highest polynomial degree a fit may use (default 4, at most 7: OpenFOAM and Fluent take 8 coefficients) |
| `formula`, `cas`, `description` | string | metadata copied from the library or typed |

### `property`

One node type carries the union of every method's parameters; the catalogue (`material.methods`)
says which ones a method uses and the Properties panel and the Materials panel show only those.
Unused parameters keep their defaults and are ignored by evaluation and compilation.

| Parameter | Kind | Dimension | Used by |
|---|---|---|---|
| `kind` | enum | | `density`, `viscosity`, `specific_heat`, `thermal_conductivity`, `molar_mass`, `formation_enthalpy` (`surface_tension`, `emissivity` reserved for v1.5); set at creation, read-only |
| `dimension` | enum | | the dimension of `value` and the tables, fixed by `kind` (table below); read-only; the DYNAMIC dimension of the quantities marked † |
| `method` | enum | | a method id of the catalogue valid for this kind and the material's kind (`materials.invalid_method` otherwise) |
| `value` † | quantity | the property's | `constant`; `boussinesq` ρ₀; `power_law` μ₀; `perfect_fluid` ρ₀; `compressible_liquid` ρ₀; `exponential` κ₀; `power` c₀; `wlf` μ₀; the reference value μ₀ of every non-Newtonian law |
| `reference_temperature` | quantity | temperature | `power_law`, `exponential`, `power`, `wlf`; `boussinesq` and `compressible_liquid` read the material's `T_ref` and `p_ref` |
| `expansion_coefficient` | quantity | thermal_expansion | `boussinesq` β |
| `sutherland_coefficient` | quantity | sutherland_coefficient | `sutherland` A_s (μ = A_s T^1.5 / (T + T_s)); the Fluent three-coefficient form is converted on input: A_s = μ₀ (T₀ + S) / T₀^1.5, T_s = S [fu p1682] |
| `sutherland_temperature` | quantity | temperature | `sutherland` T_s |
| `exponent` | number | | `power_law` n; `exponential` n₀; `power` n₀; `compressible_liquid` n; non-Newtonian n |
| `collision_diameter` | quantity | length | `kinetic_theory` σ (Å in the catalogue, stored in m) |
| `well_depth_temperature` | quantity | temperature | `kinetic_theory` ε/k_B |
| `degrees_of_freedom` | int | | `kinetic_theory` specific heat |
| `coefficients` | array | SI, see method | `polynomial`, `log_polynomial`, `andrade` (5), `rational_polynomial` (5), `wlf` (C₁, C₂), `janaf` (14: low then high), `nasa9_polynomial` (9 per segment), `piecewise_polynomial` (flattened, `degree + 1` per segment); the formula defines each coefficient's unit, so these are the one place SI numbers stand without a quantity |
| `degree` | int | | `piecewise_polynomial`: coefficients per segment minus one |
| `breakpoints` | array | temperature | `piecewise_polynomial` and `nasa9_polynomial` segment bounds (n + 1 values for n segments); `janaf` reads `T_low, T_common, T_high` from it |
| `table_temperatures` | array | temperature | `piecewise_linear` |
| `table_values` | array | the property's (SI; the schema cannot mark an array `DYNAMIC`, so `material.methods` states it) | `piecewise_linear` |
| `critical_temperature`, `critical_pressure` | quantity | temperature, pressure | `peng_robinson` |
| `critical_volume` | quantity | specific_volume | `peng_robinson` (m³/kg; molar in Fluent, converted) |
| `acentric_factor` | number | | `peng_robinson` ω |
| `bulk_modulus` | quantity | pressure | `compressible_liquid` K₀ |
| `principal_values` | array | thermal_conductivity | `anisotropic` κ₁, κ₂, κ₃ along the region's coordinate axes |
| `formation_entropy` | quantity | specific_entropy | `polynomial`, `piecewise_linear`, `janaf` and `nasa9_polynomial` specific heat (OpenFOAM `sf`; Fluent `formation_entropy`) |
| `viscosity_infinite`, `viscosity_min`, `viscosity_max` | quantity | dynamic_viscosity | non-Newtonian laws |
| `consistency` | number | Pa·sⁿ | non-Newtonian `k` |
| `time_constant` | quantity | time | `carreau`, `cross` λ |
| `transition_exponent` | number | | `carreau` a (default 2) |
| `yield_stress` | quantity | pressure | `herschel_bulkley`, `casson` τ₀ |
| `valid_min`, `valid_max` | quantity | temperature | where the method is trusted: set by the fit, or typed; checks compare them with the material's range. `valid_max` 0 means no range is claimed; tables, segments and JANAF breakpoints then bound the method by themselves |

Dimensions added to `stokes.units.dimensions` by ADR-0018: `molar_mass` (kg/mol; displayed
g/mol in every system), `thermal_expansion` (1/K), `specific_energy` (J/kg; BTU/lb),
`specific_entropy` (J/(kg·K)), `specific_volume` (m³/kg; ft³/lb), `sutherland_coefficient`
(kg/(m·s·K^0.5)), `surface_tension` (N/m).

| Property kind | Dimension | Fluid | Solid |
|---|---|---|---|
| `density` | density | required | required (`constant` only, as in Fluent and OpenFOAM) |
| `viscosity` | dynamic_viscosity | required | — |
| `specific_heat` | specific_heat | required | required (written as c_v = c_p for solids) |
| `thermal_conductivity` | thermal_conductivity | required | required |
| `molar_mass` | molar_mass | required (0 means unknown) | optional (OpenFOAM's `molWeight` is written but unused with `rhoConst`) |
| `formation_enthalpy` | specific_energy | required (default 0) | — |

The property node's `meta` carries what the fit produced (written in the fit's undo step,
regenerated by `material.refit`): `fit = {provider, provider_version, fluid, composition, basis,
phase, phase_used, p_ref, T_min, T_max, points, method, degree, max_rel_err, rms_rel_err,
tolerance, forced, generated, reference, state, T_sat?}` (`reference` is the provider's
citation, e.g. CoolProp's `BibTeX-EOS` / `BibTeX-VISCOSITY` keys; `state` the material state the
fit was made at; `phase_used` the candidate order, `gas` or `liquid`; `forced` a form `methods`
chose, which later refits keep); `signature = {params, hash}`, the names and a hash of the
parameters the provider or the catalogue wrote; `missing = {provider, reason}` when the provider
has no model for the property (its value is then a stand-in and `material.check` reports
`materials.property_unavailable` until it is set); and `detached: true` once the user edits the
property by hand (`material.set_method`, or `parameter.set` through the session's listener, in
the same undo step). A property whose parameters no longer hash to its signature counts as
detached too, so an edit made before any `material.*` command ran in the session is seen. A
library property waiting for a fit (never fitted: a background fit pending, failed or
cancelled; or fitted at another state than the material's present provider, fluid, composition,
basis, phase, `T_ref`, `p_ref` or range) has status `stale`; compile refuses a stale material
unless acknowledged, like every other staleness. Undo and redo recompute the marks.

## The method catalogue

`METHODS` in `catalogue.py` is a tuple of `MethodSpec(id, label, property_kind, material_kinds,
params, evaluator, of14, fluent, note, independent, uses, arrives)`, one per (property kind,
method id): the same id is a different formula for different properties (kinetic theory), and the
OF14 selection differs per material kind, so `of14` holds one text per kind. `params` are
`ParamUse(name, rule, required)` with the rules `any`, `positive`, `nonnegative`, `nonzero`;
`uses` names the material-level inputs (`molar_mass`, `T_ref`, `p_ref`, `p`, `viscosity`,
`specific_heat`); `arrives` names the release of a method that cannot be chosen yet (the
non-Newtonian laws: slice 5; `real_gas_table`: v1.5). `MethodSpec.evaluate` resolves the numpy
evaluator lazily from `methods.EVALUATORS`, so the catalogue loads without numpy.
`material.methods` returns it filtered.
`independent` is `T` for every method but the non-Newtonian laws (`strain_rate`); `evaluate`
is a pure numpy function of (T, p, params) used by `material.evaluate`, by the fit as its
candidate forms, by the checks, by the compiler's tabulation and by the `function` engine
(ADR-0016) when a response needs a property. Coefficients multiply powers of T in kelvin.
"OF14" names the Foundation 14 selection (`equationOfState`, `thermo`, `transport`) and its
keys; **(rt)** marks a selection that Foundation 12–14 do not precompile and compile on the
engine host on first use (`allowSystemOperations`, needs the OpenFOAM build tools); "Fluent"
names the Create/Edit Materials method (`[fu p1676]` TUI list for density) and *expr* means the
Fluent route writes the method as a property expression, which Fluent allows for density,
viscosity and conductivity but never for specific heat `[fu p1074]`.

### Density (fluid)

| Method id | Formula / parameters | OF14 | Fluent |
|---|---|---|---|
| `constant` | ρ = value | `rhoConst` (`rho`) | constant |
| `ideal_gas` | ρ = p M / (R T), p absolute; needs `molar_mass` | `perfectGas` (R from `molWeight`) | ideal-gas: ρ = (p_op + p_gauge) M/(R T) [fu p1677] |
| `incompressible_ideal_gas` | ρ = p_ref M / (R T) | `incompressiblePerfectGas` (`pRef`) **(rt)** | incompressible-ideal-gas (p_op) [fu p1676] |
| `boussinesq` | ρ = value (1 − β (T − T_ref)) in buoyancy only | `Boussinesq` (`rho0`, `T0`, `beta`) | boussinesq + `therm_exp_coeff`; T_ref becomes the operating temperature [fu p1433] |
| `polynomial` | ρ = Σ cᵢ Tⁱ, ≤ 8 coefficients | `icoPolynomial` (`rhoCoeffs<8>`) **(rt)**, or a table (see Compilation) | polynomial [fu p1662] |
| `piecewise_linear` | linear interpolation, held at the end values; ≤ 50 points for Fluent | `icoTabulated` (`rho { values ((T rho) ...); }`) in the tabulated set | piecewise-linear [fu p1665] |
| `piecewise_polynomial` | one polynomial per segment (Fluent: ≤ 3 segments × 8) | — tabulated (see Compilation) | piecewise-polynomial [fu p1667] |
| `rational_polynomial` | 1/ρ = C₀ + C₁ T + C₂ T² − C₃ p − C₄ p T | `rPolynomial` (`C` ×5) | *expr* (T and p never together in one Fluent expression: C₃ = C₄ = 0 required [fu p1076]) |
| `perfect_fluid` | ρ = value + p/(R T), R = R_u/M | `perfectFluid` (`rho0`, `R`) **(rt)** | *expr* |
| `compressible_liquid` | Tait: ρ = value (1 + n (p − p_ref)/K₀)^(1/n); `bulk_modulus` K₀, `exponent` n; water: 998.2, 2.2 GPa, 7.15 [fu p1672] | `adiabaticPerfectFluid` (`rho0`, `p0` = p_ref, `gamma` = n, `B` = K₀/n − p_ref: the same law) | compressible-liquid (`reference_pressure`, `reference_density`, `reference_bulk_modulus`, `density_exponent`) |
| `peng_robinson` | cubic EOS in T_c, p_c, V_c, ω [fu p1736] | `PengRobinsonGas` (`Tc`, `Vc`, `Pc`, `omega`) **(rt)** | real-gas-peng-robinson (plus `critical_*`, `acentric_factor`) |
| `real_gas_table` | ρ(p, T) table from the provider (v1.5) | `rhoTabulated` + `hTabulated` + `tabulated` (the (p, T) set) | real-gas-nist (`nist_fluid` = CoolProp's `REFPROP_name`) or an RGP table |

Other Fluent density methods (Redlich-Kwong, Soave-RK, Aungier-RK, real-gas-nist-mixture,
premixed-combustion, user-defined) have no catalogue method; the ESI/Fluent dialects may add
them later.

### Viscosity (fluid)

| Method id | Formula / parameters | OF14 | Fluent |
|---|---|---|---|
| `constant` | μ = value | `const` (`mu`, and `kappa` from the conductivity: `const` takes exactly one of `Pr` or `kappa`) | constant |
| `sutherland` | μ = A_s T^1.5 / (T + T_s); air 1.458e-6, 110.4 K | `sutherland` (`As`, `Ts`); κ is **derived** (κ = μ c_v (1.32 + 1.77 R/c_v)), the material's conductivity is not written (`materials.conductivity_derived`) | sutherland, two-coefficient (`c1`, `c2`) [fu p1682] |
| `power_law` | μ = value (T / T_ref)ⁿ; air 1.716e-5, 273 K, 2/3 | — fitted or tabulated | power-law three-coefficient [fu p1683] |
| `polynomial` | μ = Σ cᵢ Tⁱ | `polynomial` (`muCoeffs<8>`, `kappaCoeffs<8>`: the conductivity is written in the same form) **(rt)** | polynomial |
| `log_polynomial` | ln μ = Σ cᵢ (ln T)ⁱ | `logPolynomial` (`muLogCoeffs<8>`, `kappaLogCoeffs<8>`, paired) **(rt)** | *expr* |
| `andrade` | ln μ = c₀ + c₁ T + c₂ T² + c₃/(c₄ + T) | `Andrade` (`muCoeffs`, `kappaCoeffs`, 5 each, paired) **(rt)** | *expr* |
| `wlf` | ln(μ/value) = −C₁ (T − T_ref)/(C₂ + T − T_ref) | `WLF` (`mu0`, `Tr`, `C1`, `C2`, `Pr` from the conductivity at T_ref) **(rt)** | *expr* |
| `piecewise_linear` | table | `icoTabulated` transport (`mu`, `kappa` tables, paired) in the tabulated set | piecewise-linear |
| `piecewise_polynomial` | per segment | — tabulated | piecewise-polynomial |
| `kinetic_theory` | Chapman–Enskog: μ = 2.67e-6 √(M T)/(σ² Ω_μ), Neufeld collision integral; needs `ideal_gas` density [fu p1684] | — fitted (Stokes evaluates it) | kinetic-theory (`lennard_jones_length`, `lennard_jones_energy`) |
| `shear_power_law` | μ = k γ̇ⁿ⁻¹ clamped to [μ_min, μ_max] | `generalisedNewtonian` / `powerLaw` (`k`, `n`, `nuMin`, `nuMax`; kinematic, constant ρ required) | non-newtonian-power-law |
| `carreau` | μ = μ_∞ + (μ₀ − μ_∞)(1 + (λ γ̇)^a)^((n−1)/a) | `BirdCarreau` (`nuInf`, `k` = λ, `n`, `a`) | carreau |
| `cross` | μ = μ_∞ + (μ₀ − μ_∞)/(1 + (λ γ̇)ⁿ) | `CrossPowerLaw` (`nuInf`, `m` = λ, `n`) | cross |
| `herschel_bulkley` | μ = min(μ₀, τ₀/γ̇ + k γ̇ⁿ⁻¹) | `HerschelBulkley` (`tau0`, `k` in m²·s^(n−2) for 13 and 14, `n`) | herschel-bulkley |
| `casson` | √μ = √(τ₀/γ̇) + √m, clamped to [μ_min, μ_max] | `Casson` (`m`, `tau0`, `nuMin`, `nuMax`) | — |

The non-Newtonian laws evaluate against strain rate, are laminar-only in Fluent [fu p1686],
compile to `constant/momentumTransport` (`laminar { model generalisedNewtonian; viscosityModel
<name>; ... }` with ν₀ = μ₀/ρ as the `nu` of `physicalProperties`) for `of14.incompressibleFluid`,
and arrive in slice 5.

### Specific heat

| Method id | Formula / parameters | OF14 fluid | OF14 solid | Fluent |
|---|---|---|---|---|
| `constant` | c_p = value (`formation_enthalpy` as `hf`) | `hConst` (`Cp`, `hf`) or `eConst` (`Cv`) when the energy variable is internal energy | `eConst` (`Cv` = c_p, `hf`) | constant |
| `polynomial` | c_p = Σ cᵢ Tⁱ | `hPolynomial` (`CpCoeffs<8>`, `hf`, `sf`) **(rt)** | `ePolynomial` (`CvCoeffs<8>`, `hf`, `sf`) | polynomial |
| `janaf` | NASA-7, two ranges: c_p/R = a₁ + a₂T + a₃T² + a₄T³ + a₅T⁴, a₆ and a₇ fix h and s; `coefficients` = 7 low + 7 high; `breakpoints` = T_low, T_common, T_high | `janaf` (`Tlow`, `Thigh`, `Tcommon`, `lowCpCoeffs`, `highCpCoeffs`) | — | piecewise-polynomial, two ranges, five coefficients each scaled by R/M (what Fluent's database species use [fu p2441]) |
| `nasa9_polynomial` | NASA-9 per segment (≤ 8 segments) | — fitted to `janaf` | — | nasa-9-piecewise-polynomial [fu p1668] |
| `power` | c_p = value (T/T_ref)^n₀ | `hPower` (`c0`, `n0`, `Tref`, `hf`; `C0` in 12 and 13) **(rt)** | `ePower` | polynomial (refit) |
| `piecewise_linear` | table | `hIcoTabulated` (`Cp { values ((T Cp) ...); }`, `hf`, `sf`) in the tabulated set | `eIcoTabulated` (`Cv` table) | piecewise-linear |
| `piecewise_polynomial` | per segment | — tabulated, or `janaf` when it is a JANAF pair | — | piecewise-polynomial |
| `kinetic_theory` | c_p = ½ (R/M)(f + 2) with `degrees_of_freedom` [fu p1715] | written as `hConst` | — | kinetic-theory |

### Thermal conductivity

| Method id | Formula / parameters | OF14 fluid | OF14 solid | Fluent |
|---|---|---|---|---|
| `constant` | κ = value | `kappa` in `const`; ignored with `sutherland` (Eucken); a degree-0 polynomial or a table when paired | `constIsoSolid` (`kappa`) | constant |
| `anisotropic` | κ₁, κ₂, κ₃ along the region's coordinate axes (`principal_values`) | — | `constAnisoSolid` (`kappa (k1 k2 k3)`) with the solid `thermophysicalTransport` `anisotropic { coordinateSystem }` | orthotropic (three values, two direction vectors) [fu p1696] |
| `polynomial` | κ = Σ cᵢ Tⁱ | `polynomial` (paired with μ) **(rt)** | `polynomialSolid` (`kappaCoeffs<8>`) | polynomial |
| `log_polynomial` | ln κ = Σ cᵢ (ln T)ⁱ | `logPolynomial` (paired) **(rt)** | — | *expr* |
| `exponential` | κ = κ₀ (T / T_ref)^n₀ | — | `exponentialSolid` (`kappa0`, `n0`, `Tref`) | *expr* |
| `piecewise_linear` | table | `icoTabulated` (paired) in the tabulated set | `tabulatedSolid` (`kappa { values ((T k) ...); }`) | piecewise-linear |
| `piecewise_polynomial` | per segment | — tabulated | — | piecewise-polynomial |
| `kinetic_theory` | κ = (15/4)(R/M) μ [(4/15)(c_p M/R) + 1/3] [fu p1692] | — fitted | — | kinetic-theory |

`cyl-orthotropic`, `biaxial` and matrix (`anisotropic`, `principal-axes-values`) conductivities
are Fluent-only and arrive with the multi-region work (M5) as coordinate-system choices of the
region, not as material methods.

### Molar mass and formation enthalpy

`molar_mass` and `formation_enthalpy` take `constant` only (`specie { molWeight }` in kg/kmol
and `hf` in J/kg; Fluent `molecular_weight` and `formation_enthalpy`). A library fills them
(CoolProp `molar_mass` in kg/mol, Cantera `mean_molecular_weight` in kg/kmol); a user fluid
leaves `molar_mass` at 0 until typed, and every method that needs it refuses with
`materials.needs_molar_mass`.

### Defaults of `material.create`

| Kind, template | Record | density | viscosity | specific_heat | thermal_conductivity | molar_mass | range |
|---|---|---|---|---|---|---|---|
| `fluid`, `liquid` | `water_liquid` | constant 996.29 kg/m³ | constant 8.5407e-4 Pa·s | constant 4180.64 J/(kg·K) | constant 0.60869 W/(m·K) | 18.015 g/mol | 275–370 K |
| `fluid`, `gas` | `air` | `ideal_gas` | constant 1.8513e-5 Pa·s | constant 1007.04 J/(kg·K) | constant 0.026350 W/(m·K) | 28.965 g/mol | 200–1000 K |
| `solid` | `aluminium` | constant 2699 kg/m³ | — | constant 898.65 J/(kg·K) | constant 237 W/(m·K) | 26.98 g/mol | 273.15–373.15 K |

Decisions log F6 (a): a material built from scratch takes the app's own library, so each
template is made from a built-in record (`authoring.TEMPLATE_RECORDS`): every property is the
record's value at its `T_ref` (300 K), held constant (F6 (b)), except a gas's density, which keeps
the record's ideal-gas law; the material's range is the record's and its `meta.template` names
the record. Nothing comes from Fluent's materials database (slice 1 used Fluent's
`water-liquid`, `air` and `aluminum` entries).

## Library materials

### Providers

| `provider` | Backing | What it offers | Availability |
|---|---|---|---|
| `builtin` | `providers/builtin.py` + `data/materials.json` | a referenced catalogue: common liquids and gases with polynomial fits over stated ranges, common solids (metals, alloys, insulators, glass, plastics) with constant or tabulated properties; every record names its source: fluids are generated from CoolProp (MIT) at stated reference states and fitted; solids use widely published values cited to a public source (e.g. NIST) and cross-checked against a second public source. Nothing is copied from textbook or handbook tables (decisions log F5 (c)) | always |
| `coolprop` | CoolProp `HEOS` (`AbstractState("HEOS", fluid)`) | 136 pure and pseudo-pure fluids (CoolProp 8.0.0), 147 predefined mixtures (`R410A.mix`), ad-hoc mixtures (`Nitrogen&Oxygen` with mole fractions); ρ, μ, κ, c_p, c_v, h, s, speed of sound, Prandtl, β, molar mass, limits, phase; viscosity for 66 fluids and conductivity for 63 (70 fluids have neither) | the optional `materials` extra; absence raises `materials.provider_missing` |
| `coolprop_incomp` | CoolProp `INCOMP` | 74 incompressible liquids and 52 solutions (`MEG`, `MPG`, `LiBr`, `MITSW`) by mass or volume fraction within `fraction_min`..`fraction_max`: ρ, c_p, κ, μ, h, s, `T_freeze`; ρ, c_p, κ, μ do not depend on pressure; no speed of sound, no molar mass; placeholder 1 Pa·s / 0 W/(m·K) where a correlation is missing (turned into `missing`) | with CoolProp |
| `cantera` | Cantera `Solution(fluid)` with `TPX`/`TPY` and `mixture-averaged` transport | ideal-gas mixtures from a YAML mechanism (`air.yaml`, `gri30.yaml`, user files) at a composition: ρ, c_p, c_v, μ, κ, M, h, s, speed of sound; NASA-7 coefficients exported as `janaf` (`species.thermo.coeffs` = `[T_mid, high×7, low×7]`; a mixture's coefficients are **mole**-fraction weighted, with the ideal entropy of mixing added to a7, when every species present shares `T_mid`: c_p per kilogram is (R/M) Σ x_k a_k(T), so the export reproduces Cantera's c_p to rounding; the range is where every species present has thermo data, and evaluation outside it raises `materials.range`) | the optional `materials` extra |
| `refprop` | CoolProp `REFPROP` backend | the user's NIST REFPROP 10 (never shipped): every `coolprop` property for REFPROP's fluids and mixtures, including transport for fluids CoolProp lacks (Acetone, Ethylene, Neon, R113, Xenon, ...) | only when REFPROP is found (below) |

REFPROP detection (`providers/refprop.py`), in this order: the settings path
(`material.providers(refprop_path=...)` sets it for the process until a settings store exists);
the `COOLPROP_REFPROP_ROOT` environment variable; NIST's own `RPPREFIX` variable (CoolProp does
not read it, so Stokes passes it on; Windows also spells it `RPprefix`); the folders on `PATH`.
A candidate counts only when it holds the library (`REFPRP64.DLL`, `librefprop.so`,
`librefprop.dylib`). The materials worker then sets both `COOLPROP_REFPROP_ROOT` (in its own
environment, because CoolProp gives that variable precedence over its configuration) and
`CoolProp.CoolProp.set_config_string(CoolProp.CoolProp.ALTERNATIVE_REFPROP_PATH, folder)` (CoolProp
8 takes the configuration key as an enum, not a string), and confirms the installation by
constructing `AbstractState("REFPROP", "WATER")`; the version is read with
`get_global_param_string("REFPROP_version")`. Measured on CoolProp 8.0.0: REFPROP loads once per
process and a failed load is remembered (a later path does not help), and CoolProp's loader
prints its failure to standard output; so nothing calls REFPROP before a library file was found,
and a new settings path restarts the worker. `materials.refprop_missing` carries the reason (what
was searched, or why the library would not load). REFPROP is not re-entrant: one lock
serialises every call (the worker also serves one request at a time). Detection runs once per
worker process on first use and is reported by `material.providers` (`path`, `source`,
`version`); nothing of REFPROP is ever bundled, and a packaging test asserts that no shipped file
matches `REFPRP*.DLL`, `librefprop*`, `*.FLD`, `*.MIX` or `*.BNC`.

Provider interface (`providers/base.py`), one class per provider, imported lazily inside
`providers/__init__.py:get_provider(name)`:

```
class Provider(Protocol):
    name: str
    def available(self) -> Availability            # installed, version, reason when not
    def search(self, query, kind) -> list[LibraryRecord]
    def info(self, fluid, composition, basis, *, T_ref, p_ref, phase) -> LibraryInfo
    def evaluate(self, fluid, composition, basis, phase, T: ndarray, p: float) -> PropertyGrid
```

The library providers are `providers/remote.py:RemoteProvider` proxies: the API process never
imports CoolProp or Cantera; `coolprop.py`, `cantera.py` and `refprop.py` run inside the materials
worker process (`worker.py`, `worker_main.py`; see "Evaluation and caching"). A provider's
search index (every fluid with its limits and transport coverage) is built once by the worker
and cached in memory and under `<STOKES_HOME or ~/.stokes>/cache/materials`, keyed by the
library's version (REFPROP: also its folder and version); the first REFPROP index loads each of
its fluids (about 5 s here). Record `extra` fields: CoolProp's `refprop_name`; an INCOMP
solution's `fraction_min`, `fraction_max` and native `basis`; a Cantera mechanism's `species`
and `example_composition`.

`PropertyGrid` holds one SI array per property kind the provider could compute plus a `missing`
list. Evaluation never raises for a missing property; it raises `materials.range` when (T, p) is
outside the provider's limits (CoolProp: below the melting line, above `Tmax`, within 1e-4 % of
saturation) or the phase is not the one asked for.

### The library record

`material.library_search` returns records; `material.add_from_library` takes one plus choices:

```json
{"provider": "coolprop", "fluid": "Water", "name": "Water", "formula": "H2O", "cas": "7732-18-5",
 "kind": "fluid", "aliases": ["water", "H2O", "R718"], "T_min": 273.16, "T_max": 2000.0,
 "p_max": 1.0e9, "T_critical": 647.096, "p_critical": 2.2064e7, "molar_mass": 0.018015268,
 "properties": {"density": true, "viscosity": true, "thermal_conductivity": true, "specific_heat": true},
 "composition": null, "basis": null, "reference": {"eos": "Wagner-JPCRD-2002", "viscosity": "Huber-JPCRD-2009"}}
```

The search ranks exact names and aliases first, then CAS and formula matches, then substrings;
a pseudo-pure fluid (`Air`) ranks above the mixture of the same name (`Air.mix`, whose mixture
transport is approximate). The material node stores `provider`, `fluid`, `composition`, `basis`,
`phase`, `T_ref`, `p_ref`, `T_min`, `T_max`, `tolerance`, `max_degree`, `formula`, `cas`,
`description`; the properties store the fitted methods; `meta.fit` on each property records the
provenance above. Nothing else is kept: the library is re-queried on `material.refit`.

### Fits

`fit.py` turns provider grids into catalogue methods.

| Rule | Value |
|---|---|
| Grid | `points` = 101 temperatures uniform in [`T_min`, `T_max`] at `p_ref`; verification grid: 201 points at half the step, so a fit is checked at its nodes, between them and at both ends of the range |
| Default range (`material.add_from_library`) | liquids: [max(T_min_provider + 1 K, T_ref − 30 K), min(T_sat(p_ref) − 0.5 K, T_ref + 70 K)]; gases: [max(T_sat(p_ref) + 0.5 K, T_ref − 50 K), T_ref + 450 K] clipped to the provider's limits; INCOMP: [max(T_freeze + 1 K, T_min), T_max] clipped to T_ref ± 60 K; solids and the built-in catalogue: the record's range. The user widens or narrows it in the state card |
| `constant` | the library's value at `T_ref` (the built-in records interpolated it from their 101 nodes, about 1e-6 away) |
| Least squares | polynomials: relative residuals (weights 1/y) on the scaled domain; `rational_polynomial`: 1/ρ weighted by ρ; `log_polynomial`: ln μ in ln T, unweighted; `sutherland`: Gauss-Newton on the relative residuals from the linearised law; JANAF: c_p with weights 1/y, scaled by M/R. These reproduce the eleven built-in fluid records (`tests/test_materials_regenerate.py`) |
| Candidate forms, in order; the first within `tolerance` wins | **density** gas: `ideal_gas` (library ρ against p M/(R T)), then `polynomial` 1..`max_degree`; liquid: `constant`, `rational_polynomial` (1/ρ quadratic in T, C₃ = C₄ = 0), `polynomial` 1..`max_degree`; **viscosity** gas: `sutherland` (least squares in A_s, T_s), `log_polynomial` 2..`max_degree`, `polynomial` 2..`max_degree`; liquid: `log_polynomial` 2..`max_degree`, `polynomial` 3..`max_degree`; **specific heat** gas: `constant`, `janaf` (one NASA-7 range of degree 1..4 repeated for both ranges, breakpoints `T_min`, the midpoint, `T_max`, a₆ and a₇ from `formation_enthalpy` and `formation_entropy` at 298.15 K; with Cantera, the mechanism's own NASA-7 coefficients instead of a fit), `polynomial` 1..`max_degree`; liquid: `constant`, `polynomial` 1..`max_degree`; **conductivity**: `constant`, `polynomial` 1..`max_degree`; **solids** (built-in tables): `constant`, `polynomial` 1..`max_degree`, `piecewise_linear` |
| Error measure | the largest absolute relative deviation, max abs(fit − library) / abs(library), on the verification grid (`max_rel_err`); `rms_rel_err` reported too |
| Refusal | nothing within `tolerance`: `materials.fit_tolerance` with the best form, its error and the advice (narrow the range, raise `max_degree`, or choose `piecewise_linear`, which is exact at the 101 nodes and always accepted) |
| Numerics | `numpy.polynomial.Polynomial.fit` on the scaled domain, converted to raw-T coefficients; the raw coefficients are re-evaluated in float64 on the verification grid and must reproduce the scaled fit within 1e-9 relative, otherwise that degree is refused as ill-conditioned and the result (or the refusal's best form) has a lower degree (this guards the ill-conditioned monomial basis in kelvin); every candidate is then judged by the catalogue's own evaluator with the coefficients as stored; coefficients are written with `repr` (17 significant digits) everywhere, dictionaries included |
| `methods` override | `material.add_from_library(methods={"density": "constant", ...})` or `material.refit(methods=...)` forces a form (density: `constant`, `ideal_gas`, `rational_polynomial`, `polynomial`, `piecewise_linear`; viscosity: `constant`, `sutherland`, `log_polynomial`, `polynomial`, `piecewise_linear`; specific heat: `constant`, `janaf`, `polynomial`, `piecewise_linear`; conductivity: `constant`, `polynomial`, `piecewise_linear`): `constant` takes the value at T_ref and `piecewise_linear` the 101 nodes, whatever their deviation; the others must still meet the tolerance (degrees ascending); `janaf` with Cantera takes the mechanism's NASA-7 coefficients. A forced form is recorded (`meta.fit.forced`) and kept by later refits |
| Measured on CoolProp 8.0.0 (2026-10-03 probe) | water 280–370 K at 1 atm: ρ cubic 1.3e-4, quadratic 6.2e-4; c_p cubic 6.7e-4; κ cubic 7.4e-4; μ cubic 6.0e-2 (fails), μ `log_polynomial` cubic 3.3e-3, μ quintic 5.1e-3. Air 250–1000 K: ideal gas 9.5e-4; μ `log_polynomial` cubic 2.3e-4; κ cubic 3.1e-3; c_p cubic 9.6e-4; μ Sutherland with the handbook constants 4.1e-2. Hence the 1 % default and the log-polynomial before the polynomial for viscosity |
| Cost | HEOS pure fluid 25–150 µs per state (`AbstractState`, ρ μ κ c_p; water 75 µs, air 21 µs measured), so a four-property fit on the 201 points takes about 40 ms with a warm worker and runs inline; HEOS mixtures 50–110 ms per state, so the same fit takes 10–20 s and runs in the background on the server with `material.fit_progress` events while the properties are `stale`; REFPROP: water 35 µs, R32/R125 0.17 ms per state; Cantera `air.yaml` 0.25–0.55 ms per state |

### Evaluation and caching

`evaluate.py` serves plots, checks, the compiler's tabulation and the function engine: `method`
evaluation is the catalogue's pure function; `library` evaluation goes through the provider with
an LRU cache per session (`service.py`) keyed by (provider, fluid, composition, basis, phase, p,
hash of the T grid), 64 entries.

**The worker is a process.** Measured on CoolProp 8.0.0 (2026-10-04): its nanobind wrapper holds
the GIL inside every call (a pure-Python loop on the main thread ran at 72 % of its speed beside
a thread updating water states, and a 1 ms sleep loop saw 106 ms gaps while a thread flashed a
HEOS mixture), and importing CoolProp holds it for about 3 s while the fluid library loads (a
3.1 s gap in that loop). A thread cannot keep the API responsive, so every CoolProp, Cantera and
REFPROP call runs in one spawned worker process (`worker.py` parent side, `worker_main.py` child
side, on the pattern of `stokes.geometry.worker`: JSON frames, nothing pickled after spawn, every
reply length-limited and checked). One worker serves the whole process, starts on first use (or
ahead with `material.providers(wait=false)`), and stays warm: spawning it and importing CoolProp
and Cantera took 5–10 s on the development laptop (CoolProp's import alone 3.8–5 s there).
Evaluations travel in chunks of about 0.2 s, so a long one reports progress and cancels between
chunks; a request that does not answer within its timeout kills the worker, and the next request
starts a new one.

A fit runs inside its command unless `wait` is false, or the command runs on the server's event
loop and the fit is estimated above 0.2 s (HEOS mixtures): then it runs on a daemon thread, the
properties are `stale`, `material.fit_progress` follows at most ten times a second, and the
result lands where commands run (the server's event loop, captured when the command ran) as its
own undo step, "fit <material> from <provider>". A material whose state changed meanwhile, or
that is gone, drops the result with `material.fit_failed` (`materials.stale_fit`);
`material.cancel_fit` stops a fit between chunks (`materials.cancelled`). The journal records a
background add as `wait: true` with its resolved range, so a replay is deterministic. CoolProp's tabular backends (`BICUBIC&HEOS`: 11–39 s
and 16 MB per fluid to build, 1–2 µs per state after, wrong-branch errors near saturation) are
not used in v1; they and `IF97::Water` are the route for (p, T) tables in v1.5.

## Checks

`checks.py` returns findings `{code, severity (error|warning|info), material, property?,
message, suggestion?}`; `material.check` runs them for one material against an engine (or
against every engine the project can use) and the compiler runs them for every referenced
material and refuses on any `error`.

| Code | Severity | Condition |
|---|---|---|
| `materials.needs_molar_mass` | error | `ideal_gas`, `incompressible_ideal_gas`, `perfect_fluid`, `peng_robinson`, `kinetic_theory`, `janaf` or `nasa9_polynomial` (both give c_p/R) with `molar_mass` 0 |
| `materials.energy_required` | error | a temperature-dependent method (anything but `constant`, the non-Newtonian laws and `boussinesq` for buoyancy) with an engine that solves no energy equation (`of14.incompressibleFluid`, `coupled.simple` without its passive energy); Fluent switches the energy equation on by itself [fu p1644], Stokes asks the user to |
| `materials.unsupported_by_engine` | error | the engine cannot represent the method: temperature- or pressure-dependent density or viscosity with `of14.incompressibleFluid`, `coupled.simple` or `coupled.cht` (constant only; the message names the `fluid` engine); `real_gas_table` before v1.5; `casson` with Fluent; `nasa9_polynomial` with OpenFOAM (the message names `material.refit`) |
| `materials.runtime_compiled` | warning | the OpenFOAM thermoType the compiler would write is not precompiled in Foundation 12–14 and `property_tables` is off (or the material is a gas with non-Sutherland transport): the engine compiles it on first run and needs the OpenFOAM build tools (`wmake`) on the engine host; error when the engine probe found none |
| `materials.tabulated` | info | temperature-dependent methods were sampled into the tabulated set (`property_tables` on); the finding gives the table size and the interpolation error at the verification grid |
| `materials.conductivity_derived` | warning | `sutherland` viscosity under OpenFOAM: κ follows the modified Eucken relation, the material's conductivity is not written; the finding quotes both values at `T_ref` |
| `materials.conductivity_paired` | info | a constant κ was written as a degree-0 polynomial (or a table) to pair with a polynomial (tabulated) μ |
| `materials.boussinesq_range` | warning | β (T − T_ref) exceeds 0.1 inside [`T_min`, `T_max`] [fu p1433] |
| `materials.extrapolation` | warning | the method's [`valid_min`, `valid_max`] does not cover the material's [`T_min`, `T_max`]; or a polynomial of degree ≥ 3 is written to OpenFOAM, which extrapolates polynomials without clamping (Fluent documents clamping for piecewise-linear only [fu p1665]) |
| `materials.energy_unused` | info | the energy equation is off: `specific_heat`, `thermal_conductivity` and `formation_enthalpy` are not written |
| `materials.non_newtonian_laminar` | warning | a non-Newtonian law with a turbulence model (Fluent refuses it [fu p1686]; OpenFOAM's `generalisedNewtonian` is a laminar model) |
| `materials.stale_fit` | error | a property is `stale` (refit pending or failed, never fitted, or fitted at another state) |
| `materials.property_unavailable` | error | the provider has no model for the property and it still holds the stand-in value it was created with (`meta.missing`) |
| `materials.structure` | error | a property child is missing or extra, or its `kind` or `dimension` disagrees with its name (slice 1, see Nodes) |
| `materials.range` | error | the material needs 0 < `T_min` < `T_max` and positive `T_ref` and `p_ref` |
| `materials.invalid_method` | error | the stored method is foreign to the property or the material kind, or not available yet (set by `parameter.set`, which does not consult the catalogue) |
| `materials.evaluation` | error | the method's parameters are malformed, an expression of the material or the property failed, or the method gives a non-finite or (for ρ, μ, c_p, κ) non-positive value on [`T_min`, `T_max`] at `p_ref` |
| `materials.pressure_mismatch` | warning | the material's `p_ref` differs from the case operating pressure by more than 1 % (M3, once the operating-conditions node exists); with Fluent every material shares one operating pressure and temperature [fu p1671] |
| `materials.kind_mismatch` | error | a fluid region references a solid material or the reverse |
| `materials.phase_forced` | info | `phase` is `liquid` or `gas` and the range crosses saturation at `p_ref`: values beyond it are metastable extrapolations |

## Compilation (Foundation 14)

`stokes/compile/of14/materials.py` writes `constant/physicalProperties` (per region under
`constant/<region>/` for `foamMultiRun`) from a material and the engine:

| Engine | What is written |
|---|---|
| `of14.incompressibleFluid` | `viscosityModel constant; nu <μ/ρ>;` (14 syntax; 12 and 13 take the dimensioned form); non-Newtonian laws go to `momentumTransport` (slice 5); any other temperature dependence is refused |
| `of14.fluid`, `of14.multiRegion` fluid regions | `thermoType { type heRhoThermo; mixture pureMixture; transport T; thermo H; equationOfState E; specie specie; energy sensibleEnthalpy; }` with `mixture { specie { molWeight }; thermodynamics { ... }; transport { ... }; equationOfState { ... } }`; `energy` becomes `sensibleInternalEnergy` with `eConst`/`eIcoTabulated` |
| `of14.multiRegion` solid regions | `type heSolidThermo; mixture pureMixture; equationOfState rhoConst; energy sensibleInternalEnergy;` with `transport` one of `constIsoSolid`, `constAnisoSolid`, `exponentialSolid`, `polynomialSolid`, `tabulatedSolid` and `thermo` one of `eConst`, `ePolynomial`, `ePower`, `eIcoTabulated` |
| `coupled.simple`, `coupled.cht` | constants only (`nu`, and for the passive energy `rho`, `Cp`, `Pr`) through the engine's dictionary additions (M5) |

Precompiled selections in Foundation 12, 13 and 14 (`rhoFluidThermos.C`, `solidThermos.C`;
everything else is **(rt)**):

| Set | transport | equationOfState | thermo (energy) |
|---|---|---|---|
| gas (20) | `const`, `sutherland` | `perfectGas`, `Boussinesq` | `hConst`, `janaf` (sensibleEnthalpy); `eConst`, `hConst`, `janaf` (sensibleInternalEnergy) |
| liquid (9) | `const` | `rhoConst`, `rPolynomial`, `adiabaticPerfectFluid` | `hConst` (sensibleEnthalpy); `eConst`, `hConst` (sensibleInternalEnergy) |
| tabulated T (2) | `icoTabulated` | `icoTabulated` | `hIcoTabulated` / `eIcoTabulated` |
| tabulated (p, T) (2) | `tabulated` | `rhoTabulated` | `hTabulated` / `eTabulated` (v1.5) |
| solid (20) | the five solid transports | `rhoConst` | `eConst`, `ePolynomial`, `ePower`, `eIcoTabulated` |

Strategy, in order: (1) the material's methods map onto one precompiled set directly (constants;
a gas with `ideal_gas` or `boussinesq` density, `constant` or `sutherland` viscosity, `constant`
or `janaf` specific heat; a liquid with `constant` transport and `rational_polynomial` or
`compressible_liquid` density; `piecewise_linear` everywhere; any solid); (2) otherwise, with the
engine setting `property_tables` on (default), every temperature-dependent property of a liquid
is sampled on 101 points over [`T_min`, `T_max`] into the tabulated T set (`materials.tabulated`),
and a gas keeps `perfectGas` with its transport and thermo written as **(rt)** selections
(`materials.runtime_compiled`); (3) with `property_tables` off, the **(rt)** selections are
written for liquids too. Rules the writer applies: `const` gets `kappa`; `polynomial`,
`logPolynomial`, `Andrade` and the tables carry μ and κ in the same form (a constant κ becomes a
degree-0 polynomial or a flat table, `materials.conductivity_paired`); `sutherland` drops κ
(`materials.conductivity_derived`); `WLF` gets `Pr` from c_p μ/κ at `T_ref`; keys are lower-case
(`hf`, `sf`, `hsRef`); `hPower`/`ePower` write `c0` (14) and `C0` (12, 13); solids write
`Cv` = c_p and `molWeight` from `molar_mass` or `1` with a comment; `molWeight` is in kg/kmol.
Golden files cover every row of the strategy table.

The ESI dialect (v1.5) reuses the selection vocabulary with ESI's spellings and file names
(`thermophysicalProperties`, `transportProperties` with `transportModel`; `Hf`/`Sf` capitalised)
and is verified against the ESI sources then, per ADR-0008. The Fluent route (M7, ADR-0011)
writes PyFluent settings calls: `solver.settings.setup.materials.fluid[name].density = {"option":
"polynomial", "polynomial": {"function_of": "temperature", "coefficients": [...]}}` and the
siblings `piecewise_linear.data_points`, `piecewise_polynomial.range[i]`, `sutherland` (`c1`,
`c2`), `power_law`, `compressible_liquid`, the leaves `molecular_weight`, `therm_exp_coeff`,
`formation_enthalpy`, `formation_entropy`, `lennard_jones_*`, `degrees_of_freedom`,
`critical_*`, `acentric_factor`; methods without a Fluent option become expressions over
`StaticTemperature` (density, viscosity, conductivity only); `boussinesq` sets
`general.operating_conditions.operating_temperature` from `T_ref` and the `incompressible_ideal_gas`
and library pressure set `operating_pressure`; a library material may instead compile to
`real-gas-nist` with `nist_fluid` = CoolProp's `REFPROP_name` when the user chooses the real-gas
route (one NIST fluid per case, no two-phase [fu p1758]). Option strings come from
`option.allowed_values()` at run time and are pinned per PyFluent release (0.42 / Fluent 26.1).

## Commands

`material` accepts a material id, its path (`/physics/materials/water`) or its name;
`property` is a property kind (`density`). Commands that add nodes accept `node_id` (journals)
and return node summaries. Every mutation is one undo step (a `Composite` when it touches
several nodes).

| Command | Parameters | Result |
|---|---|---|
| `material.providers` | `refprop_path` (sets the REFPROP folder; restarts the worker), `wait` = true (false: answer at once and start the worker; `material.providers_changed` follows) | `[{name, available, version, reason, fluids, note}]`; REFPROP adds `path` and `source` or says why it was not found (`available: null` while undetected); `mutates: false`, no project needed |
| `material.library_search` | `query` (name, alias, formula or CAS; empty lists all), `providers` (list; default every available one plus `builtin`), `kind` (fluid \| solid \| any), `limit` = 50 | `{records: [library record], total}`; `mutates: false` |
| `material.library_info` | `provider`, `fluid`, `composition`, `basis`, `phase` = auto, `T_ref` = 300 K, `p_ref` = 101325 Pa (SI numbers) | the full library record plus limits at `p_ref` (saturation temperature, phase at `T_ref`, fraction limits, `T_freeze`) and the provider's references; `mutates: false` |
| `material.methods` | `kind` (material kind), `property` (optional), `engine` (optional, slice 3: marks what that engine refuses or compiles at run time) | the catalogue entries valid for the kind: `[{id, label, property, dimension, material_kinds, params: [{name, kind, dimension, rule, required, doc}], independent, uses, needs_molar_mass, pressure_dependent, of14, fluent, note, available, arrives, engine_status (slice 3)}]`; `mutates: false`, no project |
| `material.create` | `name`, `kind` = fluid, `template` = liquid \| gas \| solid, `description`, `node_id` | material summary plus `properties` {kind: id}; the defaults table above |
| `material.add_from_library` | `provider`, `fluid`, `composition`, `basis`, `name` (default: the record's name made a valid node name), `kind` (from the record), `phase` = auto, `T_ref`, `p_ref`, `T_min`, `T_max`, `tolerance`, `max_degree`, `methods` (optional {property: method}), `wait` = true, `node_id` | material summary, `properties`, `record`, `fit` ({property: {method, degree, max_rel_err, rms_rel_err, tolerance, forced}}) when the fit ran inline, `missing` (properties the provider lacks), `background` and `pending`; with `wait: false` or a slow provider on the server the properties are `stale` and `material.fitted` events follow |
| `material.set_method` | `material`, `property`, `method`, `params` ({name: quantity text \| number \| array}), `valid_min`, `valid_max` | the property summary; parameters the method does not use are refused (`validation`); sets `source: user` for a library property (`detached`) |
| `material.set_state` | `material`, any of `phase`, `T_ref`, `p_ref`, `T_min`, `T_max`, `tolerance`, `max_degree`, `fluid`, `composition`, `basis`, `provider`, `refit` = true | material summary; with `refit` the library properties are fitted again in the same undo step (inline or in the background as above) |
| `material.refit` | `material`, `properties` (list, default every fitted kind not edited by hand; a detached one named here re-attaches), `methods` ({property: method}), `wait` = true | `{material, path, fit: {property: fit record}, missing, warnings, background, pending}`; refuses with `materials.fit_tolerance` and changes nothing; `materials.busy` while a background fit of the material runs |
| `material.cancel_fit` | `material` (optional; default every one) | `{cancelled: [material ids]}`: stops background fits between chunks; their properties stay `stale`; `mutates: false` |
| `material.evaluate` | `material`, `properties` (default all), `T` (list) or `T_min`/`T_max`/`count` = 101, `p` (default `p_ref`), `strain_rate` (list; non-Newtonian, slice 5), `source` = method \| library \| both | `{material, path, name, T, p, source, properties: {kind: {dimension, unit, display_unit, method_id, method: [values] \| null, library: [values] \| null, missing: bool, max_rel_err, components?}}}`: a value that cannot be evaluated is `null` (a gap); the anisotropic conductivity gives three lists and `components: [k1, k2, k3]`; a property whose expression failed raises `materials.evaluation`; `mutates: false` |
| `material.check` | `material`, `engine` (optional engine id, slice 3) | `{material, path, name, findings: [finding], ok}`, errors first; `mutates: false` |
| `material.duplicate` | `material`, `name` | the copy's summary (library provenance kept) |
| `material.export` | `material`, `path` (`.json`, outside the project folder), `format` = stokes \| of14 | `{material, file, format}`; `of14` writes the `physicalProperties` fragment the compiler would write for the `fluid` engine |
| `material.import` | `path` (a Stokes material JSON), `name` | material summary; the record's provider is re-queried if present, else the stored fits are kept with `source: user` |
| `material.save_to_library` | `material`, `name` | stores the material JSON under `~/.stokes/materials/<name>.json`; it appears in `material.library_search` under `provider: user_library` |
| `material.list` | — | `[{name, id, path, kind, source, provider, fluid, status, fitting, detached: [property kinds], regions: [ids]}]`; `regions` holds the regions whose `material` is this one, in tree order (ADR-0022 decision 4.1; `region.assign` and `region.set` assign materials, `docs/engineering/setup-schema.md`); `mutates: false`. A material a region uses is not removed: `node.remove` refuses it (`model.referenced`) |

The CLI mirrors them under `stokes material` (`providers`, `library-search`, `library-info`,
`add`, `create`, `set-state`, `refit`, `evaluate`, `check`, `list`; `export` and `import` with
those commands), JSON output as for `stokes study`; a background fit lands before the project is
saved.

## Events

| Event | Payload | When |
|---|---|---|
| `node.added`, `node.changed`, `node.removed` | as everywhere | material and property nodes; `node.changed` with `param: "status"` for `stale` |
| `material.fitted` | `{material, property, method, max_rel_err, tolerance, ok}` | one property refitted (inline or background) |
| `material.fit_progress` | `{material, done, total}` | background evaluation, at most 10 Hz |
| `material.fit_failed` | `{material, property, code, message}` | a background fit ended in an error; the property stays `stale` |
| `material.providers_changed` | `{providers: [...]}` | a provider was configured or detected (REFPROP path set) |

Order for a background fit: `node.changed` (status stale) per property, `material.fit_progress`
..., `material.fitted` per property, `node.changed` (status ok) per property.

## Errors

| Code | Meaning |
|---|---|
| `materials.provider_missing` | the provider's package is not installed; the message names `pip install 'stokes-cfd[materials]'` |
| `materials.refprop_missing` | REFPROP was not found (settings path, `COOLPROP_REFPROP_ROOT`, `RPPREFIX`, `PATH`) or failed to load |
| `materials.fluid_unknown` | the provider has no such fluid, composition or basis (INCOMP: the fraction is outside `fraction_min`..`fraction_max`) |
| `materials.property_unavailable` | the provider has no model for that property of that fluid (e.g. no viscosity correlation) |
| `materials.range` | T or p outside the provider's limits, or the range crosses saturation with `phase: auto` |
| `materials.invalid_method` | the method does not apply to the property kind or the material kind |
| `materials.fit_tolerance` | no candidate form met the tolerance |
| `materials.busy` | a background fit is running for that material |
| `materials.kind_mismatch` | `region.create`, `region.assign` or `region.set` would put a fluid material on a solid region or the reverse (`KindMismatch`, ADR-0022 decision 4.2); compile raises it too |
| `materials.unsupported_by_engine`, `materials.runtime_compiled`, `materials.energy_required`, `materials.needs_molar_mass`, `materials.stale_fit` | the check codes above when raised by compile |
| `materials.evaluation` | a method cannot be evaluated: a failed expression, or no physical root (Peng-Robinson) |
| plus `validation`, `units.mismatch`, `model.unknown_node` | as everywhere |

## The front end

Panel id `materials` ("Materials", icon `FlaskConical`), default a tab beside `properties` on
the right (placement rule as for the Explore panels: beside `properties`, else `output`), shown
by the Physics stage. The panel never computes a property: it calls the commands above and
renders their results.

| Part | Contents |
|---|---|
| Library browser | search field (name, alias, formula, CAS), provider chips (built-in, CoolProp, Incompressible, Cantera, REFPROP when present, user library), kind filter; rows: name, formula, provider badge, T range, four coverage dots (ρ μ κ c_p); Add opens the state card with the defaults (phase, T range, `p_ref`, composition and basis for mixtures and solutions) |
| Material card | kind, source and provider badges, fluid and composition, state (`phase`, `T_ref`, `p_ref`, `T_min`, `T_max`, `tolerance`, `max_degree`) as quantity fields, Refit and Check actions, the findings list (severity dots, message, suggestion as an action where there is one) |
| Property editors | one card per property: method select (the catalogue filtered by kind, with the engine's refusals greyed and explained), the method's parameters as schema fields with unit chips and expressions, validity range, the fit badge (method, max error, provider) and a Detach/Refit toggle for library properties |
| Plot | uPlot: the selected property against T over [`T_min` − 10 %, `T_max` + 10 %] (or strain rate for non-Newtonian laws): the method curve, the library points when there are any, a shaded band outside [`valid_min`, `valid_max`]; hover shows both values and the deviation; a p slider for pressure-dependent methods |

The Properties panel shows a `property` node with the same filtered fields and a small plot;
the tree gives materials and properties their own icons; the palette lists `material.*`.
Regions (M3) choose a material from a select over `material.list` filtered by kind.

## Tests

| Test | What it asserts |
|---|---|
| `test_materials_model.py` | node specs: defaults valid, JSON schema generated, persistence round trip; `material.create` builds the right children per kind; children cannot be removed or renamed; `set_method` refuses parameters the method does not use and methods foreign to the kind |
| `test_materials_catalogue.py` | every method has an evaluator, an `of14` mapping or an explicit refusal, a Fluent equivalent, an expression or `—`; every (property, material kind) has a default; evaluators reproduce hand-computed values (ideal gas; Sutherland for air at 300 K, A_s 1.458e-6 and T_s 110.4: 1.846e-5 Pa·s; Boussinesq; polynomial; piecewise-linear clamping; Tait against `adiabatic` parameters; JANAF against Cantera's `air.yaml` N2 coefficients) |
| `test_materials_fit.py` | synthetic grids (a known polynomial, a known Sutherland law) are recovered to 1e-10; the raw-coefficient re-verification lowers the degree when conditioning fails; `materials.fit_tolerance` when a 1 % tolerance cannot be met with degree 1; 17-digit coefficient text round-trips |
| `test_materials_coolprop.py` (skipped without CoolProp) | reference states against the NIST WebBook (IAPWS-95 and the IAPWS transport formulations): water 300 K, 101325 Pa: ρ 996.557 kg/m³, μ 853.742 µPa·s, κ 0.609500 W/(m·K), c_p 4180.64 J/(kg·K); water 350 K: 973.728, 368.470, 0.664874, 4194.47; steam 500 K: 0.440930, 17.2987, 0.0360352, 1981.61; nitrogen 300 K: 1.13816, 17.8901, 0.0259687, 1041.36; dry air 300 K (Lemmon, through CoolProp `Air`, no WebBook page): 1.17700, 18.5373, 0.0263845, 1006.37; tolerances 1e-4 on ρ and c_p, 1e-3 on μ and κ; `add_from_library("Water")` meets 1 % over 280–370 K with ρ `rational_polynomial`, a constant c_p (it varies by less than 1 % there, and `constant` is the first candidate, as for the built-in record), a polynomial κ and a `log_polynomial` μ; `add_from_library("Air")` yields `ideal_gas` density; INCOMP `MEG` at 0.5 mass fraction reports `T_freeze` 237.16 K and no speed of sound; a fluid without a viscosity model (`Neon`) yields `missing` and `materials.property_unavailable` when its viscosity is asked for; `phase: auto` refuses a range across 373.12 K at 1 atm; `phase: liquid` fits it with a `materials.phase_forced` finding |
| `test_materials_cantera.py` (skipped without Cantera) | `air.yaml` at `O2:0.21,N2:0.78,AR:0.01`, 300 K, 1 atm: M 28.970 kg/kmol, ρ 1.1768, c_p within 0.5 % of 1003.05 J/(kg·K); a `gri30.yaml` composition `CH4:1,O2:2,N2:7.52` evaluates; the NASA-7 export as `janaf` reproduces Cantera's c_p to 1e-6 |
| `test_materials_refprop.py` | detection by the settings path, `COOLPROP_REFPROP_ROOT`, `RPPREFIX` and `PATH`, and `materials.refprop_missing` with a wrong path or a library that will not load, on simulated installations (always run); where REFPROP is found, `REFPROP::WATER` matches `HEOS::Water` density to 1e-9 at 300 K and REFPROP gives Neon the transport CoolProp lacks |
| `test_materials_regenerate.py` (skipped without CoolProp) | the eleven built-in fluid records regenerate from CoolProp with the same method and degree, within their stored errors (to 1e-6 with the CoolProp version they name) |
| `test_materials_worker.py` | a background fit's events in order (stale, progress, fitted, ok), its own undo step, `materials.busy`, a result dropped when the material changed, cancellation of a CoolProp mixture fit, typed errors from the worker |
| `test_materials_builtin.py` | every record has a `reference` naming a public source; fluid records agree with the NIST WebBook or CoolProp values above within their fit tolerance; solid records match their cited source values exactly and stay within 3 % of the second cited source |
| `test_materials_commands.py` | every `material.*` command through `Session.execute`: result, events, undo, journal replay into a fresh session; `set_state` with `refit` is one undo step; a background fit (forced by a slow fake provider) emits progress, fitted, and clears `stale`; `material.check` codes |
| `test_compile_materials.py` (slice 3) | golden `physicalProperties` for every row of the strategy table: constant water (`rhoConst`/`hConst`/`const` with `kappa`), air (`perfectGas`/`janaf`/`sutherland`), Boussinesq air, incompressible ideal gas (`pRef`, runtime-compiled warning), a liquid with `rPolynomial` and constants, a Tait liquid as `adiabaticPerfectFluid`, a library liquid tabulated (`icoTabulated`/`hIcoTabulated`/`icoTabulated`), a gas with polynomial transport (runtime-compiled), aluminium (`constIsoSolid`/`eConst`), an anisotropic solid, an `exponentialSolid`; `incompressibleFluid` `physicalProperties` with `nu`; refusals with their codes for T-dependent density on `incompressibleFluid`, a stale fit, a missing molar mass |
| `test_materials_packaging.py` | nothing the wheel takes is a REFPROP file; `import stokes.api` loads neither CoolProp, Cantera, scipy nor numpy; using the providers keeps CoolProp and Cantera out of the API process |
| `tests/perf/test_materials_perf.py` | `material.add_from_library("Water")` from CoolProp under 200 ms with a warm worker (machine-factored; 36 ms measured); `material.evaluate` of 101 points under 50 ms; `import stokes.physics.materials` under 50 ms |
| front end | the Materials panel constructs with every provider absent (only built-in), the plot redraws on theme change, the method select lists only valid methods, a fit badge turns stale and back |

## Implementation status

Slice 1 (2026-10-03) delivers the node types, the seven dimensions, the catalogue with every
evaluator but the non-Newtonian laws and `real_gas_table`, the engine-free checks, the `builtin`
provider behind the `Provider` protocol, and the commands `material.methods`, `create`,
`add_from_library` (built-in records), `set_method`, `evaluate`, `check`, `list`,
`library_search` and `library_info`. A built-in material stores `source: builtin`,
`provider: builtin`, `fluid` = the record id and the record's phase; each property's
`meta.library` = `{provider, record, method, reference, derived?}` (`derived` when `methods`
forced a form), its `signature`, and for a fluid also `meta.fit` describing the offline CoolProp
fit the record holds.

Slice 2 (2026-10-04) delivers:

- the library providers `coolprop` (HEOS pure and pseudo-pure fluids, predefined `*.mix` and
  ad-hoc `A&B` mixtures by mole fraction), `coolprop_incomp` (pure liquids and solutions by mass
  or volume fraction, with their native basis and fraction limits), `cantera` (`air.yaml`,
  `gri30.yaml`, `h2o2.yaml` or an absolute `.yaml` path, mole or mass fractions, NASA-7 as
  `janaf`) and `refprop` (detection, the lock, the worker restart on a new path), as
  `RemoteProvider` proxies of the materials worker process, with search indexes that report
  transport coverage (CoolProp 8.0.0 HEOS: viscosity for 66 of 136 fluids, conductivity for 63,
  neither for 70) and are cached on disk;
- `fit.py` with the contract's candidate order, tolerance, verification grid, raw-coefficient
  check and forced forms; the eleven built-in fluid records regenerate from CoolProp within
  their stored errors;
- the worker process, background fits with progress, cancellation (`material.cancel_fit`),
  `materials.busy`, results landing on the server's event loop as their own undo step, and the
  per-session evaluation cache;
- the commands `material.providers`, `refit`, `set_state` and `cancel_fit`, and
  `add_from_library` with `provider`, `composition`, `basis`, `kind`, `phase`, `tolerance`,
  `max_degree`, `methods` and `wait`; `library_info` with `phase`, `T_ref`, `p_ref`;
- `material.create` templates made from the built-in records (decisions log F6 (a));
- `parameter.set` on a fitted or catalogue property marks it detached in the same undo step
  (F6 (c)) when the session's materials service exists (it is made by the first `material.*`
  command); before that, the signature still shows the edit;
- the checks `materials.stale_fit` (also never fitted), `materials.property_unavailable` and
  `materials.phase_forced`;
- `stokes material providers|add|set-state|refit|check` beside the earlier verbs.

Not built yet: `material.duplicate`, `export`, `import`, `save_to_library` and the
`user_library` provider; a settings store for the REFPROP path (it lasts for the process); the
materials service made at `project.open` (so the F6 (c) mark does not wait for a `material.*`
command); the engine-dependent checks (slice 3).

## Known limits (format 1)

- One pressure per material (`p_ref`); pressure-dependent library properties arrive with
  `real_gas_table` and the fitted C₃, C₄ of `rational_polynomial` in v1.5.
- Mixtures with species transport are not modelled; a Cantera or CoolProp mixture at a fixed
  composition is a `fluid` with frozen composition.
- Non-Newtonian laws evaluate against strain rate and compile for `of14.incompressibleFluid`
  only (slice 5).
- Humid air (`HAPropsSI`) and `IF97::Water` are not providers in v1.
