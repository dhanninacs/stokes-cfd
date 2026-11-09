"""``stokes tutorial`` and ``stokes case``: OpenFOAM tutorials and imported cases (ADR-0029, T-316).

One API command per verb, JSON output like the other verbs; a verb that changes the project saves
it afterwards. A key path is written with ``/`` between keys, and a value is JSON when it parses
as JSON (``3``, ``[1, 0, 0]``, ``{"tokens": ["uniform", [1, 0, 0]]}``) and text otherwise (a word,
or a quantity such as ``"0.5 [s]"`` for a known physical key)::

    stokes tutorial list --category incompressibleFluid
    stokes tutorial open incompressibleFluid/cavity
    stokes case import demo.stokes --tutorial incompressibleFluid/cavity
    stokes case set demo.stokes /cases/cavity system/controlDict endTime "2 [s]"
    stokes case set demo.stokes /cases/cavity system/controlDict PISO/nCorrectors 3
    stokes case diff demo.stokes /cases/cavity
    stokes case run demo.stokes /cases/cavity
    stokes case results demo.stokes --time 0.5
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

#: How long `case results --time` waits for the host to read the fields at that time.
RESULTS_WAIT_S = 120.0

CASE_VERBS: tuple[tuple[str, str], ...] = (
    ("import", "import an OpenFOAM case from the tutorial folder or a folder (one undo step)"),
    ("settings", "one file of a case as it will be staged"),
    ("set", "record one edit to a case's settings"),
    ("reset", "drop the edits to a file, or to one key of it"),
    ("diff", "every edit as a unified diff against the copy"),
    ("run", "run a case on a host and wait for it"),
    ("results", "the fields of the newest run, optionally at another written time"),
)


def add_parsers(sub: Any) -> None:  # Any: argparse subparsers action
    """Add ``tutorial`` and ``case`` with their verbs."""
    tutorial = sub.add_parser("tutorial", help="the OpenFOAM tutorials of the install")
    verbs = tutorial.add_subparsers(dest="tutorial_cmd", required=True)
    listing = verbs.add_parser("list", help="the tutorials, with whether this install can run each")
    listing.add_argument("--category", default=None, help="only this top-level folder")
    listing.add_argument("--text", default=None, help="only cases whose words contain this")
    listing.add_argument("--root", default=None, help="a tutorial folder other than the active one")
    opened = verbs.add_parser("open", help="a tutorial in a new project of its own")
    opened.add_argument("tutorial", help="a tutorial id, such as incompressibleFluid/cavity")
    opened.add_argument("--path", default=None, help="the new project's folder")

    case = sub.add_parser("case", help="imported OpenFOAM cases")
    case_verbs = case.add_subparsers(dest="case_cmd", required=True)
    parsers = {verb: case_verbs.add_parser(verb, help=text) for verb, text in CASE_VERBS}
    for verb, parser in parsers.items():
        parser.add_argument("project", help="the .stokes project")
        if verb not in ("import", "results"):
            parser.add_argument("case", help="the case node, such as /cases/cavity")
    source = parsers["import"].add_mutually_exclusive_group(required=True)
    source.add_argument("--tutorial", default=None, help="a tutorial id")
    source.add_argument("--folder", default=None, help="an absolute case folder")
    parsers["import"].add_argument("--name", default=None, help="the tree name")
    for verb in ("settings", "set", "reset"):
        parsers[verb].add_argument("file", help="a file of the case, such as system/controlDict")
    parsers["set"].add_argument("key", help="the key path, keys separated by /")
    parsers["set"].add_argument("value", help="JSON, or text (a word or a quantity)")
    parsers["reset"].add_argument("key", nargs="?", default=None, help="one key path; default all")
    parsers["run"].add_argument("--host", default=None, help="the host; default the settings'")
    parsers["run"].add_argument("--force", action="store_true", help="run even if reusable")
    parsers["results"].add_argument("--run", default=None, help="the run; default the newest")
    parsers["results"].add_argument("--time", type=float, default=None, help="a written time")


def _value(text: str) -> Any:  # Any: JSON value or text
    """JSON when the text is JSON, else the text itself (a word or quantity text)."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _keys(text: str) -> list[str]:
    return [key for key in text.split("/") if key]


def dispatch(session: Any, args: argparse.Namespace) -> tuple[Any, bool]:  # Any: Session
    """Run one verb; returns the result and whether the project changed (to be saved)."""
    if args.cmd == "tutorial":
        if args.tutorial_cmd == "list":
            params = {
                k: v for k, v in vars(args).items() if k in ("category", "text", "root") and v
            }
            return session.execute("tutorial.catalogue", **params), False
        extra = {"path": args.path} if args.path else {}
        return session.execute("tutorial.open", tutorial=args.tutorial, **extra), True
    session.execute("project.open", path=args.project)
    match args.case_cmd:
        case "import":
            params = {"tutorial": args.tutorial, "folder": args.folder, "name": args.name}
            reply = session.execute("case.import", **{k: v for k, v in params.items() if v})
            return reply, reply.get("status") != "refused"
        case "settings":
            return session.execute("case.settings", node=args.case, file=args.file), False
        case "set":
            reply = session.execute(
                "case.set",
                node=args.case,
                file=args.file,
                path=_keys(args.key),
                value=_value(args.value),
            )
            return reply, True
        case "reset":
            extra = {"path": _keys(args.key)} if args.key else {}
            return session.execute("case.reset", node=args.case, file=args.file, **extra), True
        case "diff":
            return session.execute("case.diff", node=args.case), False
        case "run":
            extra = {"host": args.host} if args.host else {}
            reply = session.execute(
                "run.start", case=args.case, force=args.force, wait=True, **extra
            )
            status = session.execute("run.status", run=reply["run"])
            return {**reply, "state": status["state"], "iterations": status.get("iterations")}, True
        case _:
            return _results(session, args), False


def _results(session: Any, args: argparse.Namespace) -> Any:  # Any: Session; JSON reply
    """`run.fields`, after `run.time` and a wait for its answer when ``--time`` is given."""
    run = args.run or (session.execute("run.list")["runs"] or [{}])[0].get("id")
    if args.time is None:
        return session.execute("run.fields", run=run)
    session.execute("run.time", run=run, time=args.time)
    deadline = time.monotonic() + RESULTS_WAIT_S
    fields = session.execute("run.fields", run=run)
    while abs(float(fields.get("time") or 0.0) - args.time) > 1e-9 * max(1.0, abs(args.time)):
        if time.monotonic() > deadline:
            break
        time.sleep(0.5)
        fields = session.execute("run.fields", run=run)
        nearest = min(fields.get("times") or [args.time], key=lambda t: abs(t - args.time))
        if abs(float(fields.get("time") or 0.0) - nearest) < 1e-12:
            break
    return fields
