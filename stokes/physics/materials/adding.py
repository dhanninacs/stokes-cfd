"""What ``material.create`` and ``material.add_from_library`` build, without a session
(ADR-0018 slices 1 and 2): a built-in record's property entries and provenance, a library
material's state and stand-in values, the provider report ``material.providers`` returns, and the
checks of the commands' arguments. The commands in ``stokes.api.commands.material`` push what
these return as undo steps.

Import rule: numpy-free at module level (``stokes.api`` imports the command module at start-up);
numpy, the providers and the fits are imported inside the functions that need them.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError

if TYPE_CHECKING:
    from stokes.physics.materials.evaluate import State
    from stokes.physics.materials.library import LibraryState
    from stokes.physics.materials.methods import Array
    from stokes.physics.materials.providers.base import LibraryInfo

LIBRARY: tuple[str, ...] = ("coolprop", "coolprop_incomp", "cantera", "refprop")
LIBRARY_POINTS = 101
"""Points a forced ``piecewise_linear`` samples a built-in record on."""

PROVIDER_NOTES = {
    "builtin": "the built-in catalogue: fluids fitted to CoolProp at stated states, solids "
    "cited to two public sources each",
    "coolprop": "CoolProp HEOS: pure and pseudo-pure fluids, predefined and ad-hoc mixtures by "
    "mole fraction; many fluids have no viscosity or conductivity model, which searches report",
    "coolprop_incomp": "CoolProp INCOMP: incompressible liquids and solutions by mass or volume "
    "fraction",
    "cantera": "Cantera ideal-gas mixtures from YAML mechanisms (air.yaml, gri30.yaml, h2o2.yaml "
    "or a file) with mixture-averaged transport",
    "refprop": "the user's NIST REFPROP through CoolProp's backend; never shipped with Stokes",
}


_NAME_CHARS = re.compile(r"[^A-Za-z0-9_]+")


def node_name(text: str) -> str:
    """A library id as a valid node name: ``R410A.mix`` -> ``R410A_mix``."""
    name = _NAME_CHARS.sub("_", text).strip("_") or "material"
    return f"m_{name}" if name[0].isdigit() else name


def choice(value: Any, allowed: tuple[str, ...], what: str) -> str:  # Any: JSON value
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(
            f"{what} must be one of {', '.join(allowed)}; got {value!r}", details={what: value}
        )
    return value


def check_fit_settings(tolerance: float | None, max_degree: int | None) -> None:
    from stokes.physics.materials.fit import MAX_DEGREE

    if tolerance is not None and not (math.isfinite(tolerance) and 0.0 < tolerance < 1.0):
        raise ValidationError(f"tolerance must lie between 0 and 1; got {tolerance!r}")
    if max_degree is not None and not 0 <= max_degree <= MAX_DEGREE:
        raise ValidationError(f"max_degree must be 0 to {MAX_DEGREE}; got {max_degree!r}")


def check_methods(methods: dict[str, Any]) -> dict[str, str]:  # Any: JSON, checked here
    from stokes.physics.materials.fit import FORCEABLE

    for prop, form in methods.items():
        allowed = FORCEABLE.get(prop)
        if allowed is None or form not in allowed:
            options = allowed or ()
            raise ValidationError(
                f"methods maps a fitted property ({', '.join(FORCEABLE)}) to a form it can be "
                f"fitted as ({', '.join(options) or 'none'}); got {prop!r}: {form!r}",
                details={"property": prop, "method": form},
            )
    return {str(k): str(v) for k, v in methods.items()}


def provider_entries(report: dict[str, Any] | None) -> list[dict[str, Any]]:  # Any: JSON
    from stokes.physics.materials.providers import builtin, remote

    entries: list[dict[str, Any]] = [
        {
            "name": "builtin",
            "available": True,
            "version": builtin.catalogue_version(),
            "reason": "",
            "fluids": len(builtin.load()),
            "note": PROVIDER_NOTES["builtin"],
        }
    ]
    for name in LIBRARY:
        found = (report or {}).get(name) if isinstance(report, dict) else None
        entry: dict[str, Any] = {  # Any: JSON
            "name": name,
            "available": remote.installed(name) if name != "refprop" else None,
            "version": remote.package_version(name) if remote.installed(name) else "",
            "reason": ""
            if remote.installed(name)
            else f"{remote.PACKAGES[name]} is not installed; {remote.INSTALL_HINT}",
            "fluids": None,
            "note": PROVIDER_NOTES[name],
        }
        if isinstance(found, dict):
            entry["available"] = bool(found.get("available"))
            entry["version"] = str(found.get("version") or entry["version"])
            entry["reason"] = str(found.get("reason") or "")
            entry["fluids"] = found.get("fluids")
            if name == "refprop":
                entry["path"] = found.get("path")
                entry["source"] = found.get("source")
        elif name == "refprop":
            if remote.installed(name):
                entry["reason"] = "REFPROP detection has not run yet"
            else:
                entry["available"] = False
        entries.append(entry)
    return entries


def record_properties(
    found: dict[str, Any],  # Any: a catalogue record
    overrides: dict[str, str],
    state: State,
) -> dict[str, dict[str, Any]]:  # Any: JSON property entries
    """The record's properties with the forced forms: ``constant`` takes the value at T_ref,
    ``piecewise_linear`` samples the record's method on 101 points over the range and keeps the
    record's validity range."""
    import numpy as np

    from stokes.physics.materials.evaluate import evaluate_all
    from stokes.physics.materials.providers import builtin

    properties: dict[str, dict[str, Any]] = {k: dict(v) for k, v in found["properties"].items()}
    definitions = builtin.definitions_of(found)
    for prop, form in overrides.items():
        if form == properties[prop]["method"]:
            continue
        if form == "constant":
            at: Array = np.array([state.T_ref])
            value = evaluate_all(definitions, state, [prop], at, np.full_like(at, state.p_ref))
            properties[prop] = {"method": "constant", "value": float(value[prop][0])}
        else:
            grid: Array = np.linspace(state.T_min, state.T_max, LIBRARY_POINTS)
            table = evaluate_all(definitions, state, [prop], grid, np.full_like(grid, state.p_ref))
            original = found["properties"][prop]
            properties[prop] = {
                "method": "piecewise_linear",
                "table_temperatures": [float(t) for t in grid],
                "table_values": [float(v) for v in table[prop]],
                **{k: original[k] for k in ("valid_min", "valid_max") if k in original},
            }
    return properties


