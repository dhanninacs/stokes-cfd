"""The one API (ADR-0010): command registry, sessions, events, journals.

Every client — Qt UI, CLI, HTTP server, MCP server, assistant — calls
:meth:`Session.execute` with a command name and JSON-able parameters. Command
schemas are generated from the registry and the node schemas; nothing is
hand-written twice.

Import rule: headless. The core modules import ``stokes.model`` and
``stokes.units`` only; a command module may also import the subsystem it exposes
(``stokes.workspace``, ``stokes.geometry``), heavy parts at the point of use.
"""

from stokes.api.events import EventBus
from stokes.api.journal import Journal, replay
from stokes.api.registry import CommandContext, CommandDef, CommandRegistry, ParamDef, command
from stokes.api.session import Session

__all__ = [
    "CommandContext",
    "CommandDef",
    "CommandRegistry",
    "EventBus",
    "Journal",
    "ParamDef",
    "Session",
    "command",
    "default_registry",
    "replay",
]


def default_registry() -> CommandRegistry:
    """The registry with every built-in command loaded."""
    from stokes.api import commands as _commands  # noqa: F401 - registers on import
    from stokes.api.registry import registry

    return registry
