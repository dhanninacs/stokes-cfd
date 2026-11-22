"""The flow-split manifold, built and optimised headless through the API (ADR-0040).

    python tools/manifold_demo.py build <folder>       the project, saved
    python tools/manifold_demo.py solve <folder>       mesh and solve it as it is; port flows
    python tools/manifold_demo.py demo  <folder> <report.json> [max_cycles]
        build, noise floor, shape.optimise (flow_split, finite differences), apply the best
        point, solve it, and the final design and the baseline on a 0.7x mesh; the report is
        rewritten after every stage.
    python tools/manifold_demo.py finish <folder> <report.json>
        the stages after the optimisation, from its report (apply, solve, 0.7x mesh).

A header with one inlet pipe and four outlet pipes, made only of implicit operations (boxes,
cylinders, a linear pattern, a blended union and a sharp clip), so every tagged plane is a face of
the clip box and does not move with the variables. The header's last three segments have the
heights ``h2``, ``h3`` and ``h4``, project parameters the optimiser moves to make the four outlet
flows equal.

Millimetres below; x runs along the header, the outlets leave towards -y, z is the depth (8 mm,
walls on both sides). **The pipes are round on purpose:** a planar cap with straight edges is a
handful of triangles after the display mesh is simplified, and the face grouping merges a group of
four triangles or fewer into its neighbour as a sliver (measured 2026-11-14 on a box stub and on
symmetry planes cut through round stubs), so no tag could name it. A disk keeps its curved edge.

Every step is a ``session.execute`` call, so the project is what a journal of the same commands
would build, and the GUI can build it the same way.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from stokes.api.session import Session

HEADER = 16.0
"""The fixed height of the first header segment, and the start of h2, h3 and h4 (mm)."""
BOUNDS = (6.0, 26.0)
"""The heights' bounds (mm): six cells across at 1 mm at the least. Above the first segment's 16 mm
on purpose: the laminar header loses pressure along its length, so the first outlets take more, and
only a deeper downstream header (less friction) can even it out (measured at the start point)."""
FIRST = (10.0, 45.0)
"""The fixed header segment's x range (mm)."""
SEGMENTS = ((45.0, 75.0, "h2"), (75.0, 105.0, "h3"), (105.0, 130.0, "h4"))
"""The variable segments: x from, x to, the parameter giving the height."""
INLET_RADIUS = 4.0
INLET_PIPE = (-15.0, 12.0)
"""The inlet pipe's x range (mm), on the header's mid-height; the clip cuts it at x = 0."""
OUTLET_RADIUS = 3.5
FIRST_OUTLET = 30.0
PITCH = 30.0
OUTLETS = 4
DEPTH = 8.0
"""The header's depth along z (mm)."""
CLIP = ((0.0, 135.0), (-20.0, 30.0), (-10.0, 10.0))
"""The clip box: the inlet plane x = 0 and the outlet plane y = -20; it does not touch z."""
BLEND = 2.0
"""The union's blend radius (mm): rounds the steps and the pipes' junctions."""
INLET_SPEED = "0.2 [m/s]"
BASE_SIZE = 1.0
"""The snappyHexMesh base cell (mm)."""
TAGS = ("inlet", "out1", "out2", "out3", "out4", "walls")


def _mm(value: float) -> str:
    return f"{value:g} [mm]"


def _box(session: Session, name: str, low: tuple[Any, ...], high: tuple[Any, ...]) -> str:
    """An implicit box between two corners (numbers in mm, or expressions); its part id."""
    size: list[Any] = []
    position: list[Any] = []
    for lo, hi in zip(low, high, strict=True):
        if isinstance(lo, str) or isinstance(hi, str):
            # Only the top of a variable segment is an expression: its bottom is y = 0.
            size.append(f"{hi}")
            position.append(f"{hi} / 2")
        else:
            size.append(_mm(hi - lo))
            position.append(_mm((hi + lo) / 2))
    info = session.execute(
        "operation.create",
        type="implicit_primitive",
        params={"kind": "box", "size": size, "position": position},
        name=name,
        wait=True,
    )
    if info.get("status") != "ok":
        raise SystemExit(f"{name}: {info.get('error')}")
    return str(info["outputs"][0])