def record_metas(
    found: dict[str, Any],  # Any: a catalogue record
    properties: dict[str, dict[str, Any]],  # Any: JSON property entries
    state: State,
) -> dict[str, dict[str, Any]]:  # Any: JSON meta values
    """Each property's provenance: ``library`` and its ``signature`` always, ``fit`` for a
    fluid fitted to CoolProp."""
    from stokes.physics.materials.library import signature_of_entry

    generated = found.get("generated") or {}
    citations = found.get("references") or {}
    metas: dict[str, dict[str, Any]] = {}
    for prop, entry in properties.items():
        original = found["properties"][prop]["method"]
        library: dict[str, Any] = {"provider": "builtin", "record": found["id"], "method": original}
        if prop in citations:
            library["reference"] = citations[prop]
        elif generated:
            library["reference"] = generated.get("references", {})
        meta: dict[str, Any] = {"library": library, "signature": signature_of_entry(entry)}
        fit = generated.get("fits", {}).get(prop)
        if entry["method"] != original:
            library["derived"] = {
                "method": entry["method"],
                "T": state.T_ref if entry["method"] == "constant" else [state.T_min, state.T_max],
            }
        elif fit is not None:
            meta["fit"] = {
                "provider": generated["provider"],
                "provider_version": generated["version"],
                "fluid": generated["fluid"],
                "composition": "",
                "basis": "",
                "phase": generated["phase"],
                "p_ref": generated["p"],
                "T_min": found["state"]["T_min"],
                "T_max": found["state"]["T_max"],
                "points": generated["points"],
                "method": fit["method"],
                "degree": fit["degree"],
                "max_rel_err": fit["max_rel_err"],
                "rms_rel_err": fit["rms_rel_err"],
                "tolerance": found["state"].get("tolerance", 0.01),
                "generated": generated["date"],
                "reference": generated.get("references", {}),
            }
        metas[prop] = meta
    return metas


def library_provider(name: str) -> tuple[Any, str]:  # Any: a Provider
    """The provider and the version a fit records (REFPROP's own for ``refprop``)."""
    from stokes.physics.materials.providers import get_provider, remote

    provider = get_provider(name)
    if name == "refprop":
        detection = remote.refprop_detection() or {}
        return provider, str(detection.get("version") or "")
    return provider, remote.package_version(name)


def stand_ins(info: LibraryInfo, phase: str) -> dict[str, float]:
    """Constant values a property gets until its fit lands, or when the provider lacks it: the
    library's value at T_ref, else the built-in template's (a missing property is flagged)."""
    from stokes.physics.materials.authoring import template
    from stokes.physics.materials.fit import FITTED_PROPERTIES

    at_ref = info.detail.get("values_at_ref") or {}
    fallback = template("liquid" if phase == "liquid" else "gas")["properties"]
    values: dict[str, float] = {}
    for prop in FITTED_PROPERTIES:
        value = at_ref.get(prop)
        if isinstance(value, int | float) and math.isfinite(value) and value > 0.0:
            values[prop] = float(value)
        elif "value" in fallback[prop]:
            values[prop] = float(fallback[prop]["value"])
        else:
            values[prop] = 1.0
    return values


def library_state(
    provider: str,
    fluid: str,
    composition: str,
    basis: str,
    phase: str,
    info: LibraryInfo,
    given: dict[str, float | None],
    tolerance: float,
    max_degree: int,
) -> LibraryState:
    from stokes.physics.materials.library import LibraryState, default_range, fit_phase

    t_ref = given["T_ref"] if given["T_ref"] is not None else 300.0
    p_ref = given["p_ref"] if given["p_ref"] is not None else 101325.0
    t_min, t_max = given["T_min"], given["T_max"]
    if t_min is None or t_max is None:
        low, high = default_range(provider, fit_phase(phase, info), info, t_ref)
        t_min = low if t_min is None else t_min
        t_max = high if t_max is None else t_max
    return LibraryState(
        provider=provider,
        fluid=fluid,
        composition=composition,
        basis=basis,
        phase=phase,
        T_ref=t_ref,
        p_ref=p_ref,
        T_min=t_min,
        T_max=t_max,
        tolerance=tolerance,
        max_degree=max_degree,
    )
