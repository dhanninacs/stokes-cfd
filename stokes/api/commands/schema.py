"""schema.* and journal.* commands: introspection and replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stokes import __version__
from stokes.api.journal import Journal, replay
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.units import DIMENSIONS, UNIT_SYSTEMS


@command("schema.commands", mutates=False, requires_project=False)
def schema_commands(ctx: CommandContext) -> list[dict[str, Any]]:
    """Every command with its input schema (the source for MCP tools and OpenAPI)."""
    return ctx.session.registry.json_schema()


@command("schema.units", mutates=False, requires_project=False)
def schema_units(ctx: CommandContext) -> dict[str, Any]:
    """Dimensions with SI and display units, and the unit systems."""
    return {
        "unit_systems": list(UNIT_SYSTEMS),
        "dimensions": {
            name: {"si_unit": d.si_unit, "display": d.display} for name, d in DIMENSIONS.items()
        },
    }


@command("schema.version", mutates=False, requires_project=False)
def schema_version(ctx: CommandContext) -> dict[str, Any]:
    """Stokes version and licensed features."""
    return {"stokes": __version__, "features": sorted(ctx.session.features.flags)}


@command(
    "journal.replay",
    params=(ParamDef("path", "string", description="A .jsonl journal file."),),
)
def journal_replay(ctx: CommandContext, path: str) -> dict[str, Any]:
    """Replay a journal into the open project."""
    records = Journal.read(Path(path))
    results = replay(ctx.session, records)
    return {"replayed": len(results)}
