"""`results.streams`: each fluid stream's flow, pressure drop and heat, from a run's monitors.

A run records, per inlet and outlet, the flow through it, its area-averaged pressure and its
mass-flow-weighted (bulk) temperature (`run.status` → `monitors`). A heat exchanger's questions
are about streams, not patches: how much each stream's pressure falls from its inlets to its
outlets, and how much heat it gains or loses.
This command groups the openings by the region they belong to (each fluid region is a stream,
ADR-0042) and answers both, with the heat from the region material's own specific heat:

    Q = sum over outlets of (m cp T)  -  sum over inlets of (m cp T)

positive when the stream is heated. A project of one fluid is one stream.

**The flow's sign** (ADR-0022 addendum 2026-11-24). The solver's flow rate through a patch is
positive out of the domain, because its face normals point out, so an inlet's is negative. Stokes
shows each opening's flow in its own direction instead: **into the domain at an inlet, out of it
at an outlet**, both positive in normal running (`directed_flow`). A negative number then means
the flow goes the other way through that opening (backflow), which is what a user needs to see.
The stream's `mass_flow` is the sum over its inlets in that convention, `mass_flow_out` over its
outlets. `run.monitors` gives each table's `sign`, so a history is drawn the same way.

**A transient run's numbers follow the time shown** (`time`): `run.monitors_at` reads each
opening's monitor history once from the host (a finished job's tables cannot change, so the run
store keeps them for the session) and takes the row written nearest that time. Without `time` the
numbers are the run's last, as the record holds them.

Read-only, headless: the materials are asked through `material.evaluate`, as the solve snapshot
asks them.
"""

from __future__ import annotations

import bisect
import math
import re
from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError

#: The monitor a stream's temperatures and heat are read from: `T` on the opening weighted by its
#: mass flux (the bulk temperature). The area average (`T_area`) is not used: on the mini plate
#: exchanger it put the duty at 51.4 W against 77.8 W (measured, 2026-11-24). A run recorded
#: before the bulk monitor existed has no `T_bulk`, and no heat is given for it.
BULK = "T_bulk"

#: The boundary kinds that bring a stream in, and the one that lets it out.
INLETS = ("velocity_inlet", "mass_flow_inlet", "pressure_inlet")
OUTLETS = ("pressure_outlet",)

#: The function objects whose tables carry an opening's flow and its mean values.
FLOW_FUNCTION = "patchFlowRate("
AVERAGE_FUNCTION = "patchAverage("
_PATCH_RE = re.compile(r"patch=([^,)]+)")
_FIELDS_RE = re.compile(r"fields=\(([^)]*)\)")
#: The bulk-temperature monitor's folder (`bulkT_<patch>`, `stokes.compile.of14.fluid`), whose
#: rows are `time T_bulk`; under a region's folder in a multi-region case.
_BULK_RE = re.compile(r"(?:^|/)bulkT_([^/]+)/")

#: `{patch: {key: [(time, value)]}}`: an opening's monitor histories, sorted by time.
Tables = dict[str, dict[str, list[tuple[float, float]]]]


def directed_flow(raw: float, opening: str) -> float:
    """A flow rate in the opening's own direction: into the domain at an inlet, out at an outlet.

    `raw` is the solver's, positive out of the domain."""
    return -raw if opening == "inlet" else raw


def opening_of(project: Any) -> dict[str, str]:  # Any: Project
    """Patch name -> `inlet` or `outlet`, for every fluid region's openings."""
    found: dict[str, str] = {}
    for entry in _openings(project).values():
        for patch in entry["inlets"]:
            found[patch] = "inlet"
        for patch in entry["outlets"]:
            found.setdefault(patch, "outlet")
    return found


def monitor_patch(name: str) -> str:
    """The patch a monitor table is about (`patchFlowRate(patch=inlet)/0/...` -> `inlet`), or ""."""
    found = _PATCH_RE.search(name)
    return found.group(1) if found is not None else ""


def monitor_sign(name: str, openings: dict[str, str]) -> int:
    """-1 for an inlet's flow-rate table (drawn as the flow in), else 1."""
    if FLOW_FUNCTION not in name:
        return 1
    return -1 if openings.get(monitor_patch(name)) == "inlet" else 1


