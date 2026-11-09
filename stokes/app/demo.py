"""The demo project: a real flanged pipe with real tags, a fluid region and typed boundaries.

**Generated at startup, never committed** (`CLAUDE.md` §6/§8: no binaries or generated artifacts in
the tree). The geometry comes from :mod:`stokes.app.demo_geometry`; this module builds the project
around it.

**What changed and why.** The demo used to be a hollow shell — `pipe`, `inlet_flange` and
`outlet_flange` were empty *groups* with no geometry, the tags were empty groups, and the boundaries
were empty groups. The viewport showed a synthetic sample mesh instead, which carries no face ids by
design (`stokes.geometry.sample`) and so cannot be tagged or given a condition either. The practical
effect was that everything M1 built looked broken on the default project: picking found nothing, no
glyph could be placed, `boundary.assign` had nothing to assign to and `setup.check` had nothing to
report. **A demo that cannot exercise the features is worse than no demo, because it reads as a
defect.**

So the demo now does what the owner would do by hand:

1. import the generated STEP, giving three real named bodies;
2. read each body's real face fingerprints and pick the inlet, outlet and wall faces **by their
   geometry** — not by hardcoded ids, which would silently name a different face if the fixture ever
   changed;
3. create real face tags from those ids;
4. give the pipe a fluid region with a material;
5. assign a typed `velocity_inlet` on the inlet, a `pressure_outlet` on the outlet and `wall` on the
   rest, so the Conditions panel, the glyphs, the tree rows and the readout all have live data;
6. define the parameters and the Explore study, as before.

**Order matters.** The geometry is imported and *waited for*, because every later step names faces
that only exist once the mesh has arrived. The app start waits here, before the window opens,
which is deliberate: a demo that fills in while the user watches is harder to trust than one that
is simply there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stokes.api import Session
from stokes.log import fields, get_logger

__all__ = ["build_demo"]

log = get_logger(__name__)

WATER = "water_liquid"
"""The demo's fluid: the catalogue id for liquid water (`materials.json` `records[].id`).

The catalogue is not loaded into a project automatically, so `build_demo` adds the record with
`material.add_from_library` first — 0.37 s measured. Without that step, `region.assign` fails with
"no material", because nothing has been added to the project yet. This caught me out once: the
catalogue ships as data, not as project nodes.
"""


@dataclass(frozen=True)
class _Faces:
    """The face ids one body's demo set-up needs."""

    inlet: int
    outlet: int
    wall: list[int]


def _part_ids(session: Session, names: tuple[str, ...]) -> list[str]:
    """The ids of the parts named ``names``, in tree order."""
    project = session.require_project()
    wanted = set(names)
    return [node.id for node in project.nodes() if node.type == "part" and node.name in wanted]


def _faces_of(store: Any, session: Session, part_id: str) -> _Faces | None:
    """The inlet, outlet and wall faces of one body, chosen from its own fingerprints.

    By geometry, not by literal id:

    * **inlet / outlet** — the faces at the extremes of the axial coordinate; for an axially aligned
      pipe those are its two ends. Ties on the axial position break toward the larger face, so a
      flange's wide end face wins over a narrow rim at the same station.
    * **wall** — every other face: the cylindrical surfaces and the flange rims.

    Returns None when the body has no mesh yet or has fewer than two faces, so the caller can leave
    the demo without conditions rather than fail — the app must still open.
    """
    import numpy as np

    from stokes.geometry.selection_cache import SelectionCache

    mesh = store.mesh(part_id)
    if mesh is None:
        return None
    # The same cache `tag.anchor` reads: fingerprints per face (area, centroid, normal).
    prints = SelectionCache.of(session).fingerprints({part_id: mesh}).get(part_id)
    if prints is None or prints.faces < 2:
        return None
    centroid = np.asarray(prints.centroid, dtype=np.float64)
    axial = centroid[:, 2]
    order = np.argsort(axial, kind="stable")
    inlet = int(order[0])
    outlet = int(order[-1])
    if inlet == outlet:
        return None
    wall = [index for index in range(prints.faces) if index not in (inlet, outlet)]
    return _Faces(inlet=inlet, outlet=outlet, wall=wall)


