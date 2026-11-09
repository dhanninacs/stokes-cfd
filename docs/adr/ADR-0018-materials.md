# ADR-0018 · Materials: a Fluent-parity method catalogue, library-backed materials fitted from CoolProp, Cantera and REFPROP, and their compilation

**Status:** accepted · 2026-10-03 · relies on ADR-0001 (GPL boundary), ADR-0008 (Foundation 14 dialect), ADR-0011 (engines; Fluent via PyFluent), ADR-0012 (quantities and expressions), ADR-0016 (studies vary any parameter); decision F4 · owner review requested on the items in docs/00 "Still open"

## Context

The owner asked for material properties done the way Fluent does them, with materials added
from libraries (CoolProp and others), temperature- and pressure-dependent, ideal or
incompressible, every method per property, built robustly on the libraries. Decision F4 fixed
the providers (built-in catalogue, CoolProp, Cantera, REFPROP through CoolProp when the user has
it); this ADR fixes the model, the methods, the fits and the compilation. The builder's contract
is `docs/engineering/materials-schema.md`; the verified facts are condensed in
`docs/research/materials/` (Fluent 2025 R2 from the local corpus, `[fu pN]`; OpenFOAM Foundation
12/13/14/dev from the GitHub sources; CoolProp 8.0.0 and Cantera 3.2.0; PyFluent 0.42), plus a
probe of CoolProp 8.0.0 run for this design.

What was found:

- **Fluent** keeps one Create/Edit Materials dialog for fluid, solid, mixture and particle
  materials with a ~675-entry database, and per property a method drop-down: density
  `constant, ideal-gas, incompressible-ideal-gas, boussinesq, polynomial, piecewise-linear,
  piecewise-polynomial, compressible-liquid (Tait), real-gas RK/SRK/PR/Aungier, real-gas-nist
  (REFPROP 10), rgp-table, user-defined, expression` [fu p1676]; viscosity adds Sutherland
  (two and three coefficients), power-law, kinetic theory, non-Newtonian laws (laminar only)
  and mixing laws; specific heat adds kinetic theory and NASA-9 polynomials and never takes an
  expression; conductivity adds kinetic theory and five anisotropic solid forms. Polynomials
  take 8 coefficients, piecewise-linear 50 points (held at the ends), piecewise-polynomial 3
  ranges × 8 [fu p1662–1668]. Pressure enters through the operating pressure (ideal gas:
  p_op + p_gauge; incompressible ideal gas: p_op only) and the real-gas models; Boussinesq's T₀
  is an operating condition; a method that needs energy switches the energy equation on; only
  piecewise-linear clamping is documented, polynomials are left to solution limits
  [fu p1664, p1671, p1730].
- **OpenFOAM Foundation 12–14** select a fluid's `thermoType` in `constant/physicalProperties`
  (`heRhoThermo`, `pureMixture`, transport, thermo, equationOfState, `specie`, energy) and a
  solid's `heSolidThermo` with `e*` thermos only. The precompiled selections are a gas set
  (`const`/`sutherland` × `perfectGas`/`Boussinesq` × `hConst`/`janaf`/`eConst`), a liquid set
  (`const` × `rhoConst`/`rPolynomial`/`adiabaticPerfectFluid` × `hConst`/`eConst`) and two
  all-tabulated sets (T-only `icoTabulated`/`hIcoTabulated`; (p, T) `rhoTabulated`/`hTabulated`/
  `tabulated`); everything else, `incompressiblePerfectGas`, `icoPolynomial`, `polynomial`,
  `logPolynomial`, `WLF`, `Andrade`, `PengRobinsonGas`, `hPolynomial` included, is compiled on
  the engine host on first use when `allowSystemOperations` is on (the default) and the build
  tools exist. `const` takes `mu` with exactly one of `Pr` or `kappa`; `sutherland` derives κ by
  the modified Eucken relation; the polynomial and tabulated transports carry μ and κ in the
  same form; `incompressibleFluid` takes only `viscosityModel constant; nu`, with non-Newtonian
  laws under `momentumTransport`. Tait's law and `adiabaticPerfectFluid` are one law
  (B = K₀/n − p₀). Keys are lower-case (`hf`, `sf`), `molWeight` is in kg/kmol, release 14
  renamed `C0` to `c0` in the power thermos.