def _op(session: Session, op_type: str, name: str, inputs: list[str], **params: Any) -> str:
    info = session.execute(
        "operation.create", type=op_type, inputs=inputs, params=params, name=name, wait=True
    )
    if info.get("status") != "ok":
        raise SystemExit(f"{name}: {info.get('error')}")
    return str(info["outputs"][0])


def _cylinder(
    session: Session, name: str, radius: float, length: float, axis: str, at: tuple[float, ...]
) -> str:
    info = session.execute(
        "operation.create",
        type="implicit_primitive",
        params={
            "kind": "cylinder",
            "radius": _mm(radius),
            "length": _mm(length),
            "axis": axis,
            "position": [_mm(v) for v in at],
        },
        name=name,
        wait=True,
    )
    if info.get("status") != "ok":
        raise SystemExit(f"{name}: {info.get('error')}")
    return str(info["outputs"][0])


def build_geometry(session: Session) -> str:
    """The parameters and the implicit operations; the fluid part's id."""
    for _, _, name in SEGMENTS:
        session.execute("parameter.define", name=name, dimension="length", value=_mm(HEADER))
    half = DEPTH / 2
    first = _box(session, "S1", (FIRST[0], 0.0, -half), (FIRST[1], HEADER, half))
    segments = [
        _box(session, f"S{i + 2}", (x0, 0.0, -half), (x1, name, half))
        for i, (x0, x1, name) in enumerate(SEGMENTS)
    ]
    x0, x1 = INLET_PIPE
    inlet = _cylinder(
        session, "inlet_pipe", INLET_RADIUS, x1 - x0, "x", ((x0 + x1) / 2, HEADER / 2, 0.0)
    )
    stub = _cylinder(session, "outlet_pipe", OUTLET_RADIUS, 32.0, "y", (FIRST_OUTLET, -14.0, 0.0))
    outlets = _op(
        session,
        "implicit_pattern",
        "outlets",
        [stub],
        kind="linear",
        axis="x",
        count=OUTLETS,
        pitch=_mm(PITCH),
    )
    union = _op(
        session,
        "implicit_boolean",
        "header",
        [first],
        op="union",
        tools=[*segments, inlet, outlets],
        blend_radius=_mm(BLEND),
    )
    (cx0, cx1), (cy0, cy1), (cz0, cz1) = CLIP
    clip = _box(session, "clip", (cx0, cy0, cz0), (cx1, cy1, cz1))
    return _op(session, "implicit_boolean", "manifold", [union], op="intersection", tools=[clip])


def classify_faces(session: Session, part: str) -> dict[str, list[int]]:
    """The faces of each tag, found by where they are: the inlet at x = 0, the outlets at
    y = -20 from the lowest x up, the walls the rest."""
    from stokes.geometry.retag import faces_of
    from stokes.geometry.store import GeometryStore

    mesh = GeometryStore.of(session).mesh(part)
    if mesh is None:
        raise SystemExit("the manifold has no mesh")
    faces, _ = faces_of(mesh)
    tol = 1e-4
    out: dict[str, list[int]] = {name: [] for name in TAGS}
    caps: list[tuple[float, int]] = []
    for i, f in faces.items():
        (x, y, _), n = f.centroid, f.normal
        if n[0] < -0.99 and abs(x - CLIP[0][0] / 1000) < tol:
            out["inlet"].append(i)
        elif n[1] < -0.99 and abs(y - CLIP[1][0] / 1000) < tol:
            caps.append((x, i))
        else:
            out["walls"].append(i)
    caps.sort()
    if len(caps) != OUTLETS:
        raise SystemExit(f"expected {OUTLETS} outlet caps, found {len(caps)}")
    for k, (_, i) in enumerate(caps):
        out[f"out{k + 1}"] = [i]
    for name, ids in out.items():
        if not ids:
            raise SystemExit(f"no face for {name}")
    return out