def _average_keys(name: str) -> tuple[str, ...]:
    """The keys of a `patchAverage` table's value columns: `p_mean`, then `T_area` with energy.

    OpenFOAM 14 drops the space in the directory name (`fields=(pT)`), so the fields are read as
    the executor writes them: `p`, and `T` when the energy writer averaged it too."""
    found = _FIELDS_RE.search(name)
    fields = found.group(1) if found is not None else "p"
    return ("p_mean", "T_area") if "T" in fields else ("p_mean",)


def _start_time(name: str) -> float:
    """The time folder a table was written under (`.../0.002/surfaceFieldValue.dat`), 0 if none."""
    parts = name.split("/")
    try:
        return float(parts[-2]) if len(parts) >= 2 else 0.0
    except ValueError:
        return 0.0


def tables_from(texts: dict[str, str]) -> Tables:
    """The opening's histories from the monitor files' text, keyed by the files' names.

    A table in several time folders (a resumed run) is joined and sorted by time; a later file's
    row at the same time wins."""
    from stokes.run.monitors import parse_table

    joined: dict[str, dict[str, dict[float, float]]] = {}
    for name, text in sorted(texts.items(), key=lambda item: _start_time(item[0])):
        bulk = _BULK_RE.search(name)
        patch = bulk.group(1) if bulk is not None else monitor_patch(name)
        if patch == "":
            continue
        if bulk is not None:
            keys: tuple[str, ...] = (BULK,)
        elif FLOW_FUNCTION in name:
            keys = ("flow_rate",)
        elif AVERAGE_FUNCTION in name:
            keys = _average_keys(name)
        else:
            continue
        table = parse_table(text)
        if len(table.columns) < 1 + len(keys):
            continue
        times = table.values[0]
        columns = table.values[-len(keys) :]
        entry = joined.setdefault(patch, {})
        for key, column in zip(keys, columns, strict=True):
            rows = entry.setdefault(key, {})
            for t, v in zip(times, column, strict=True):
                if math.isfinite(t) and math.isfinite(v):
                    rows[float(t)] = float(v)
    return {
        patch: {key: sorted(rows.items()) for key, rows in entry.items()}
        for patch, entry in joined.items()
    }


def values_at(tables: Tables, time: float) -> tuple[float | None, dict[str, dict[str, float]]]:
    """Each opening's values at the row written nearest `time`, and that row's time.

    The time returned is the one furthest from `time` among those used, so a caller can say
    honestly how close the numbers are to the time shown."""
    monitors: dict[str, dict[str, float]] = {}
    used: float | None = None
    for patch, entry in tables.items():
        values: dict[str, float] = {}
        for key, rows in entry.items():
            if not rows:
                continue
            at = bisect.bisect_left(rows, (time, -math.inf))
            near = [rows[i] for i in (at - 1, at) if 0 <= i < len(rows)]
            t, v = min(near, key=lambda row: abs(row[0] - time))
            values[key] = v
            if used is None or abs(t - time) > abs(used - time):
                used = t
        if values:
            monitors[patch] = values
    return used, monitors


def _read_tables(runner: Any, job: str) -> Tables:  # Any: a host runner
    """Every opening's monitor table of a job, read from its host."""
    from stokes.run.monitors import MAX_TABLE_BYTES, is_table, monitor_name

    reply = runner.call("job.outputs", job=job, timeout=60.0)
    files = reply.get("files") if isinstance(reply, dict) else None
    texts: dict[str, str] = {}
    for item in files or []:
        path = str(item.get("path") or "") if isinstance(item, dict) else ""
        name = monitor_name(path)
        wanted = FLOW_FUNCTION in name or AVERAGE_FUNCTION in name or _BULK_RE.search(name)
        if not is_table(path) or not wanted:
            continue
        if name.startswith(("/", "\\")) or ".." in name.split("/"):
            continue
        read = runner.call(
            "job.read",
            job=job,
            path=f"postProcessing/{name}",
            max_bytes=MAX_TABLE_BYTES,
            timeout=60.0,
        )
        texts[name] = str(read.get("text") or "") if isinstance(read, dict) else ""
    return tables_from(texts)