- **CoolProp 8.0.0** (MIT, one `cp312-abi3` wheel covering Python 3.12–3.14 on Windows,
  manylinux and macOS; nanobind wrapper with enum configuration keys) offers 136 HEOS fluids,
  147 predefined and ad-hoc mixtures, 74 incompressible liquids and 52 solutions by mass or
  volume fraction, tabular and IF97 backends, and the REFPROP backend. Viscosity exists for 66
  fluids and conductivity for 63 (70 have neither); INCOMP returns placeholder transport values
  where it has no correlation. A pure-fluid state costs 25–150 µs, a mixture state 8–85 ms, a
  bicubic table 11–39 s and 16 MB to build. CoolProp finds REFPROP through
  `COOLPROP_REFPROP_ROOT`, its configuration keys or `PATH`, not through NIST's `RPPREFIX`;
  REFPROP is not re-entrant and is NIST's licensed product. Polynomial fits of CoolProp data
  over 280–370 K (water) and 250–1000 K (air) show cubic density, specific heat and conductivity
  within 1e-3 and viscosity needing a log-polynomial (3e-3) where a cubic fails (6e-2); the
  ideal-gas law reproduces air's density within 1e-3 and Sutherland's handbook constants its
  viscosity within 4e-2.
- **Cantera 3.2.0** (BSD-3, wheels cp310–cp314 for Windows and manylinux; `numpy`,
  `ruamel.yaml` MIT, `typing_extensions` PSF) gives ideal-gas mixtures from YAML mechanisms
  (`air.yaml`, `gri30.yaml`) at any composition with mixture-averaged transport and exposes
  NASA-7 coefficients that map onto OpenFOAM's `janaf`.
- **PyFluent 0.42** (MIT; Fluent 24.2–27.1) sets materials through
  `solver.settings.setup.materials.fluid[name].<property> = {"option": ..., ...}` with
  sub-objects for polynomial, piecewise, Sutherland, power-law and Tait forms, leaves for
  molecular weight, thermal expansion, formation enthalpy and entropy, Lennard-Jones and
  critical data, and operating conditions under `setup.general.operating_conditions`.

Constraints from the constitution: every numeric parameter is a quantity (rule 6), the case is a
pure function of the model (rule 9), heavy work leaves the UI thread (rule 7), no dialect
vocabulary in the tree (ADR-0008), studies must be able to vary any material parameter
(ADR-0016), and nothing GPL or proprietary ships (ADR-0001, F4).

## Decision

1. **Materials are nodes** under `/physics/materials`: a `material` (kind `fluid` or `solid`;
   `mixture` reserved) with fixed `property` children `density`, `viscosity`, `specific_heat`,
   `thermal_conductivity`, `molar_mass`, `formation_enthalpy` as the kind requires. A property
   carries one `method` from the catalogue and that method's parameters as quantities (constants,
   reference temperatures, Sutherland coefficients, β, σ, ε/k, critical data, yield stress, ...)
   or SI coefficient arrays (polynomials, JANAF, tables). Regions reference materials by id and
   accept only their own kind. Quantities take expressions, so `materials.oil.viscosity.value`
   is addressable from any field and from a study variable.
2. **The material's state** is its own: `T_ref` (Boussinesq and power-law T₀, the operating
   temperature Fluent needs), `p_ref` (the pressure at which a library is evaluated, the
   `incompressible_ideal_gas` and Tait reference), the fit range `T_min`..`T_max`, `phase`
   (auto, or forced liquid/gas), `tolerance` and `max_degree`. `p_ref` defaults to 101325 Pa,
   may be an expression over the operating conditions node (M3), and the compiler warns on a
   mismatch.