def build(folder: Path, base_size: float = BASE_SIZE) -> Session:
    """The whole case: geometry, air, the boundaries, the mesh and solver settings; saved."""
    from stokes.api.session import Session

    session = Session()
    session.execute("project.new", path=str(folder), name="manifold")
    part = build_geometry(session)
    session.execute("project.save")
    faces = classify_faces(session, part)
    air = session.execute("material.add_from_library", provider="builtin", fluid="air")
    session.execute("region.assign", material=air["name"], target=[{"part": part, "faces": "all"}])

    def assign(name: str, kind: str, values: dict[str, Any]) -> None:
        session.execute(
            "boundary.assign",
            target=[{"part": part, "faces": faces[name]}],
            kind=kind,
            values=values,
            name=name,
        )

    assign("inlet", "velocity_inlet", {"velocity": INLET_SPEED})
    for k in range(OUTLETS):
        assign(f"out{k + 1}", "pressure_outlet", {"pressure": "0 [Pa]"})
    assign("walls", "wall", {})
    session.execute("mesh.configure", values={})
    session.execute("mesh.body_role", part=part, role="fluid")
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={
            "base_size": base_size / 1000.0,
            "surface_size": base_size / 1000.0,
            "layers": 0,
            "cores": 1,
        },
    )
    session.execute(
        "solve.configure",
        values={
            "engine": "of14.incompressibleFluid",
            "turbulence": "laminar",
            "iterations": 2000,
            "residual_target": 1e-5,
            "write_interval": 2000,
            "cores": 1,
        },
    )
    session.execute("project.save")
    return session


def fluid_part(session: Session) -> str:
    """The part the ``manifold`` operation made."""
    project = session.require_project()
    for node in project.root.walk():
        if node.type == "implicit_boolean" and node.name == "manifold":
            return str(project.get(str(node.params["outputs"][0])).id)
    raise SystemExit("no manifold operation in the project")


def solve_once(folder: Path) -> dict[str, Any]:  # Any: JSON
    """Mesh and solve the project as it is (the ordinary engine), and read the port flows."""
    from stokes.api.session import Session
    from stokes.run.hosts import HostRegistry

    session = Session()
    session.execute("project.open", path=str(folder))
    began = time.monotonic()
    meshed = session.execute("mesh.generate", wait=True)
    mesh_seconds = time.monotonic() - began
    if meshed.get("state") != "done":
        raise SystemExit(f"meshing ended {meshed.get('state')}: {meshed}")
    run = session.execute("run.start", wait=True)
    solve_seconds = time.monotonic() - began - mesh_seconds
    status = session.execute("run.status")
    runner = HostRegistry.of(session).runner(str(status.get("host") or "local"))
    found: dict[str, Any] = runner.call(
        "job.sensitivity", job=str(run["job"]), nu=1.5e-5, adjoint=False, timeout=600.0
    )
    session.execute("project.save")
    return {
        "mesh": {k: meshed.get(k) for k in ("state", "cells", "result")},
        "mesh_seconds": round(mesh_seconds, 1),
        "run": {k: run.get(k) for k in ("state", "run", "job")},
        "solve_seconds": round(solve_seconds, 1),
        "status": {k: status.get(k) for k in ("converged", "iterations", "time", "cells")},
        "time": found.get("time"),
        "flows": found.get("flows"),
    }


def _cycle_numbers(found: Any) -> dict[str, Any]:  # Any: CycleResult -> JSON
    from stokes.explore.shape import flow_split

    split = flow_split(found.flows, found.outlets)
    return {
        **split.to_json(),
        "flows": dict(found.flows),
        "pressure_loss": found.objective,
        "inlet_pressure": (found.detail.get("pressures") or {}).get("inlet"),
        **{k: found.detail.get(k) for k in ("mass_balance", "converged", "iterations", "cells")},
    }


