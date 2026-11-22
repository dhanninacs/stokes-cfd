"""`results.variables`: every variable a run's results can show, for a field picker.

The stored fields come from the run's field cache (every patch and the slice); the derived ones are
the built-in field functions of `stokes.post.variables`; the user's own are the saved field
functions. Each answer carries the group, label, symbol, units and one-line meaning a picker shows,
and the built-ins the run cannot compute carry `available: false` and the reason.

Read-only, from caches; the reference values for the pressure coefficient are computed once per
run's field file and kept (`results._references`).
"""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command

RUN = ParamDef("run", "string", required=False, description="The run; default: the current one.")


def stored_fields(header: dict[str, Any]) -> dict[str, tuple[str, int]]:  # Any: the cache header
    """Each field any patch or the slice holds, as `name -> (units, components)`."""
    units = header.get("units") or {}
    found: dict[str, tuple[str, int]] = {}
    entries: list[Any] = [p.get("fields") or {} for p in header.get("patches") or []]
    slice_entry = header.get("slice")
    if isinstance(slice_entry, dict):
        entries.append(slice_entry.get("fields") or {})
    for fields in entries:
        if not isinstance(fields, dict):
            continue
        for name, entry in fields.items():
            if name in found or not isinstance(entry, dict):
                continue
            components = int(entry.get("components") or 1)
            found[str(name)] = (str(units.get(name, "")), components)
    return found


@command("results.variables", params=(RUN,), mutates=False)
def results_variables(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:  # Any: JSON
    """Every variable the run's results can show, grouped and ordered for a picker.

    Answers `{run, kinematic, density, references, variables: [{name, label, symbol, group, units,
    description, source, available, why?}]}`. `source` is `run` (the solver or the agent wrote
    it), `derived` (a built-in formula) or `function` (a saved field function); `references` are
    the pressure coefficient's `U_ref` and `p_ref`, or null without an inlet and an outlet.
    """
    from stokes.api.commands.results import (
        _density,
        _functions,
        _kinematic,
        _references,
        _run_fields,
    )
    from stokes.post.variables import catalogue

    run_id, _data, header = _run_fields(ctx, run)
    stored = stored_fields(header)
    units = {name: unit for name, (unit, _n) in stored.items()}
    kinematic = _kinematic(units)
    density = _density(ctx, None)
    references = _references(ctx, run_id)
    from stokes.api.commands.results import _store

    record = _store(ctx).record(run_id)
    # An imported case's fields are read as they are; the agent derives nothing for it.
    derives = record is None or str(getattr(record, "engine", "") or "") != "of14.external"
    return {
        "run": run_id,
        "kinematic": kinematic,
        "density": density,
        "references": references,
        "variables": catalogue(
            stored,
            _functions(ctx),
            kinematic,
            density is not None,
            references is not None,
            derives=derives,
        ),
    }