def monitors_at(ctx: CommandContext, run: str | None, time: float) -> dict[str, Any]:
    """`run.monitors_at`'s answer: each opening's flow and mean values at a written time."""
    from stokes.api.commands.run import _run_host, _store

    if not math.isfinite(time):
        raise ValidationError("time must be a finite number", code="run.bad_time")
    found, runner = _run_host(ctx, run)
    store = _store(ctx)
    tables: Tables | None = store.monitor_tables(found.id)
    if tables is None:
        tables = _read_tables(runner, found.job)
        if found.final:
            store.keep_monitor_tables(found.id, tables)
    sampled, values = values_at(tables, float(time))
    openings = opening_of(ctx.session.require_project())
    monitors: dict[str, dict[str, Any]] = {}
    for patch, entry in values.items():
        out: dict[str, Any] = dict(entry)
        opening = openings.get(patch, "")
        out["opening"] = opening
        if "flow_rate" in entry:
            out["flow"] = directed_flow(entry["flow_rate"], opening)
        monitors[patch] = out
    return {"run": found.id, "time": float(time), "sampled": sampled, "monitors": monitors}


@command(
    "run.monitors_at",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef("time", "number", description="The time (s) to read the openings' values at."),
    ),
    mutates=False,
)
def run_monitors_at(ctx: CommandContext, time: float, run: str | None = None) -> dict[str, Any]:
    """Each inlet's and outlet's flow and mean pressure (and temperature) at a written time.

    `{run, time, sampled, monitors: {patch: {flow_rate, flow, opening, p_mean, T_area?, T_bulk?}}}`:
    `sampled` is the time of the rows used (the nearest written to `time`), `flow_rate` the
    solver's (positive out of the domain) and `flow` in the opening's own direction (into the
    domain at an inlet). For a transient run's results at the time on screen.
    """
    return monitors_at(ctx, run, float(time))


@command(
    "results.streams",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "time",
            "number",
            required=False,
            description="A written time (s): the numbers at it. Omit it for the run's last.",
        ),
    ),
    mutates=False,
)
def results_streams(
    ctx: CommandContext, run: str | None = None, time: float | None = None
) -> dict[str, Any]:
    """Each fluid region's inlets and outlets, mass flow, area-averaged inlet and outlet pressure,
    mass-flow-weighted (bulk) inlet and outlet temperature, pressure drop and heat gained (W,
    positive heats the stream, from the bulk temperatures), and the balance of the heat the
    streams gained against what they lost.

    `time` reads them at that written time (the nearest rows the monitors wrote; `time` in the
    reply is theirs); without it they are the run's last and `time` is null. Flows are in each
    opening's own direction (the module's sign convention)."""
    from stokes.api.commands.run import _store

    status = _store(ctx).status(run)
    sampled: float | None = None
    monitors: Any  # Any: the record's or `monitors_at`'s per-patch dicts
    if time is not None:
        at = monitors_at(ctx, run, float(time))
        monitors = at["monitors"]
        sampled = at["sampled"]
    else:
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
        "time": sampled,
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
    """One stream's numbers. Means over several openings are weighted by each one's flow.

    The flows are in each opening's own direction (`directed_flow`): `mass_flow` is what comes in
    through the inlets, `mass_flow_out` what leaves through the outlets."""

    def flow(patch: str) -> float:
        return abs(float(monitors[patch].get("flow_rate") or 0.0))

    def directed(patch: str, opening: str) -> float:
        return directed_flow(float(monitors[patch].get("flow_rate") or 0.0), opening)

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

    mass_in = sum(directed(p, "inlet") for p in inlets)
    mass_out = sum(directed(p, "outlet") for p in outlets)
    p_in, p_out = mean(inlets, "p_mean"), mean(outlets, "p_mean")
    t_in, t_out = mean(inlets, BULK), mean(outlets, BULK)
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
                sum(flow(p) * float(monitors[p].get(BULK) or t_out) for p in outlets)
                - sum(flow(p) * float(monitors[p].get(BULK) or t_in) for p in inlets)
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