3. **The method catalogue** (`stokes.physics.materials.catalogue`) is a declarative table of
   methods per property and material kind with parameters, independent variable, a pure numpy
   evaluator, the Foundation 14 selection and the Fluent method. It reaches Fluent parity where
   the physics is the same: density `constant, ideal_gas, incompressible_ideal_gas, boussinesq,
   polynomial, piecewise_linear, piecewise_polynomial, rational_polynomial, perfect_fluid,
   compressible_liquid (Tait = adiabaticPerfectFluid), peng_robinson, real_gas_table (v1.5)`;
   viscosity `constant, sutherland, power_law, polynomial, log_polynomial, andrade, wlf,
   piecewise_linear, piecewise_polynomial, kinetic_theory` and the non-Newtonian
   `shear_power_law, carreau, cross, herschel_bulkley, casson`; specific heat `constant,
   polynomial, janaf, nasa9_polynomial, power, piecewise_linear, piecewise_polynomial,
   kinetic_theory`; conductivity `constant, anisotropic, polynomial, log_polynomial,
   exponential, piecewise_linear, piecewise_polynomial, kinetic_theory`. Solid density is
   constant, as in both targets. Fluent's cubic equations of state other than Peng-Robinson,
   its mixing laws and particle materials are not in v1.
4. **Library materials** are added through one search across providers (`builtin`, `coolprop`
   HEOS, `coolprop_incomp`, `cantera`, `refprop`, and the user's saved materials) and store
   provider, fluid id, composition and basis, phase, state and range; their property methods
   are **fitted** from the provider over the range at `p_ref` by `fit.py`: candidate forms in a
   fixed order (ideal gas, constants, rational polynomial, Sutherland, log-polynomial, JANAF,
   polynomials up to `max_degree`), accepted when the maximum relative deviation on an
   offset verification grid is within `tolerance` (default 1 %), otherwise refused with the best
   form and its error, with `piecewise_linear` (the grid itself) always available. The library
   stays the source of truth: the fit is recorded in the property's `meta.fit` with the
   provider's version and citation, is regenerated by `material.refit` whenever the state
   changes, goes `stale` meanwhile, and a hand edit detaches the property. Fitted coefficients
   live in the project, so compiling never needs the library.
5. **Pressure dependence** in v1 is through the methods that carry it exactly (`ideal_gas`,
   `incompressible_ideal_gas`, `perfect_fluid`, `compressible_liquid`, `rational_polynomial`,
   `peng_robinson`); a library is evaluated at `p_ref`. v1.5 adds `real_gas_table` ((p, T)
   tables for the tabulated OpenFOAM set, `real-gas-nist` or RGP for Fluent) and the fitted
   pressure terms of `rational_polynomial`.
6. **Providers** sit behind one `Provider` protocol (`available`, `search`, `info`, `evaluate`
   on a T grid at p), imported lazily, in the optional `materials` extra (`CoolProp>=8.0`,
   `cantera>=3.2`; both pass the allow-list). REFPROP is detected from the settings path,
   `COOLPROP_REFPROP_ROOT`, `RPPREFIX` (passed on to CoolProp's `ALTERNATIVE_REFPROP_PATH`) or
   `PATH`, confirmed by constructing a REFPROP state, serialised by one lock, and never bundled
   (a packaging test asserts it). Evaluations are cached per session; those estimated above
   200 ms (mixtures, REFPROP mixtures) run in a worker with progress events and the properties
   stale until done. Missing transport models are data (`missing`), not failures.
7. **Checks** (`checks.py`) run live in `material.check` and again at compile, refusing on
   errors: molar mass needed, energy equation required for a temperature-dependent method,
   method unsupported by the engine (temperature-dependent density or viscosity with
   `incompressibleFluid` or the coupled solver), stale fits, kind mismatch; warning on
   runtime-compiled thermoTypes, Eucken-derived conductivity under `sutherland`, Boussinesq
   range, extrapolation beyond the validity range, non-Newtonian laws with turbulence,
   operating-pressure mismatch, forced phases.
8. **Compilation to Foundation 14** maps methods to selections by the table in the contract and
   prefers the precompiled sets. When a material does not map onto one, the engine setting
   `property_tables` (default on) samples a liquid's temperature-dependent methods onto 101
   points and writes the T-only tabulated set (exact for `piecewise_linear`, interpolation error
   reported), while gases keep `perfectGas` with runtime-compiled transport and thermo and a
   warning naming the build tools the engine host needs; with the setting off, the
   runtime-compiled selections are written for liquids too. The writer applies the pairing and
   derivation rules (`kappa` in `const`, μ and κ in one form, Eucken under `sutherland`, lower-case
   keys, `c0` in 14) and ships golden files for every strategy row. `incompressibleFluid` and
   the coupled solvers take constants only. The ESI dialect reuses the vocabulary with its
   spellings in v1.5; the Fluent route writes PyFluent settings calls, using Fluent expressions
   for methods without a Fluent option (density, viscosity, conductivity only) and offering
   `real-gas-nist` for library fluids with a REFPROP name.
9. **API**: `material.providers`, `library_search`, `library_info`, `methods`, `create`,
   `add_from_library`, `set_method`, `set_state`, `refit`, `evaluate`, `check`, `duplicate`,
   `export`, `import`, `save_to_library`, `list`, mirrored by `stokes material`; events
   `material.fitted`, `fit_progress`, `fit_failed`, `providers_changed`; every mutation one undo
   step. The front end gets a **Materials** panel (library browser, material state card,
   per-property method editors, a live property plot from `material.evaluate`) registered like
   the Explore panels and shown by the Physics stage; the Properties panel shows a property with
   the same filtered fields.
10. **Dimensions** `molar_mass`, `thermal_expansion`, `specific_energy`, `specific_entropy`,
    `specific_volume`, `sutherland_coefficient` and `surface_tension` join `stokes.units`.
11. **Tests** follow the contract's list: catalogue completeness, fit recovery and refusal,
    CoolProp against NIST WebBook states (water, steam, nitrogen; air against Lemmon through
    CoolProp), Cantera `air.yaml`, REFPROP detection when present, the built-in catalogue against
    its cited tables, every command through `Session.execute` with replay, golden
    `physicalProperties` files, packaging (no REFPROP, no eager imports), perf budgets.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Evaluate the library at run time (a coded `Function1`, a UDF, or writing a CoolProp call into the case) | A coded function links GPL OpenFOAM code to a non-GPL library on the engine host, the case stops being a pure function of the model, and the project would not compile without the library installed. |
| Always write tables (never fit) | Tables hide the physics (an ideal gas becomes 101 numbers), Fluent takes at most 50 points, and the precompiled OpenFOAM sets want `perfectGas`, `sutherland` and `janaf` for gases. Fits keep the engineer-facing method and fall back to tables. |
| Always write polynomials (never tables) | Every temperature-dependent liquid would compile its thermoType at run time, which needs the build tools on every engine host and fails on package installs without them. |
| One node type per method (`density_sutherland`, ...) | Changing a method would replace the node and break ids, expression references and study variables; one `property` type with a method enum keeps `materials.x.density.value` stable. |
| Properties as parameters of the material node, no children | Loses per-property status, provenance and references; the union of parameters on one node would be unreadable. |
| Store only the library reference and refit on every compile | Compile would need the library and a network of versions; a stored fit with its citation is reproducible and inspectable. |
| CoolProp's tabular backends for every evaluation | 11–39 s and 16 MB per fluid before the first number, and wrong-branch errors near saturation; direct HEOS costs under 100 ms per fit. Kept for the (p, T) tables of v1.5. |
| Ship REFPROP data or a REFPROP wrapper | NIST's licence forbids redistribution; CoolProp's backend with the user's installation is the only route (F4). |

## Consequences

- New packages: `stokes.physics.materials` (`model`, `catalogue`, `methods`, `fit`,
  `evaluate`, `checks`, `providers/`, `data/materials.json`) and
  `stokes.compile.of14.materials`; the `materials` extra; seven dimensions; `material.*`
  commands and `stokes material`; the Materials panel. The tree gains two node types and the
  skeleton group `/physics/materials` gets content; `web/src/panels/nodeRules.ts` already treats
  it as structural.
- Harder: the method union on one node type needs the catalogue to drive the forms; the
  runtime-compiled warning depends on the engine probe (M2) reporting `wmake`; Fluent option
  strings are pinned per PyFluent release.
- Easier: studies vary any property constant or state value without new machinery; the
  function engine can evaluate properties; the compiled-case preview shows the fit's citation
  as a comment; agents add a material from a library in one call.
- Dependencies register: CoolProp 8.0 (MIT) and cantera 3.2 (BSD-3) move from "planned" to
  the `materials` extra when slice 2 lands, with their transitive packages audited (`numpy`,
  `ruamel.yaml`, `typing_extensions`).

Implementation plan, one slice at a time (builder tiers per `docs/engineering/model-routing.md`):

1. **Core model and catalogue (T1).** Dimensions; `material` and `property` node types with
   the fixed children; `catalogue.py` and `methods.py` with every evaluator of decision 3 but
   the non-Newtonian laws; `checks.py` without engine-specific rows; the `builtin` provider with
   twenty referenced records; commands `create`, `set_method`, `set_state` (no refit),
   `methods`, `evaluate` (method source), `check`, `duplicate`, `list`, `export`/`import`
   (stokes format), `save_to_library`; CLI; the model, catalogue, builtin and command tests.
   No CoolProp yet.
2. **Library providers and fits (T1).** `Provider` protocol, `coolprop`, `coolprop_incomp`,
   `cantera`, `refprop` with detection and the lock; `fit.py` with the candidate order,
   tolerance, verification grid and raw-coefficient check; `add_from_library`, `refit`,
   `library_search`, `library_info`, `providers`; the session cache and worker with progress
   events and staleness; the CoolProp, Cantera, REFPROP, fit and perf tests; the `materials`
   extra, dependency register rows and notices.
3. **Compile mapping (T1, golden files T2).** `stokes.compile.of14.materials` with the
   selection table, the precompiled sets, the `property_tables` strategy, pairing and
   derivation rules, `incompressibleFluid` and solid writers; engine capability predicates for
   `of14.incompressibleFluid`, `of14.fluid`, `of14.multiRegion`, `coupled.*`; the compile checks
   (`runtime_compiled`, `tabulated`, `energy_required`, `unsupported_by_engine`,
   `conductivity_*`); `material.export` format `of14`; golden tests for every strategy row. Lands
   with the M3 compiler.
4. **Front end (T1).** The Materials panel (browser, state card, property editors, uPlot
   plot), placement beside Properties, property fields and small plot in the Properties panel,
   tree icons, palette items, the panel tests; `web/DESIGN.md` gains the panel's section.
5. **Non-Newtonian laws and the user library polish (T2).** Strain-rate evaluators, the
   `momentumTransport` writer for `incompressibleFluid`, the laminar check, the strain-rate
   plot axis; `~/.stokes/materials` browsing and sharing as JSON.
6. **v1.5 (T1).** `mixture` kind with species and mixing laws (Fluent's and OpenFOAM's
   `multicomponentMixture`), `real_gas_table` with the (p, T) tabulated set and `real-gas-nist`,
   fitted pressure terms of `rational_polynomial`, humid air and `IF97::Water` providers, the ESI
   spellings, Fluent's remaining cubic equations of state.
