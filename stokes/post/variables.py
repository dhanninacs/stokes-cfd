# ruff: noqa: RUF001 - the meanings are written for people, so Greek letters and dashes are meant.
"""The variables a results view can show: every field a run wrote, the derived quantities built on
them, and the user's own field functions, each with a group, a label, units and a meaning.

**Two kinds of derived quantity.** Those that need the volume gradient (vorticity, Q-criterion,
wall shear stress, y+) are computed by the agent when it extracts the fields
(`stokes_agent.derived`) and arrive as stored fields. Those that are a formula over stored point
values (dynamic and total pressure, the pressure coefficient) are **built-in field functions**:
expressions in the one grammar, evaluated by `stokes.post.calculator` exactly as a user's function
is, so they report, plot and colour the model through the same paths. A user's function of the
same name wins, as a local definition should.

An incompressible run's pressure is kinematic (`p/rho`, m2/s2) and the formulas follow it: the
dynamic pressure is `0.5 |U|^2` there and `0.5 rho |U|^2` with an absolute pressure in Pa. The
pressure coefficient needs a reference speed and pressure; `stokes.api.commands.results` gives the
area-weighted mean speed on the inlets and mean pressure on the outlets as `U_ref` and `p_ref`, and
says so in the variable's meaning.

Pure and standard library only: the catalogue is data.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The groups a picker shows, in order.
GROUPS: tuple[str, ...] = (
    "Velocity",
    "Pressure",
    "Temperature",
    "Turbulence",
    "Flow features",
    "Wall",
    "Time-averaged",
    "Other",
    "Functions",
)


@dataclass(frozen=True)
class Variable:
    """What a picker needs to offer one variable."""

    name: str
    label: str
    group: str
    description: str
    symbol: str = ""

    def to_json(self, units: str, source: str, available: bool, why: str = "") -> dict[str, object]:
        out: dict[str, object] = {
            "name": self.name,
            "label": self.label,
            "symbol": self.symbol or self.name,
            "group": self.group,
            "units": units,
            "description": self.description,
            "source": source,
            "available": available,
        }
        if why:
            out["why"] = why
        return out


#: Every stored field Stokes knows by name. A stored field not in this table is still offered, in
#: "Other", under its own name.
KNOWN: dict[str, Variable] = {
    v.name: v
    for v in (
        Variable("U_mag", "Velocity magnitude", "Velocity", "The speed of the flow.", "|U|"),
        Variable("U_x", "Velocity X", "Velocity", "The velocity's x component, signed.", "Ux"),
        Variable("U_y", "Velocity Y", "Velocity", "The velocity's y component, signed.", "Uy"),
        Variable("U_z", "Velocity Z", "Velocity", "The velocity's z component, signed.", "Uz"),
        Variable(
            "p",
            "Static pressure",
            "Pressure",
            "The solver's pressure: kinematic (p/ρ) for an incompressible run, absolute in Pa "
            "with heat transfer.",
            "p",
        ),
        Variable("T", "Temperature", "Temperature", "The static temperature.", "T"),
        Variable(
            "k",
            "Turbulent kinetic energy",
            "Turbulence",
            "Energy of the turbulent fluctuations per unit mass.",
            "k",
        ),
        Variable(
            "epsilon",
            "Turbulent dissipation rate",
            "Turbulence",
            "Rate at which turbulent energy is dissipated (k-ε models).",
            "ε",
        ),
        Variable(
            "omega",
            "Specific dissipation rate",
            "Turbulence",
            "Dissipation per unit turbulent energy (k-ω models).",
            "ω",
        ),
        Variable(
            "nut",
            "Turbulent viscosity",
            "Turbulence",
            "The eddy viscosity the turbulence model adds (kinematic).",
            "νt",
        ),
        Variable(
            "alphat",
            "Turbulent thermal diffusivity",
            "Turbulence",
            "The heat diffusivity the turbulence model adds (kg/m/s).",
            "αt",
        ),
        Variable(
            "vorticity_mag",
            "Vorticity magnitude",
            "Flow features",
            "|∇×U|: how fast the fluid rotates locally.",
            "|ω|",
        ),
        Variable(
            "Q",
            "Q-criterion",
            "Flow features",
            "½(|Ω|² − |S|²): positive where rotation beats strain, the usual way to find vortex "
            "cores (an iso-surface at a small positive value).",
            "Q",
        ),
        Variable(
            "wallShearStress_mag",
            "Wall shear stress",
            "Wall",
            "The tangential stress the flow puts on a wall, from the velocity gradient at the wall "
            "(kinematic, τ/ρ, for an incompressible run).",
            "τw",
        ),
        Variable(
            "yPlus",
            "y+",
            "Wall",
            "The dimensionless distance of the first cell from the wall: about 1 or less resolves "
            "the viscous sublayer, 30–300 suits wall functions.",
            "y+",
        ),
        Variable(
            "wallHeatFlux",
            "Wall heat flux",
            "Wall",
            "Heat crossing the wall into the fluid per unit area, from the temperature gradient at "
            "the wall (with the turbulent diffusivity where the run has it).",
            "q",
        ),
        Variable(
            "UMean_mag",
            "Mean velocity magnitude",
            "Time-averaged",
            "|U| averaged over time (fieldAverage).",
            "|Ū|",
        ),
        Variable("pMean", "Mean pressure", "Time-averaged", "Pressure averaged over time.", "p̄"),
        Variable(
            "kMean", "Mean turbulent kinetic energy", "Time-averaged", "k averaged over time.", "k̄"
        ),
        Variable(
            "nutMean", "Mean turbulent viscosity", "Time-averaged", "νt averaged over time.", "ν̄t"
        ),
        Variable("TMean", "Mean temperature", "Time-averaged", "T averaged over time.", "T̄"),
    )
}

#: The vectors Stokes knows, with what their components are called and where they are grouped.
VECTOR_NAMES: dict[str, tuple[str, str]] = {
    "U": ("Velocity", "Velocity"),
    "UMean": ("Mean velocity", "Time-averaged"),
    "vorticity": ("Vorticity", "Flow features"),
    "wallShearStress": ("Wall shear stress", "Wall"),
}

#: Stored fields that are never offered on their own: vectors (their magnitude and components are).
VECTORS: frozenset[str] = frozenset({"U", "UMean", "vorticity", "wallShearStress"})


@dataclass(frozen=True)
class Builtin:
    """A built-in field function: a definition for each kind of pressure, and what it needs."""

    variable: Variable
    kinematic: str
    absolute: str
    needs: tuple[str, ...]


BUILTINS: dict[str, Builtin] = {
    b.variable.name: b
    for b in (
        Builtin(
            Variable(
                "p_dynamic",
                "Dynamic pressure",
                "Pressure",
                "½ρ|U|²: the pressure the flow would gain if brought to rest (½|U|² per unit "
                "density for an incompressible run).",
                "q",
            ),
            kinematic="0.5 * U_mag^2",
            absolute="0.5 * rho * U_mag^2",
            needs=("U_mag",),
        ),
        Builtin(
            Variable(
                "p_total",
                "Total pressure",
                "Pressure",
                "Static plus dynamic pressure: what a pitot tube reads.",
                "p0",
            ),
            kinematic="p + 0.5 * U_mag^2",
            absolute="p + 0.5 * rho * U_mag^2",
            needs=("p", "U_mag"),
        ),
        Builtin(
            Variable(
                "Cp",
                "Pressure coefficient",
                "Pressure",
                "(p − p_ref)/(½ρU_ref²), with U_ref the mean inlet speed and p_ref the mean "
                "outlet pressure of this run.",
                "Cp",
            ),
            kinematic="(p - p_ref) / (0.5 * U_ref^2)",
            absolute="(p - p_ref) / (0.5 * rho * U_ref^2)",
            needs=("p", "U_mag", "p_ref", "U_ref"),
        ),
    )
}


#: Why an agent-derived quantity is missing from a run read before the agent computed it.
READ_AGAIN = "not in this run's fields yet: read the fields again"


def builtin_definitions(kinematic: bool, has_density: bool) -> dict[str, str]:
    """The built-in functions as `name -> definition` for a run's kind of pressure.

    With an absolute pressure and no known density the functions that need `rho` are left out
    rather than given a wrong unit.
    """
    out: dict[str, str] = {}
    for name, builtin in BUILTINS.items():
        if kinematic:
            out[name] = builtin.kinematic
        elif has_density:
            out[name] = builtin.absolute
    return out


def describe(name: str) -> Variable:
    """The catalogue entry of a stored field, or a plain one for a field Stokes does not know."""
    found = KNOWN.get(name)
    if found is not None:
        return found
    if name.endswith("_mag"):
        base = name[: -len("_mag")]
        return Variable(name, f"{base} magnitude", "Other", f"The magnitude of the vector {base}.")
    for suffix, axis in (("_x", "X"), ("_y", "Y"), ("_z", "Z")):
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            if base in VECTOR_NAMES:
                label, group = VECTOR_NAMES[base]
                return Variable(
                    name,
                    f"{label} {axis}",
                    group,
                    f"The {axis} component of {label.lower()}, signed.",
                )
            return Variable(name, f"{base} {axis}", "Other", f"The {axis} component of {base}.")
    return Variable(name, name, "Other", "A field the solver wrote.")


def catalogue(
    stored: dict[str, tuple[str, int]],
    functions: dict[str, str],
    kinematic: bool,
    has_density: bool,
    has_references: bool,
    function_units: dict[str, str] | None = None,
    derives: bool = True,
) -> list[dict[str, object]]:
    """Every variable a run can show, grouped and ordered as a picker lists them.

    ``stored`` maps each stored field to `(units, components)`. A vector is offered as its
    magnitude (when stored, or derived as `U_mag` is) and its three components. Built-ins are
    listed with `available: false` and the reason when the run lacks what they need.
    """
    seen: dict[str, dict[str, object]] = {}
    pressure_units = stored.get("p", ("", 1))[0]
    for name, (units, components) in stored.items():
        if components == 3:
            for axis in ("x", "y", "z"):
                component = f"{name}_{axis}"
                if component not in stored:
                    seen[component] = describe(component).to_json(units, "run", True)
            continue
        if name in VECTORS:
            continue
        seen[name] = describe(name).to_json(units, "run", True)
    definitions = builtin_definitions(kinematic, has_density)
    for name, builtin in BUILTINS.items():
        if name in seen or name in functions:
            continue
        missing = [n for n in builtin.needs if n in ("p", "U_mag") and n not in seen]
        why = ""
        if missing:
            why = f"the run has no {', '.join(missing)}"
        elif name not in definitions:
            why = "the fluid's density is not known"
        elif "U_ref" in builtin.needs and not has_references:
            why = "the model has no inlet and outlet to take reference values from"
        units = "" if name == "Cp" else pressure_units
        seen[name] = builtin.variable.to_json(units, "derived", why == "", why)
    # The agent's derived quantities a run read before they existed lacks: listed, with the remedy.
    if derives and "U" in stored and "vorticity_mag" not in stored:
        agent = ["vorticity_mag", "Q", "wallShearStress_mag", "yPlus"]
        if "T" in stored:
            agent.append("wallHeatFlux")
        for name in agent:
            if name not in seen:
                seen[name] = KNOWN[name].to_json("", "run", False, READ_AGAIN)
    for name, definition in functions.items():
        units = (function_units or {}).get(name, "")
        seen[name] = Variable(name, name, "Functions", definition).to_json(units, "function", True)
    order = {g: i for i, g in enumerate(GROUPS)}
    known_order = {n: i for i, n in enumerate([*KNOWN, *BUILTINS])}
    return sorted(
        seen.values(),
        key=lambda v: (
            order.get(str(v["group"]), len(GROUPS)),
            known_order.get(str(v["name"]), len(known_order)),
            str(v["name"]),
        ),
    )
