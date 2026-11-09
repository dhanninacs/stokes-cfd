"""``stokes boundary`` and ``stokes region``: the analysis set-up verbs (ADR-0022 decision 3.5).

One API command per verb, JSON output as ``stokes tag``. The project is the first argument
(``kinds`` needs none); a verb that changes the project saves it afterwards. A target or a list of
bodies that starts with ``[`` is JSON (members, or part ids); anything else is a reference::

    stokes boundary assign demo.stokes /geometry/tags/inlet velocity_inlet \\
        --values '{"velocity": "2 [m/s]"}'
    stokes boundary assign demo.stokes '[{"part": "/geometry/parts/pipe", "faces": [3]}]' wall
    stokes region assign demo.stokes water '["/geometry/parts/pipe"]'
    stokes setup check demo.stokes
    stokes setup defaults demo.stokes /geometry/parts/enclosure external --velocity '2 [m/s]'
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from stokes.errors import StokesError

BOUNDARY_VERBS: tuple[tuple[str, str], ...] = (
    ("assign", "assign a condition kind to a tag, a boundary or picked faces (one undo step)"),
    ("set", "change a boundary's kind, values, tag, faces or name"),
    ("delete", "delete a boundary (and its tag with --delete-tag)"),
    ("list", "every boundary with its tag, primary value, findings and status"),
    ("kinds", "the condition kinds and their parameters (no project needed)"),
)
REGION_VERBS: tuple[tuple[str, str], ...] = (
    ("create", "create a region with its tag and boundaries group"),
    ("assign", "assign a material to a region or to bodies (they join its region)"),
    ("set", "change a region's material, kind, bodies or name"),
    ("delete", "delete a region with its boundaries (and its tag with --delete-tag)"),
    ("list", "every region with its material, bodies and boundaries"),
)
SETUP_VERBS: tuple[tuple[str, str], ...] = (
    ("check", "the set-up state, the next step and every finding (read-only)"),
    ("defaults", "give a prepared body the default conditions of a flow template"),
)


def _json(text: str, flag: str, kind: type) -> Any:  # Any: JSON value
    try:
        found = json.loads(text)
    except ValueError as exc:
        raise StokesError(f"{flag} is not valid JSON: {exc}") from exc
    if not isinstance(found, kind):
        raise StokesError(f"{flag} must be a JSON {'object' if kind is dict else 'list'}")
    return found


def _target(text: str) -> Any:  # Any: a reference or a JSON list
    """A target: JSON when it starts with ``[``, else a reference."""
    return _json(text, "target", list) if text.lstrip().startswith("[") else text


def add_parsers(sub: Any) -> None:  # Any: argparse subparsers action
    """Add ``boundary``, ``region`` and ``setup`` with their verbs."""
    parser = sub.add_parser(
        "boundary", help="boundary conditions: assign, set, delete, list, kinds"
    )
    verbs = parser.add_subparsers(dest="boundary_cmd", required=True)
    for verb, text in BOUNDARY_VERBS:
        _boundary_arguments(verb, verbs.add_parser(verb, help=text))
    parser = sub.add_parser(
        "region", help="regions and materials: create, assign, set, delete, list"
    )
    verbs = parser.add_subparsers(dest="region_cmd", required=True)
    for verb, text in REGION_VERBS:
        _region_arguments(verb, verbs.add_parser(verb, help=text))
    parser = sub.add_parser("setup", help="the set-up check: check")
    verbs = parser.add_subparsers(dest="setup_cmd", required=True)
    for verb, text in SETUP_VERBS:
        _setup_arguments(verb, verbs.add_parser(verb, help=text))


def _boundary_arguments(verb: str, p: argparse.ArgumentParser) -> None:
    if verb == "kinds":
        p.add_argument("--engine", help="an engine id (accepted and ignored)")
        return
    p.add_argument("project", help="the .stokes folder")
    if verb == "assign":
        p.add_argument("target", help="tag or boundary (id, path or tag name), or JSON members")
        p.add_argument("kind", help="a condition kind id, e.g. velocity_inlet")
        p.add_argument("--values", help="values as a JSON object")
        p.add_argument("--name", help="the boundary's name")
        p.add_argument("--region", help="the region (made when no region has that name)")
        p.add_argument("--color", help="#rrggbb for the tag; default the kind's colour")
    elif verb == "set":
        p.add_argument("boundary", help="boundary id or path")
        p.add_argument("--kind", help="a new condition kind")
        p.add_argument("--values", help="values as a JSON object")
        p.add_argument("--tag", help="another boundary tag (id, path or name)")
        p.add_argument("--members", help="new faces for its tag, as JSON members")
        p.add_argument("--name", help="a new name")
        p.add_argument("--keep-color", action="store_true", help="keep the tag's colour")
    elif verb == "delete":
        p.add_argument("boundary", help="boundary id or path")
        p.add_argument("--delete-tag", action="store_true", help="delete its tag too")
    elif verb == "list":
        p.add_argument("--region", help="only this region's boundaries")


def _region_arguments(verb: str, p: argparse.ArgumentParser) -> None:
    p.add_argument("project", help="the .stokes folder")
    if verb == "create":
        p.add_argument("name", help="region name")
        p.add_argument("--kind", help="fluid or solid (default: the material's kind, else fluid)")
        p.add_argument("--bodies", help="bodies as a JSON list of part ids or paths")
        p.add_argument("--material", help="material id, path or name")
    elif verb == "assign":
        p.add_argument("material", help="material id, path or name")
        p.add_argument("target", help="region or region tag, or bodies as a JSON list")
        p.add_argument("--name", help="for bodies: the region to put them in")
    elif verb == "set":
        p.add_argument("region", help="region id, path or name")
        p.add_argument("--material", help="material id, path or name")
        p.add_argument("--kind", help="fluid or solid")
        p.add_argument("--members", help="the region's bodies as a JSON list")
        p.add_argument("--name", help="a new name")
    elif verb == "delete":
        p.add_argument("region", help="region id, path or name")
        p.add_argument("--delete-tag", action="store_true", help="delete its tag too")


def _setup_arguments(verb: str, p: argparse.ArgumentParser) -> None:
    p.add_argument("project", help="the .stokes folder")
    if verb == "defaults":
        p.add_argument("target", help="a part (id or path) or a region (id, path or name)")
        p.add_argument("template", help="external (after an enclosure) or internal (after a cap)")
        p.add_argument("--inlet", help="the inlet faces, as JSON members")
        p.add_argument("--outlet", help="the outlet faces, as JSON members")
        p.add_argument("--material", help="material id, path or name; default air or water")
        p.add_argument("--velocity", help="the velocity inlet's velocity, e.g. '2 [m/s]'")


def dispatch(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    """Run one verb; returns the result and whether the project changed (to be saved)."""
    if args.cmd == "setup":
        session.execute("project.open", path=args.project)
        return _setup(session, args)
    if args.cmd == "boundary":
        if args.boundary_cmd == "kinds":
            return session.execute("boundary.kinds", engine=args.engine), False
        session.execute("project.open", path=args.project)
        return _boundary(session, args)
    session.execute("project.open", path=args.project)
    return _region(session, args)


def _optional(text: str | None, flag: str, kind: type) -> Any:  # Any: JSON value or None
    return None if text is None else _json(text, flag, kind)


def _boundary(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    match args.boundary_cmd:
        case "assign":
            result = session.execute(
                "boundary.assign",
                target=_target(args.target),
                kind=args.kind,
                values=_optional(args.values, "--values", dict),
                name=args.name,
                region=args.region,
                color=args.color,
            )
            return result, True
        case "set":
            result = session.execute(
                "boundary.set",
                boundary=args.boundary,
                kind=args.kind,
                values=_optional(args.values, "--values", dict),
                tag=args.tag,
                members=_optional(args.members, "--members", list),
                name=args.name,
                keep_color=args.keep_color,
            )
            return result, True
        case "delete":
            result = session.execute(
                "boundary.delete", boundary=args.boundary, delete_tag=args.delete_tag
            )
            return result, True
        case "list":
            return session.execute("boundary.list", region=args.region), False
    raise StokesError(f"unknown boundary subcommand {args.boundary_cmd!r}")  # pragma: no cover


def _region(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    match args.region_cmd:
        case "create":
            result = session.execute(
                "region.create",
                name=args.name,
                kind=args.kind,
                bodies=_optional(args.bodies, "--bodies", list),
                material=args.material,
            )
            return result, True
        case "assign":
            result = session.execute(
                "region.assign", material=args.material, target=_target(args.target), name=args.name
            )
            return result, True
        case "set":
            result = session.execute(
                "region.set",
                region=args.region,
                material=args.material,
                kind=args.kind,
                members=_optional(args.members, "--members", list),
                name=args.name,
            )
            return result, True
        case "delete":
            result = session.execute(
                "region.delete", region=args.region, delete_tag=args.delete_tag
            )
            return result, True
        case "list":
            return session.execute("region.list"), False
    raise StokesError(f"unknown region subcommand {args.region_cmd!r}")  # pragma: no cover


def _setup(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    match args.setup_cmd:
        case "check":
            # Read-only: nothing to save (setup.check does not mutate, so it is not journaled).
            return session.execute("setup.check"), False
        case "defaults":
            result = session.execute(
                "setup.defaults",
                target=args.target,
                template=args.template,
                inlet=_optional(args.inlet, "--inlet", list),
                outlet=_optional(args.outlet, "--outlet", list),
                material=args.material,
                velocity=args.velocity,
            )
            return result, True
    raise StokesError(f"unknown setup subcommand {args.setup_cmd!r}")  # pragma: no cover


def exit_code(result: Any) -> int:  # Any: the setup.check result
    """0 for ``not_defined``, ``stale`` and ``ready``, and for a verb whose result is not a state
    (``setup.defaults``); 1 for ``incomplete``."""
    state = result.get("state") if isinstance(result, dict) else None
    return 1 if state == "incomplete" else 0
