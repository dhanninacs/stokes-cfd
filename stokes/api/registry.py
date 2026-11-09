"""Command definitions and the registry that generates their schemas."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from stokes.api import legacy
from stokes.errors import UnknownCommand, ValidationError

if TYPE_CHECKING:
    from stokes.api.session import Session

ParamType = Literal["string", "number", "integer", "boolean", "object", "array", "any"]
COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class ParamDef:
    """One parameter of a command. A null value counts as not given, unless ``nullable``: then
    an explicit null reaches the handler (``parameter.set`` clears an optional reference with
    it, ADR-0022 decision 2.8)."""

    name: str
    type: ParamType = "string"
    required: bool = True
    description: str = ""
    default: Any = None  # Any: JSON default
    nullable: bool = False

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"description": self.description}
        if self.type != "any":
            schema["type"] = [self.type, "null"] if self.nullable else self.type
        if self.default is not None:
            schema["default"] = self.default
        return schema

    def check(self, value: Any) -> Any:  # Any: JSON value in, JSON value out
        checks: dict[str, Callable[[Any], bool]] = {
            "string": lambda v: isinstance(v, str),
            "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "any": lambda v: True,
        }
        if not checks[self.type](value):
            raise ValidationError(
                f"parameter {self.name!r} must be {self.type}, got {type(value).__name__}",
                details={"parameter": self.name, "expected": self.type},
            )
        return value


@dataclass
class CommandContext:
    """What a handler receives besides its parameters."""

    session: Session
    journal_extra: dict[str, Any] = field(default_factory=dict)  # Any: JSON

    def record(self, **extra: Any) -> None:  # Any: JSON
        """Add generated values (ids, indexes) to the journal record so replay is exact."""
        self.journal_extra.update(extra)


Handler = Callable[..., Any]


@dataclass(frozen=True)
class CommandDef:
    name: str
    handler: Handler
    params: tuple[ParamDef, ...] = ()
    doc: str = ""
    mutates: bool = True
    requires_project: bool = True
    feature: str | None = None

    def __post_init__(self) -> None:
        if not COMMAND_NAME_RE.match(self.name):
            raise ValidationError(f"invalid command name {self.name!r} (use area.verb)")
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValidationError(f"duplicate parameter names in command {self.name!r}")

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
        known = {p.name: p for p in self.params}
        unknown = sorted(set(params) - set(known))
        if unknown:
            raise ValidationError(
                f"command {self.name!r} has no parameter(s) {', '.join(unknown)}; "
                f"known: {', '.join(known) or 'none'}",
                details={"unknown": unknown},
            )
        result: dict[str, Any] = {}
        for pdef in self.params:
            if pdef.name in params and params[pdef.name] is not None:
                result[pdef.name] = pdef.check(params[pdef.name])
            elif pdef.nullable and pdef.name in params:
                result[pdef.name] = None
            elif pdef.required and pdef.default is None:
                raise ValidationError(
                    f"command {self.name!r} needs parameter {pdef.name!r}",
                    details={"parameter": pdef.name},
                )
            elif pdef.default is not None:
                result[pdef.name] = pdef.default
        return result

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.doc,
            "mutates": self.mutates,
            "requires_project": self.requires_project,
            "feature": self.feature,
            "input_schema": {
                "type": "object",
                "properties": {p.name: p.json_schema() for p in self.params},
                "required": [p.name for p in self.params if p.required and p.default is None],
                "additionalProperties": False,
            },
        }


class CommandRegistry:
    """Command definitions by name.

    A renamed command keeps its old name as an alias (:meth:`alias`, ADR-0021 decision 2.4), so
    old journals and clients still work: :meth:`get` resolves an alias, while :meth:`names` and
    the schema list current names only and the session journals the current name. A name is a
    command or an alias, never both.
    """

    def __init__(self) -> None:
        self._defs: dict[str, CommandDef] = {}
        self._aliases: dict[str, str] = {}

    def register(self, definition: CommandDef, *, replace: bool = False) -> CommandDef:
        if definition.name in self._aliases:
            current = self.canonical(definition.name)
            raise ValidationError(
                f"command {definition.name!r} is the old name of {current!r}",
                details={"command": definition.name, "alias_of": current},
            )
        if definition.name in self._defs and not replace:
            raise ValidationError(f"command {definition.name!r} already registered")
        self._defs[definition.name] = definition
        return definition

    def alias(self, old: str, new: str) -> None:
        """Make ``old``, the name of a renamed command, stand for ``new``.

        ``new`` may be registered later, and may itself be renamed later: aliases chain. Giving
        the same pair again changes nothing.
        """
        for name in (old, new):
            if not COMMAND_NAME_RE.match(name):
                raise ValidationError(f"invalid command name {name!r} (use area.verb)")
        if old in self._defs:
            raise ValidationError(
                f"command {old!r} is registered; only the name of a renamed command is an alias",
                details={"command": old},
            )
        if self._aliases.get(old, new) != new:
            raise ValidationError(
                f"command {old!r} is the old name of {self._aliases[old]!r} already",
                details={"command": old, "alias_of": self._aliases[old]},
            )
        if self.canonical(new) == old:
            raise ValidationError(f"command alias {old!r} -> {new!r} would make a cycle")
        self._aliases[old] = new

    def canonical(self, name: str) -> str:
        """The current name of ``name``: the command an alias leads to, else the name itself."""
        while name in self._aliases:  # acyclic: alias() refuses a cycle
            name = self._aliases[name]
        return name

    def aliases(self) -> dict[str, str]:
        """Every alias with the current name it resolves to."""
        return {old: self.canonical(old) for old in sorted(self._aliases)}

    def get(self, name: str) -> CommandDef:
        current = self.canonical(name)
        try:
            return self._defs[current]
        except KeyError as exc:
            details: dict[str, Any] = {"command": name, "known": self.names()}  # Any: JSON
            renamed = ""
            if current != name:
                details["alias_of"] = current
                renamed = f" (the old name of {current!r})"
            raise UnknownCommand(f"unknown command {name!r}{renamed}", details=details) from exc

    def names(self) -> list[str]:
        """The registered commands; aliases are not listed."""
        return sorted(self._defs)

    def json_schema(self) -> list[dict[str, Any]]:
        """Every command's schema. ``aliases`` lists the old names that resolve to the command."""
        old_names: dict[str, list[str]] = {}
        for old, current in self.aliases().items():
            old_names.setdefault(current, []).append(old)
        return [
            {**self._defs[n].json_schema(), "aliases": old_names.get(n, [])} for n in self.names()
        ]


def _with_old_names(target: CommandRegistry) -> CommandRegistry:
    """``target`` with the old names of renamed commands (``stokes.api.legacy.COMMANDS``)."""
    for old, new in legacy.COMMANDS.items():
        target.alias(old, new)
    return target


registry = _with_old_names(CommandRegistry())
"""The default registry: the built-in commands register into it when ``stokes.api.commands`` is
imported, and the old names of renamed commands resolve in it from the start."""


def command(
    name: str,
    *,
    params: tuple[ParamDef, ...] = (),
    doc: str = "",
    mutates: bool = True,
    requires_project: bool = True,
    feature: str | None = None,
    into: CommandRegistry | None = None,
) -> Callable[[Handler], Handler]:
    """Register ``fn(ctx, **params)`` as a command."""

    def decorate(fn: Handler) -> Handler:
        (into or registry).register(
            CommandDef(
                name,
                fn,
                params,
                doc or (fn.__doc__ or "").strip(),
                mutates,
                requires_project,
                feature,
            ),
            replace=True,
        )
        return fn

    return decorate