def _ranges(ids: list[int]) -> list[list[int]]:
    """Sorted ids as merged inclusive ranges: the form a tag member stores."""
    if not ids:
        return []
    ordered = sorted(ids)
    out: list[list[int]] = []
    first = last = ordered[0]
    for value in ordered[1:]:
        if value == last + 1:
            last = value
            continue
        out.append([first, last])
        first = last = value
    out.append([first, last])
    return out


def _faces(part_id: str, ids: list[int]) -> list[dict[str, Any]]:  # Any: JSON members
    """One member naming ``ids`` of ``part_id``, in the spelling the commands take."""
    return [{"part": part_id, "faces": _ranges(ids)}]


def _define_parameters(run: Any) -> None:  # Any: Session.execute
    """The model's parameters, before anything refers to them."""
    run("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    run("parameter.define", name="rho", dimension="density", value="998 [kg/m**3]")
    run("parameter.define", name="T_in", dimension="temperature", value="40 [degC]")
    run("parameter.define", name="D", dimension="length", value="60 [mm]")
    run("parameter.define", name="dp_dyn", dimension="pressure", value="0.5 * rho * U_in**2")
    run("parameter.define", name="Q", dimension="volume_flow", value="U_in * pi * (D/2)**2")


def _build_without_geometry(
    session: Session, run: Any, parameters_done: bool = False
) -> None:  # Any: Session.execute
    """The part of the demo that does not need the geometry: the study and the empty scene.

    What is deliberately ABSENT: the region, the tags and the typed boundaries. Every one of them
    names faces of the imported bodies, and with no geometry there are no faces to name — inventing
    empty groups here is exactly the old demo that "cannot exercise the features and reads as a
    defect". A project with parameters and a study is honest about what it is; a project with empty
    tags pretending to be a flow case is not.

    The parameters are not re-defined when the caller already made them (`parameters_done`), so the
    geometry-failure path does not define them twice.
    """
    if not parameters_done:
        _define_parameters(run)
    run("node.add", parent="/scenes", type="group", name="scene_1")
    sweep = "/studies/velocity_sweep"
    run("study.create", name="velocity_sweep", description="Inlet velocity sweep")
    run(
        "study.add_variable",
        study=sweep,
        parameter="U_in",
        values=["1 [m/s]", "2 [m/s]", "3 [m/s]"],
    )
    run("study.add_response", study=sweep, expression="dp_dyn", label="Dynamic pressure")
    run("study.add_response", study=sweep, expression="Q", label="Volume flow")
    run("study.generate", study=sweep)
    _ = session


def build_demo(session: Session) -> None:
    """Build the demo project in ``session``.

    **A build without OCP gets the geometry-free demo rather than a failure** (Claude's defect 5).
    The geometry is generated from OCP primitives, so on a build missing the optional `cad` extra
    the window used to fail to open at all — the tree is built and a warning is logged instead. The
    demo exists to show the app working; a demo that stops the app working is the wrong trade.
    """
    from stokes.app.demo_geometry import build_demo_step, demo_available
    from stokes.geometry.store import GeometryStore

    run = session.execute
    # **THE PROJECT IS NOT MADE HERE** (claude-win2's 1805 message, sample copies). `build_demo`
    # used to start with `project.new(name="demo_pipe")`, which was right when the demo was a
    # one-off and wrong now: `project.sample_open` has already made the copy's folder, and a second
    # `project.new` creates ANOTHER folder and abandons the one the caller prepared. **Measured**:
    # `project.sample_open` reported `pipe-1.stokes` and the project landed in `demo_pipe.stokes`.
    #
    # The caller decides the folder; this builds the CONTENT. That split is what lets the same
    # builder serve the start screen's sample copy and, later, a fresh project the user named.
    if session.project is None:
        run("project.new", name="demo_pipe")
    _define_parameters(run)

    if not demo_available():
        log.warning(
            "the demo geometry needs the optional `cad` extra (OCP); building the tree without it",
            extra=fields(reason="OCP not importable"),
        )
        _build_without_geometry(session, run, parameters_done=True)
        return

    # --- the geometry: generate a STEP, import it, wait for the meshes -------------------------
    store = GeometryStore.of(session)
    # **A write that the disk refuses is a fallback, not a crash** (Claude's defect 3). On Windows
    # `Path.replace` over a file another process holds open raises `PermissionError [WinError 5]`,
    # and letting that escape `build_demo` meant the window did not open at all. The disk is allowed
    # to say no: the demo degrades to its geometry-free form and logs why, as it does when OCP is
    # missing.
    try:
        source = build_demo_step()
    except OSError as exc:
        log.error(
            "the demo geometry could not be written; building the tree without it",
            extra=fields(error=str(exc)),
        )
        _build_without_geometry(session, run, parameters_done=True)
        return
    imported = run("geometry.import_step", path=str(source))
    # **The wait is SKIPPED on the session thread, because it is refused there** - measured:
    # `project.sample_open` ran this builder while the server was serving, and `GeometryStore.wait`
    # raised `validation: waiting for an import would block the server`, so the menu's Samples entry
    # could not open the one sample the build ships. The refusal is right (CLAUDE.md 7: never block
    # the UI thread) and the builder has to live with it.
    #
    # Skipping is not "ignore the result": the checks below still run where they can. When the wait
    # is skipped the import continues in the store's worker and its parts arrive as `geometry.*`
    # events, which is how the front end follows every other import.
    if store.blocks_session():
        log.info(
            "the demo geometry import continues in the background; its parts arrive as events",
            extra=fields(source=str(source)),
        )
    elif not store.wait(imported["id"], timeout=120):
        log.error(
            "the demo geometry did not finish importing; building the tree without it",
            extra=fields(source=str(source), timeout_s=120),
        )
        _build_without_geometry(session, run, parameters_done=True)
        return

    # **The tagging half runs either after a wait, or when the import reports done.** See
    # `_tag_the_pipe`: the two paths exist because the session thread may not wait.
    if store.blocks_session():
        if not store.when_done(str(imported["id"]), lambda: _tag_the_pipe(session, run, store)):
            log.error(
                "the demo geometry import could not be watched; building the tree without it",
                extra=fields(source=str(source)),
            )
            _build_without_geometry(session, run, parameters_done=True)
            return
    else:
        _tag_the_pipe(session, run, store)

    # --- a scene, so the Scenes stage is not empty ---------------------------------------------
    run("node.add", parent="/scenes", type="group", name="scene_1")

    # --- Explore v1 (ADR-0016): an inlet-velocity sweep, evaluated by the function engine -------
    sweep = "/studies/velocity_sweep"
    run("study.create", name="velocity_sweep", description="Inlet velocity sweep")
    run(
        "study.add_variable",
        study=sweep,
        parameter="U_in",
        values=["1 [m/s]", "2 [m/s]", "3 [m/s]"],
    )
    run("study.add_response", study=sweep, expression="dp_dyn", label="Dynamic pressure")
    run("study.add_response", study=sweep, expression="Q", label="Volume flow")
    run("study.generate", study=sweep)
    run("study.update", study=sweep)
    session.undo.clear()


def _tag_the_pipe(session: Session, run: Any, store: Any) -> None:  # Any: Session.execute
    """The geometry-dependent half of the demo: the material, the region, the boundaries.

    **Its own function because it has to run at a chosen TIME.** The faces it tags come from the
    imported mesh, so it cannot run until the read is done - and on the session thread it may not
    wait for that read (the import needs the same thread to build its parts). So the caller
    either waits for the import where it may, or hands this function to
    :meth:`GeometryStore.when_done` where it may not.
    """
    # --- the fluid material, then a region over the pipe ---------------------------------------
    # Added to THIS project before it is referred to: the catalogue ships as data
    # (`materials.json`) and a project starts with no materials of its own.
    run("material.add_from_library", fluid=WATER, provider="builtin")
    # The **fluid** body is the region: it is the domain the conditions apply to, and its two end
    # faces (standing clear of the flanges, thanks to the extension) are the natural inlet and
    # outlet. The metal parts are the solids it flows through, so they carry the wall too.
    fluids = _part_ids(session, ("fluid",))
    solids = _part_ids(session, ("pipe", "inlet_flange", "outlet_flange"))
    if fluids:
        run("region.assign", material=WATER, target=fluids, name="fluid")
        faces = _faces_of(store, session, fluids[0])
        if faces is not None:
            # The typed boundaries, each naming its own tag. **`boundary.assign` creates the tag**,
            # with the kind's colour and the `name` given here — creating a tag first and assigning
            # after made TWO tags per condition (`inlet` and `inlet_2`), because the command builds
            # its own tag from the picked faces and does not adopt an existing one.
            #
            # `values={"velocity": "U_in"}` stores the parameter expression, not a bare number, so
            # the value field shows `U_in` and the condition follows the parameter (CLAUDE.md §1
            # rule 6).
            run(
                "boundary.assign",
                target=_faces(fluids[0], [faces.inlet]),
                kind="velocity_inlet",
                values={"velocity": "U_in"},
                name="inlet",
            )
            run(
                "boundary.assign",
                target=_faces(fluids[0], [faces.outlet]),
                kind="pressure_outlet",
                values={"pressure": "0 [Pa]"},
                name="outlet",
            )
            # The fluid's own curved surface is a wall, and so is every metal face the fluid
            # touches: the pipe's bore and the flanges' bores. The metal parts are given the wall as
            # whole bodies, which is what the tag then names.
            if faces.wall:
                run(
                    "boundary.assign",
                    target=_faces(fluids[0], faces.wall),
                    kind="wall",
                    values={},
                    name="walls",
                )
            if solids:
                # Members, not bare ids: `boundary.assign` takes the member form, and `"faces":
                # "all"` is the whole-body spelling (`{"part": x}` alone is malformed — the owner's
                # answer 4). `_ranges([])` would name nothing, so `"all"` is written directly.
                run(
                    "boundary.assign",
                    target=[{"part": part, "faces": "all"} for part in solids],
                    kind="wall",
                    values={},
                    name="solid_walls",
                )

    # --- the mesh domain, so the tutorial can actually be meshed on open ------------------------
    # **The demo shipped unmeshable.** `ensure_stage_settings` fills `/mesh/settings` from the
    # schema's defaults, and those defaults are `domain: "part"` with **no part chosen** - so the
    # first thing a user does to the tutorial failed instantly:
    #
    #     MeshInvalid: domain is 'part' but no domain part is set
    #
    # Measured on this build, before this line existed. The demo knows which body is the flow
    # domain - it has just given it a region and the conditions - so it names it. **A general
    # project must not guess**: which body is the domain is the user's decision, and the GUI's
    # Mesh panel is where they make it. This is the demo completing its own setup, not a
    # default changing under anyone.
    if fluids:
        run("mesh.configure", values={"domain": "part", "domain_part": fluids[0]})

    # --- the look: a translucent fluid, so the flow path is visible through the metal -----------
    # The colours come from the STEP file (`demo_geometry`'s palette, written by
    # `brep.write_step`), so only the opacity is set here: the fluid is the one body you need to see
    # *through*, because it is inside an opaque pipe and would otherwise be invisible. 0.35 is a
    # display choice, not a physical one — the viewport's opacity is a rendering property, not a
    # material property, and the material (`water_liquid`) is what the solver would read.
    if fluids:
        run("geometry.style", nodes=fluids, opacity=0.35)


def _register_sample() -> None:
    """Tell `stokes.model.samples` how to build the pipe, **from here**.

    **This direction is what the layering rule requires**: `stokes.model` is headless and this
    package is where OCP is allowed, so the model must not import the app - measured, the gate
    refused `stokes.model/samples.py: stokes.model must not import 'stokes.app.demo'` even with the
    import inside a function. Registering from the app inverts the arrow and keeps the headless
    package headless.
    """
    from stokes.model.samples import register

    register("pipe", build_demo)


_register_sample()
