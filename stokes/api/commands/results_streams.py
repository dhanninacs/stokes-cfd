"""`results.streams`: each fluid stream's flow, pressure drop and heat, from a run's monitors.

A run records, per inlet and outlet, the flow through it and its mean pressure and temperature
(`run.status` → `monitors`). A heat exchanger's questions are about streams, not patches: how much
each stream's pressure falls from its inlets to its outlets, and how much heat it gains or loses.
This command groups the openings by the region they belong to (each fluid region is a stream,
ADR-0042) and answers both, with the heat from the region material's own specific heat:

    Q = sum over outlets of (m cp T)  -  sum over inlets of (m cp T)

positive when the stream is heated. A project of one fluid is one stream.

Read-only, headless: the materials are asked through `material.evaluate`, as the solve snapshot
asks them.
"""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError

#: The boundary kinds that bring a stream in, and the one that lets it out.
INLETS = ("velocity_inlet", "mass_flow_inlet", "pressure_inlet")
OUTLETS = ("pressure_outlet",)


@command(
    "results.streams",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
    ),
    mutates=False,
)
def results_streams(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:
    """Each fluid region's inlets and outlets, mass flow, mean inlet and outlet pressure and
    temperature, pressure drop and heat gained (W, positive heats the stream), and the balance
    of the heat the streams gained against what they lost."""
    from stokes.api.commands.run import _store

    status = _store(ctx).status(run)
    monitors = status.get("monitors") or {}
    if not isinstance(monitors, dict) or not monitors:
        raise ValidationError(
            "this run has no inlet or outlet monitors yet; finish the run first",
            code="results.no_monitors",
        )
    project = ctx.session.require_project()
    openings = _openings(project)
    energy = str(status.get("engine") or "") != "of14.incompressibleFluid"
    streams: list[dict[str, Any]] = []
    for region, found in openings.items():
        inlets = [p for p in found["inlets"] if isinstance(monitors.get(p), dict)]
        outlets = [p for p in found["outlets"] if isinstance(monitors.get(p), dict)]
        if not inlets or not outlets:
            continue
        stream = _stream(ctx, region, found["material"], inlets, outlets, monitors, energy)
        streams.append(stream)
    heated = sum(s["heat"] for s in streams if s.get("heat") is not None and s["heat"] > 0)
    cooled = -sum(s["heat"] for s in streams if s.get("heat") is not None and s["heat"] < 0)
    balance: dict[str, Any] = {}
    if heated and cooled:
        balance = {
            "gained": heated,
            "lost": cooled,
            "imbalance": abs(heated - cooled) / max(heated, cooled),
        }
    return {
        "run": str(status.get("id") or run or ""),
        "streams": streams,
        "balance": balance,
        "units": {
            "mass_flow": "kg/s" if energy else "m3/s",
            "pressure": "Pa" if energy else "m2/s2",
            "temperature": "K",
            "heat": "W",
        },
    }


def _openings(project: Any) -> dict[str, dict[str, Any]]:
    """Region name -> its inlet and outlet patch names and its material id, from the tree.

    A boundary's patch is named after its tag (the mesh names patches after tags), and its region
    is the region node it sits under.
    """
    found: dict[str, dict[str, Any]] = {}
    for node in project.root.walk():
        if node.type != "region" or str(node.params.get("kind") or "fluid") == "solid":
            continue
        entry = found.setdefault(
            str(node.name),
            {"inlets": [], "outlets": [], "material": str(node.params.get("material") or "")},
        )
        for child in node.walk():
            if child.type != "boundary":
                continue
            tag = project.find(str(child.params.get("tag") or ""))
            patch = str(tag.name) if tag is not None else str(child.name)
            kind = str(child.params.get("kind") or "")
            if kind in INLETS:
                entry["inlets"].append(patch)
            elif kind in OUTLETS:
                entry["outlets"].append(patch)
    return found


def _stream(
    ctx: CommandContext,
    region: str,
    material: str,
    inlets: list[str],
    outlets: list[str],
    monitors: dict[str, Any],
    energy: bool,
) -> dict[str, Any]:
    """One stream's numbers. Means over several openings are weighted by each one's flow."""

    def flow(patch: str) -> float:
        return abs(float(monitors[patch].get("flow_rate") or 0.0))

    def mean(patches: list[str], key: str) -> float | None:
        pairs = [(flow(p), monitors[p].get(key)) for p in patches]
        weighed: list[tuple[float, float]] = [
            (m, float(v)) for m, v in pairs if isinstance(v, (int, float))
        ]
        pairs = weighed
        total = sum(m for m, _v in pairs)
        if not pairs or total <= 0.0:
            return None
        return float(sum(m * v for m, v in pairs) / total)

    mass_in = sum(flow(p) for p in inlets)
    mass_out = sum(flow(p) for p in outlets)
    p_in, p_out = mean(inlets, "p_mean"), mean(outlets, "p_mean")
    t_in, t_out = mean(inlets, "T_mean"), mean(outlets, "T_mean")
    out: dict[str, Any] = {
        "region": region,
        "inlets": inlets,
        "outlets": outlets,
        "mass_flow": mass_in,
        "mass_flow_out": mass_out,
        "p_in": p_in,
        "p_out": p_out,
        "pressure_drop": None if p_in is None or p_out is None else p_in - p_out,
        "T_in": t_in,
        "T_out": t_out,
        "cp": None,
        "heat": None,
    }
    if energy and t_in is not None and t_out is not None and material:
        cp = _specific_heat(ctx, material, 0.5 * (t_in + t_out))
        if cp is not None:
            out["cp"] = cp
            out["heat"] = cp * (
                sum(flow(p) * float(monitors[p].get("T_mean") or t_out) for p in outlets)
                - sum(flow(p) * float(monitors[p].get("T_mean") or t_in) for p in inlets)
            )
    return out


def _specific_heat(ctx: CommandContext, material: str, temperature: float) -> float | None:
    """The material's specific heat at the stream's mean temperature, J/(kg K), or None."""
    from stokes.run.snapshot import _scalar

    try:
        found = ctx.session.execute(
            "material.evaluate", material=material, properties=["specific_heat"], T=[temperature]
        )
        value = _scalar(found, "specific_heat")
    except Exception:
        return None
    return float(value) if value else None
