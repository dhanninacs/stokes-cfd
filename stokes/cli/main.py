"""``stokes`` command-line entry point.

Every subcommand maps onto one API command, loads the project, executes, saves
when the command mutates, and prints JSON. Exit codes: 0 ok, 1 Stokes error
(JSON on stdout with an ``error`` key), 2 usage. ``stokes setup check`` also
exits 1 when the set-up state is ``incomplete`` (its findings are then on
stdout as usual).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from stokes import __version__
from stokes.cli import cases, setup
from stokes.errors import StokesError


def _number_or_text(text: str) -> float | str:
    """A number when ``text`` is one, else the text: quantity parameters take either."""
    try:
        return float(text)
    except ValueError:
        return text


def _parse_params(items: Sequence[str]) -> dict[str, Any]:
    """``k=v`` pairs; values that parse as JSON are JSON, others are strings."""
    params: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise StokesError(f"expected key=value, got {item!r}")
        key, _, value = item.partition("=")
        try:
            params[key] = json.loads(value)
        except ValueError:
            params[key] = value
    return params


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stokes", description="Stokes CFD workbench: project commands with JSON output."
    )
    parser.add_argument("--version", action="version", version=f"stokes {__version__}")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="create a project")
    p.add_argument("project", help="path of the .stokes folder to create")
    p.add_argument("--name", help="project name (default: folder name)")
    p.add_argument("--units", default="si", help="display unit system: si, mm, imperial")

    p = sub.add_parser("info", help="project summary")
    p.add_argument("project")

    p = sub.add_parser("tree", help="print the tree")
    p.add_argument("project")
    p.add_argument("--root", help="start node (id or path)")
    p.add_argument("--depth", type=int, help="levels to include")

    p = sub.add_parser("get", help="read a node or one parameter")
    p.add_argument("project")
    p.add_argument("node")
    p.add_argument("param", nargs="?")
    p.add_argument("--unit", help="display unit for quantities")

    p = sub.add_parser("set", help="set a parameter: quantity text or expression")
    p.add_argument("project")
    p.add_argument("node")
    p.add_argument("param")
    p.add_argument("value")

    p = sub.add_parser("add", help="add a node")
    p.add_argument("project")
    p.add_argument("parent")
    p.add_argument("type")
    p.add_argument("name")
    p.add_argument("params", nargs="*", help="initial parameters as key=value")

    p = sub.add_parser("rm", help="remove a node")
    p.add_argument("project")
    p.add_argument("node")

    p = sub.add_parser("rename", help="rename a node")
    p.add_argument("project")
    p.add_argument("node")
    p.add_argument("name")

    p = sub.add_parser("param", help="define a named parameter under /physics/parameters")
    p.add_argument("project")
    p.add_argument("name")
    p.add_argument("dimension")
    p.add_argument("value")
    p.add_argument("--description", default="")

    p = sub.add_parser("params", help="list named parameters")
    p.add_argument("project")

    p = sub.add_parser("replay", help="replay a journal into the project")
    p.add_argument("project")
    p.add_argument("journal")

    p = sub.add_parser("geometry", help="CAD geometry")
    geometry = p.add_subparsers(dest="geometry_cmd", required=True)
    g = geometry.add_parser("import", help="import a geometry file (STEP, IGES, BREP) as parts")
    g.add_argument("file", help="a .step/.stp, .iges/.igs or .brep/.brp file")
    g.add_argument("--name", help="part name (default: from the file name)")
    g.add_argument("--units", help="the file's length unit, for BREP (default mm)")
    g.add_argument(
        "--project",
        help="add the part to this project and save it; without one the file is only inspected",
    )

    p = sub.add_parser(
        "material",
        help="materials: providers, library-search, library-info, add, create, set-state, "
        "refit, evaluate, check, list",
    )
    material = p.add_subparsers(dest="material_cmd", required=True)
    for verb, text in (
        ("list", "list the project's materials"),
        ("create", "create a material"),
        ("library-search", "search the material libraries"),
        ("library-info", "info about a library record"),
        ("evaluate", "evaluate a material property"),
        ("providers", "the material providers and REFPROP's detection"),
        ("add", "add a material from a library (fitted from CoolProp, Cantera or REFPROP)"),
        ("set-state", "change a material's state and fit its library properties again"),
        ("refit", "fit a library material's properties again"),
        ("check", "a material's consistency findings"),
    ):
        m = material.add_parser(verb, help=text)
        m.add_argument(
            "--project",
            help="project to open first; the library queries and providers do not need one",
        )
        _material_arguments(verb, m)
        if verb == "create":
            m.add_argument("name")
            m.add_argument("--kind", default="fluid", help="fluid or solid")
            m.add_argument("--template", help="liquid or gas for a fluid, solid for a solid")
        elif verb == "library-search":
            m.add_argument("query", nargs="?", default="", help="name, alias or CAS")
            m.add_argument(
                "--providers", default="", help='comma-separated, e.g. "builtin,coolprop"'
            )
            m.add_argument("--kind", default="any", help="fluid, solid or any")
            m.add_argument("--limit", type=int, default=50)
        elif verb == "library-info":
            m.add_argument("fluid", help="record id from library_search")
            m.add_argument("--provider", default="builtin")
        elif verb == "evaluate":
            m.add_argument("material", help="material id, path or name")
            m.add_argument("property", help="property name, e.g. density")
            m.add_argument("--temperature", help='T: a number in K or a quantity such as "25 degC"')
            m.add_argument("--pressure", help='absolute p: a number in Pa or such as "2 [bar]"')

    p = sub.add_parser("study", help="parametric studies: create, generate, update, results")
    study = p.add_subparsers(dest="study_cmd", required=True)
    s = study.add_parser("create", help="create a study under /studies")
    s.add_argument("project")
    s.add_argument("name")
    s.add_argument("--type", default="sweep", help="sweep or doe (default sweep)")
    s.add_argument("--description", default="")
    s.add_argument("--sampler", help="doe sampler, e.g. latin_hypercube")
    s.add_argument("--seed", type=int, help="seed of the randomised samplers")
    s.add_argument("--count", type=int, help="number of points, for the samplers that take one")
    for verb, text in (
        ("generate", "fill the design table (replaces it)"),
        ("update", "evaluate the points with the function engine"),
        ("results", "print the design table"),
        ("pareto", "print the Pareto front of the evaluated points"),
    ):
        s = study.add_parser(verb, help=text)
        s.add_argument("project")
        s.add_argument("study", help="study id, path or name")
        if verb == "update":
            s.add_argument("--points", default="all", help='"all" or indices such as 0,2,5')
        if verb == "results":
            s.add_argument("--format", default="json", choices=["json", "csv"])

    _add_tag_parser(
        sub,
        "tag",
        "tags: create, add, remove, set, invert, grow, rematch, rename, delete, "
        "list, members, by-color, by-name",
    )
    _add_tag_parser(sub, "selection", None)
    setup.add_parsers(sub)
    cases.add_parsers(sub)

    p = sub.add_parser("call", help="run any API command")
    p.add_argument("command")
    p.add_argument("--project", help="project to open first (and save after if it mutates)")
    p.add_argument("--json", dest="payload", default="{}", help="parameters as a JSON object")

    p = sub.add_parser("units", help="unit conversion")
    units = p.add_subparsers(dest="units_cmd", required=True)
    u = units.add_parser("convert", help="convert a quantity between units")
    u.add_argument("value", type=float, help="the numeric value in the source unit")
    u.add_argument("from_unit", help="source unit, e.g. m, K, m/s")
    u.add_argument("to_unit", help="target unit, e.g. mm, degC, ft/s")

    p = sub.add_parser("schema", help="introspection")
    p.add_argument("what", choices=["commands", "nodes", "units", "version"])
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    from stokes.api import Session

    parser = build_parser()
    args = parser.parse_args(argv)
    session = Session()
    try:
        result, mutated = _dispatch(session, args)
        if mutated and session.project_path is not None:
            session.execute("project.save")
        _print(result, args.pretty)
        return setup.exit_code(result) if args.cmd == "setup" else 0
    except StokesError as exc:
        _print({"error": exc.to_dict()}, args.pretty)
        return 1


def _dispatch(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    def open_project() -> None:
        session.execute("project.open", path=args.project)

    match args.cmd:
        case "new":
            from pathlib import Path

            name = args.name or Path(args.project).name.removesuffix(".stokes")
            result = session.execute(
                "project.new", name=name, path=args.project, display_units=args.units
            )
            return result, False
        case "info":
            open_project()
            return session.execute("project.info"), False
        case "tree":
            open_project()
            return session.execute("node.tree", root=args.root, depth=args.depth), False
        case "get":
            open_project()
            if args.param:
                return (
                    session.execute(
                        "parameter.get", node=args.node, name=args.param, unit=args.unit
                    ),
                    False,
                )
            return session.execute("node.get", node=args.node), False
        case "set":
            open_project()
            return (
                session.execute("parameter.set", node=args.node, name=args.param, value=args.value),
                True,
            )
        case "add":
            open_project()
            return (
                session.execute(
                    "node.add",
                    parent=args.parent,
                    type=args.type,
                    name=args.name,
                    params=_parse_params(args.params),
                ),
                True,
            )
        case "rm":
            open_project()
            return session.execute("node.remove", node=args.node), True
        case "rename":
            open_project()
            return session.execute("node.rename", node=args.node, name=args.name), True
        case "param":
            open_project()
            return (
                session.execute(
                    "parameter.define",
                    name=args.name,
                    dimension=args.dimension,
                    value=args.value,
                    description=args.description,
                ),
                True,
            )
        case "params":
            open_project()
            return session.execute("parameter.list"), False
        case "replay":
            open_project()
            return session.execute("journal.replay", path=args.journal), True
        case "geometry":
            return _geometry(session, args)
        case "material":
            return _material(session, args)
        case "study":
            return _study(session, args)
        case "tag" | "selection":
            return _selection(session, args)
        case "boundary" | "region" | "setup":
            return setup.dispatch(session, args)
        case "tutorial" | "case":
            return cases.dispatch(session, args)
        case "call":
            if args.project:
                open_project()
            try:
                payload = json.loads(args.payload)
            except ValueError as exc:
                raise StokesError(f"--json is not valid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise StokesError("--json must be a JSON object")
            definition = session.registry.get(args.command)
            return session.execute(args.command, **payload), bool(
                definition.mutates and args.project
            )
        case "units":
            match args.units_cmd:
                case "convert":
                    return (
                        session.execute(
                            "units.convert",
                            value=args.value,
                            from_unit=args.from_unit,
                            to_unit=args.to_unit,
                        ),
                        False,
                    )
            raise StokesError(f"unknown units subcommand {args.units_cmd!r}")  # pragma: no cover
        case "schema":
            return session.execute(
                f"schema.{args.what}" if args.what != "nodes" else "node.types"
            ), False
    raise StokesError(f"unknown subcommand {args.cmd!r}")  # pragma: no cover


def _geometry(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    """``geometry import``: the import runs to the end, then the part (one solid) or the group
    of parts (several solids) is printed."""
    if args.project:
        session.execute("project.open", path=args.project)
    else:
        session.execute("project.new", name="inspect")
    part = session.execute(
        "geometry.import", path=args.file, name=args.name, units=args.units, wait=True
    )
    info = session.execute("geometry.info", node=part["id"])
    state = info["import"]
    if state["status"] != "ready":
        errors = [state.get("error")] + [p["import"].get("error") for p in info.get("parts", [])]
        error: dict[str, Any] = next((e for e in errors if e), {})
        failure = StokesError(str(error.get("message") or f"import {state['status']}"))
        failure.code = str(error.get("code") or "geometry.error")
        raise failure
    return info, bool(args.project)


def _json_argument(text: str, flag: str) -> Any:  # Any: JSON list of members or references
    """A list or object argument of a command, given as JSON text."""
    try:
        found = json.loads(text)
    except ValueError as exc:
        raise StokesError(f"{flag} is not valid JSON: {exc}") from exc
    if not isinstance(found, list | dict):
        raise StokesError(f"{flag} must be a JSON list or object")
    return found


def _add_tag_parser(sub: Any, name: str, help_text: str | None) -> None:  # Any: subparsers action
    """Add the ``tag`` verb tree under ``name``; ``selection`` reuses it as a hidden alias."""
    # With no ``help`` keyword at all the subparser stays out of the top-level help. Python 3.13
    # prints ``==SUPPRESS==`` for ``help=argparse.SUPPRESS``, and ``help=None`` lists a bare row.
    parser = sub.add_parser(name, help=help_text) if help_text is not None else sub.add_parser(name)
    verbs = parser.add_subparsers(dest="selection_cmd", required=True)
    for verb, text in (
        ("create", "create a tag under /geometry/tags"),
        ("add", "add faces or whole parts to a tag"),
        ("remove", "take faces or whole parts out of a tag"),
        ("set", "replace a tag's members"),
        ("invert", "within the given parts, select the faces the tag lacks"),
        ("grow", "add every face reachable without crossing an edge sharper than the angle"),
        ("rematch", "carry a tag over to a changed part's new face ids"),
        ("rename", "rename a tag"),
        ("delete", "delete a tag"),
        ("list", "every tag"),
        ("members", "a tag's faces part by part, expanded to ids"),
        ("by-color", "parts of one colour, as members for create, add or set"),
        ("by-name", "parts whose name matches, as members for create, add or set"),
    ):
        sel = verbs.add_parser(verb, help=text)
        sel.add_argument(
            "--project", help="project to open first; a change is saved after the command"
        )
        _selection_arguments(verb, sel)


def _selection_arguments(verb: str, sel: argparse.ArgumentParser) -> None:
    """The arguments of one ``tag.*`` command, in the order its parameters appear in
    :mod:`stokes.api.commands.selection`; lists of members are JSON text."""
    if verb == "create":
        sel.add_argument("name", help="tag name (letters, digits, underscores)")
        sel.add_argument("--members", default="[]", help="members as JSON")
        sel.add_argument("--kind", default="boundary", help="boundary, region or plain")
        sel.add_argument("--color", help="#rrggbb; default: the next colour of the palette")
        return
    if verb in ("add", "remove", "set"):
        sel.add_argument("tag", help="tag id, path or name")
        sel.add_argument("members", help="members as JSON")
        return
    if verb == "invert":
        sel.add_argument("tag", help="tag id, path or name")
        sel.add_argument("parts", help="parts or groups (ids or paths) as a JSON list")
        return
    if verb == "grow":
        sel.add_argument("tag", help="tag id, path or name")
        sel.add_argument("--angle-deg", type=float, help="feature angle in degrees (30)")
        sel.add_argument("--seeds", help="faces to grow from, as JSON members")
        return
    if verb == "rematch":
        sel.add_argument("tag", help="tag id, path or name")
        sel.add_argument("--min-confidence", type=float, help="0 to 1 (0.5)")
        return
    if verb == "rename":
        sel.add_argument("tag", help="tag id, path or name")
        sel.add_argument("name", help="new name")
        return
    if verb in ("delete", "members"):
        sel.add_argument("tag", help="tag id, path or name")
        if verb == "delete":
            sel.add_argument(
                "--force",
                action="store_true",
                help="also delete the boundaries and regions that use the tag",
            )
        return
    if verb == "by-color":
        sel.add_argument("color", help="#rrggbb")
        sel.add_argument("--part", help="a part or group to search; default: every part")
        return
    if verb == "by-name":
        sel.add_argument("pattern", help="shell-style pattern, e.g. bolt*")
        sel.add_argument("--part", help="a part or group to search; default: every part")


def _selection(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    """One ``tag.*`` command per verb; the project is saved after the ones that change it."""
    if not args.project:
        raise StokesError(f"tag {args.selection_cmd} needs a project: pass --project")
    session.execute("project.open", path=args.project)
    match args.selection_cmd:
        case "create":
            return (
                session.execute(
                    "tag.create",
                    name=args.name,
                    members=_json_argument(args.members, "--members"),
                    kind=args.kind,
                    color=args.color,
                ),
                True,
            )
        case "add" | "remove" | "set":
            return (
                session.execute(
                    f"tag.{args.selection_cmd}",
                    tag=args.tag,
                    members=_json_argument(args.members, "members"),
                ),
                True,
            )
        case "invert":
            return (
                session.execute(
                    "tag.invert",
                    tag=args.tag,
                    parts=_json_argument(args.parts, "parts"),
                ),
                True,
            )
        case "grow":
            seeds = None if args.seeds is None else _json_argument(args.seeds, "--seeds")
            return (
                session.execute(
                    "tag.grow",
                    tag=args.tag,
                    angle_deg=args.angle_deg,
                    seeds=seeds,
                ),
                True,
            )
        case "rematch":
            return (
                session.execute(
                    "tag.rematch",
                    tag=args.tag,
                    min_confidence=args.min_confidence,
                ),
                True,
            )
        case "rename":
            return (
                session.execute("tag.rename", tag=args.tag, name=args.name),
                True,
            )
        case "delete":
            return session.execute("tag.delete", tag=args.tag, force=args.force), True
        case "list":
            return session.execute("tag.list"), False
        case "members":
            return session.execute("tag.members", tag=args.tag), False
        case "by-color":
            return (
                session.execute("tag.by_color", color=args.color, part=args.part),
                False,
            )
        case "by-name":
            return (
                session.execute("tag.by_name", pattern=args.pattern, part=args.part),
                False,
            )
    raise StokesError(f"unknown tag subcommand {args.selection_cmd!r}")  # pragma: no cover


_STATE_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("--phase", "phase", "auto, liquid, gas or supercritical"),
    ("--T-ref", "T_ref", 'reference temperature: K or a quantity such as "25 degC"'),
    ("--p-ref", "p_ref", 'reference (absolute) pressure: Pa or a quantity such as "2 [bar]"'),
    ("--T-min", "T_min", "lower end of the fit range"),
    ("--T-max", "T_max", "upper end of the fit range"),
)
_NO_PROJECT = ("library-search", "library-info", "providers")


def _material_arguments(verb: str, m: argparse.ArgumentParser) -> None:
    """The arguments of the library verbs (``providers``, ``add``, ``set-state``, ``refit``,
    ``check``); the others are declared where the group is built."""
    if verb == "providers":
        m.add_argument("--refprop-path", help="the REFPROP folder (Stokes' settings path)")
        return
    if verb == "add":
        m.add_argument("fluid", help="the provider's id: Water, R410A.mix, MEG, air.yaml, WATER")
        m.add_argument(
            "--provider",
            default="builtin",
            help="builtin, coolprop, coolprop_incomp, cantera or refprop",
        )
        m.add_argument(
            "--composition", default="", help='e.g. "0.79,0.21", "0.5", "O2:0.21,N2:0.79"'
        )
        m.add_argument("--basis", help="mole, mass or volume")
        m.add_argument("--name", help="material name (default: from the record)")
    if verb in ("add", "set-state"):
        for flag, key, text in _STATE_OPTIONS:
            m.add_argument(flag, dest=key, help=text)
        m.add_argument("--tolerance", type=float, help="largest relative fit error (0.01)")
        m.add_argument("--max-degree", type=int, help="highest polynomial degree (4, at most 7)")
    if verb == "set-state":
        m.add_argument("--fluid", help="the provider's fluid id")
        m.add_argument("--composition", help="fractions")
        m.add_argument("--basis", help="mole, mass or volume")
        m.add_argument("--provider", help="a library provider makes it a library material")
        m.add_argument("--no-refit", action="store_true", help="leave the properties stale")
    if verb in ("add", "refit"):
        m.add_argument("--methods", help='forced forms as JSON, e.g. {"density": "constant"}')
    if verb == "refit":
        m.add_argument("--properties", default="", help="comma-separated; default all")
    if verb in ("set-state", "refit", "check"):
        m.add_argument("material", help="material id, path or name")


def _quantity_options(args: argparse.Namespace) -> dict[str, Any]:  # Any: JSON
    found: dict[str, Any] = {}  # Any: JSON
    for _, key, _ in _STATE_OPTIONS:
        value = getattr(args, key, None)
        if value is not None:
            found[key] = value if key == "phase" else _number_or_text(value)
    if getattr(args, "tolerance", None) is not None:
        found["tolerance"] = args.tolerance
    if getattr(args, "max_degree", None) is not None:
        found["max_degree"] = args.max_degree
    return found


def _methods_option(text: str | None) -> dict[str, Any] | None:  # Any: JSON
    if not text:
        return None
    try:
        methods = json.loads(text)
    except ValueError as exc:
        raise StokesError(f"--methods is not valid JSON: {exc}") from exc
    if not isinstance(methods, dict):
        raise StokesError('--methods must be a JSON object such as {"density": "constant"}')
    return methods


def _material_library(session: Any, args: argparse.Namespace) -> tuple[Any, bool] | None:  # Any
    """The library verbs, or None for the others. A background fit (none runs without an event
    loop, but a script may ask for one) lands before the project is saved."""
    match args.material_cmd:
        case "providers":
            return session.execute("material.providers", refprop_path=args.refprop_path), False
        case "check":
            return session.execute("material.check", material=args.material), False
        case "add":
            result = session.execute(
                "material.add_from_library",
                fluid=args.fluid,
                provider=args.provider,
                composition=args.composition,
                basis=args.basis,
                name=args.name,
                methods=_methods_option(args.methods),
                **_quantity_options(args),
            )
        case "set-state":
            changes = {
                key: getattr(args, key)
                for key in ("fluid", "composition", "basis", "provider")
                if getattr(args, key) is not None
            }
            result = session.execute(
                "material.set_state",
                material=args.material,
                refit=not args.no_refit,
                **changes,
                **_quantity_options(args),
            )
        case "refit":
            properties = [p.strip() for p in args.properties.split(",") if p.strip()]
            result = session.execute(
                "material.refit",
                material=args.material,
                properties=properties or None,
                methods=_methods_option(args.methods),
            )
        case _:
            return None
    service = session.extensions.get("materials")
    if service is not None:
        service.wait()
    return result, True


def _material(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    """One ``material.*`` command per verb; the project is saved after the ones that change it."""
    if args.project:
        session.execute("project.open", path=args.project)
    elif args.material_cmd not in _NO_PROJECT:
        raise StokesError(f"material {args.material_cmd} needs a project: pass --project")
    library = _material_library(session, args)
    if library is not None:
        return library
    match args.material_cmd:
        case "list":
            return session.execute("material.list"), False
        case "create":
            return session.execute(
                "material.create", name=args.name, kind=args.kind, template=args.template
            ), True
        case "library-search":
            providers = (
                [p.strip() for p in args.providers.split(",") if p.strip()]
                if args.providers
                else None
            )
            return session.execute(
                "material.library_search",
                query=args.query,
                providers=providers,
                kind=args.kind,
                limit=args.limit,
            ), False
        case "library-info":
            return session.execute(
                "material.library_info", fluid=args.fluid, provider=args.provider
            ), False
        case "evaluate":
            # The command takes lists of properties and temperatures (T) and one pressure (p).
            params: dict[str, Any] = {"properties": [args.property]}  # Any: JSON
            if args.temperature is not None:
                params["T"] = [_number_or_text(args.temperature)]
            if args.pressure is not None:
                params["p"] = _number_or_text(args.pressure)
            return session.execute("material.evaluate", material=args.material, **params), False
    raise StokesError(f"unknown material subcommand {args.material_cmd!r}")  # pragma: no cover


def _study(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    """``study create|generate|update|results|pareto``: one ``study.*`` command each; the
    project is saved after the ones that change it (the table files are written at once)."""
    session.execute("project.open", path=args.project)
    match args.study_cmd:
        case "create":
            created = session.execute(
                "study.create",
                name=args.name,
                type=args.type,
                description=args.description,
                sampler=args.sampler,
                seed=args.seed,
                count=args.count,
            )
            return created, True
        case "generate":
            return session.execute("study.generate", study=args.study), True
        case "update":
            points: Any = "all"  # Any: "all" or a list of indices
            if args.points != "all":
                try:
                    points = [int(p) for p in args.points.split(",") if p.strip()]
                except ValueError as exc:
                    raise StokesError(
                        f'--points expects "all" or indices like 0,2,5: {exc}'
                    ) from exc
            return session.execute("study.update", study=args.study, points=points), True
        case "results":
            return session.execute("study.results", study=args.study, format=args.format), False
        case "pareto":
            return session.execute("study.pareto", study=args.study), False
    raise StokesError(f"unknown study subcommand {args.study_cmd!r}")  # pragma: no cover


def _print(data: Any, pretty: bool) -> None:  # Any: JSON
    text = json.dumps(data, indent=2 if pretty else None, ensure_ascii=False, default=str)
    sys.stdout.write(text + "\n")


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