def _assign(session: Session, heights: list[float]) -> None:
    """Set the heights and find the boundary tags again on the rebuilt part.

    **The tags must be found again** (measured 2026-11-15): an implicit part's face ids are those
    of its last run, so after ``parameter.assign`` every tag on it is stale and ``mesh.generate``
    refuses with "picked on an older version of the part". The faces are found by where they are
    and given back with ``tag.rematch``, as a shape cycle does on its private copy."""
    from stokes.geometry.carry import tags_of
    from stokes.geometry.opstore import OperationStore

    values = {name: v for (_, _, name), v in zip(SEGMENTS, heights, strict=True)}
    session.execute("parameter.assign", values=values)
    session.settle()
    OperationStore.of(session).wait()
    # **Every implicit operation is run again, in tree order**, as a shape cycle does: measured
    # 2026-11-15, a project just opened kept its old surface after parameter.assign (the 0.7x
    # baseline was meshed on the final design's 26 mm header with h = 16 mm in the tree).
    from stokes.geometry.model import OPERATIONS_PATH, is_implicit_operation

    group = session.require_project().find(OPERATIONS_PATH)
    for op in list(group.walk()) if group is not None else []:
        if is_implicit_operation(op.type):
            session.execute("operation.run", op=op.id, wait=True)
    part = fluid_part(session)
    faces = classify_faces(session, part)
    for tag in tags_of(session.require_project()):
        if tag.name in faces:
            session.execute(
                "tag.rematch", tag=tag.id, result=[{"part": part, "faces": faces[tag.name]}]
            )


def demo(folder: Path, report_path: Path, max_cycles: int = 8) -> None:
    """The whole headless proof (ADR-0040), stage by stage, the report rewritten after each."""
    import shutil
    import tempfile

    from stokes.api.shape_cycle import AdjointCycle
    from stokes.explore.shape import as_cycle_result

    report: dict[str, Any] = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}  # Any: JSON
    clock = time.monotonic()

    def save(stage: str) -> None:
        report["stage"] = stage
        report["wall_clock_s"] = round(time.monotonic() - clock, 1)
        report_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(f"[{report['wall_clock_s']:>8}s] {stage}", flush=True)

    if folder.exists():
        shutil.rmtree(folder)
    session = build(folder)
    part = fluid_part(session)
    report["design"] = {
        "inlet_speed": INLET_SPEED,
        "start_heights_mm": HEADER,
        "bounds_mm": BOUNDS,
        "base_size_mm": BASE_SIZE,
        "outlet_radius_mm": OUTLET_RADIUS,
        "inlet_radius_mm": INLET_RADIUS,
    }
    save("built")

    # V0 and V1: the baseline twice (remeshed from scratch each time), then h2 + 0.05 mm.
    work = Path(tempfile.mkdtemp(prefix="manifold-noise-", dir=folder.parent))
    probe = AdjointCycle(session, part, work, adjoint=False)
    project = session.require_project()
    runs = []
    for label, h2 in (("baseline", HEADER), ("repeat", HEADER), ("h2+0.05mm", HEADER + 0.05)):
        began = time.monotonic()
        found = as_cycle_result(probe(_with_heights(project, {"h2": h2 / 1000})))
        seconds = round(time.monotonic() - began, 1)
        runs.append({"run": label, "seconds": seconds, **_cycle_numbers(found)})
        report["noise"] = runs
        save(f"noise: {label}")
    j = [float(r["objective"]) for r in runs]
    scatter = abs(j[1] - j[0])
    report["baseline"] = runs[0]
    report["noise_floor"] = {
        "J_repeat_scatter": scatter,
        "J_relative_scatter": scatter / abs(j[0]) if j[0] else None,
        "J_change_h2_plus_0.05mm": j[2] - j[0],
    }
    noise = max(scatter / abs(j[0]) if j[0] else 0.0, 1e-4)
    shutil.rmtree(work, ignore_errors=True)
    save("noise floor measured")

    lo, hi = (b / 1000 for b in BOUNDS)
    variables = [{"reference": n, "lower": lo, "upper": hi} for _, _, n in SEGMENTS]
    cycles: list[dict[str, Any]] = []  # Any: JSON

    def heard(event: str, data: dict[str, Any]) -> None:  # Any: JSON
        cycles.append({k: data.get(k) for k in ("index", "x", "objective", "seconds", "detail")})
        report["cycles"] = cycles
        save(f"cycle {data.get('index')}")

    session.events.subscribe("shape.cycle", heard)
    began = time.monotonic()
    result = session.execute(
        "shape.optimise",
        part=part,
        objective="flow_split",
        gradient="finite_difference",
        variables=variables,
        fd_step=[0.001] * len(variables),
        max_cycles=max_cycles,
        noise_floor=noise,
        wait=True,
    )
    keys = ("status", "message", "best", "improvement", "significant", "noise_floor", "target")
    report["optimisation"] = {k: result.get(k) for k in keys}
    report["optimisation"]["seconds"] = round(time.monotonic() - began, 1)
    report["optimisation"]["cycles"] = len(result.get("cycles") or [])
    report["optimisation"]["solves"] = sum(
        int((c.get("detail") or {}).get("solves", 1)) for c in result.get("cycles") or []
    )
    save("optimised")

    best = [float(v) for v in result["best"]["x"]]
    _assign(session, best)
    session.execute("project.save")
    _close(session)
    finish(folder, report_path, report, best, clock)


def finish(
    folder: Path,
    report_path: Path,
    report: dict[str, Any],  # Any: JSON
    best: list[float],
    clock: float,
) -> None:
    """The applied design solved in the live project, then the final design and the baseline on a
    0.7x mesh (V4). Also runs alone on a finished optimisation (``finish``)."""
    import shutil

    def save(stage: str) -> None:
        report["stage"] = stage
        report["wall_clock_s"] = round(time.monotonic() - clock, 1)
        report_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(f"[{report['wall_clock_s']:>8}s] {stage}", flush=True)

    session = Session()
    session.execute("project.open", path=str(folder))
    _assign(session, best)
    session.execute("project.save")
    _close(session)
    report["final_heights_mm"] = [round(v * 1000, 3) for v in best]
    final = solve_once(folder)
    report["final_solve"] = final
    report["final"] = _split_of(final["flows"])
    save("final design solved in the live project")

    # V4: the final design and the baseline on a 0.7x mesh.
    fine: dict[str, Any] = {}  # Any: JSON
    for label, heights in (("final", best), ("baseline", [HEADER / 1000] * 3)):
        target = folder.parent / f"{folder.stem}_fine_{label}.stokes"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(folder, target, ignore=shutil.ignore_patterns("runs"))
        other = Session()
        other.execute("project.open", path=str(target))
        _assign(other, heights)
        size = 0.7 * BASE_SIZE / 1000
        other.execute("mesh.configure", values={"base_size": size, "surface_size": size})
        other.execute("project.save")
        _close(other)
        solved = solve_once(target)
        fine[label] = {**_split_of(solved["flows"]), "cells": solved["mesh"].get("cells")}
        report["mesh_independence"] = fine
        save(f"0.7x mesh: {label}")
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save("done")


def _with_heights(project: Any, heights: dict[str, float]) -> Any:  # Any: Project
    import copy

    from stokes.model.project import Project

    data = Project.from_json(copy.deepcopy(project.to_json()), node_registry=project.registry)
    for name, value in heights.items():
        node, param = data.resolve_param(name)
        data.set_param(node.id, param, value)
    return data


def _split_of(flows: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    from stokes.explore.shape import flow_split

    plain = {k: float(v["flow"]) for k, v in flows.items()}
    split = flow_split(plain, [f"out{k + 1}" for k in range(OUTLETS)]).to_json()
    inflow = -sum(q for q in plain.values() if q < 0)
    split["mass_balance"] = abs(sum(plain.values())) / inflow if inflow else None
    split["inlet_pressure"] = flows.get("inlet", {}).get("pressure")
    return split


def _close(session: Any) -> None:  # Any: Session
    from stokes.geometry.store import GeometryStore

    GeometryStore.of(session).close()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    action, folder = argv[1], Path(argv[2]).resolve()
    began = time.monotonic()
    if action == "build":
        session = build(folder)
        part = fluid_part(session)
        print(json.dumps({"part": part, "seconds": round(time.monotonic() - began, 1)}))
        return 0
    if action == "finish":
        report_path = Path(argv[3]).resolve()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        best = [float(v) for v in report["optimisation"]["best"]["x"]]
        finish(folder, report_path, report, best, time.monotonic())
        return 0
    if action == "demo":
        demo(folder, Path(argv[3]).resolve(), int(argv[4]) if len(argv) > 4 else 8)
        return 0
    if action == "solve":
        found = solve_once(folder)
        print(json.dumps(found, indent=1))
        return 0
    print(f"unknown action {action!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
