"""Old names that keep working after a rename (ADR-0021 decisions 2.3 and 2.4).

Journals hold command names, parameter names and node paths; projects hold node types and paths.
A rename must not break what was written before it, so each table below maps an old name to the
current one, and the API resolves it wherever the old name can come in:

- ``COMMANDS``, old command name to current: aliases in the default command registry
  (:meth:`~stokes.api.registry.CommandRegistry.alias`). ``get`` resolves them, the schema lists
  them under ``aliases`` of their command, and :meth:`Session.execute` journals the current name.
- ``PARAMETERS``, current command name to ``{old parameter name: current}``: renamed by
  :func:`rename_parameters` in :meth:`Session.execute` before it validates.
- ``PATHS``, old path prefix to current: rewritten by :func:`rewrite_params` in
  :meth:`Session.execute` before it validates, so the journal holds the current path.
- ``NODE_TYPES``, old node type to current: the very dict
  :data:`stokes.model.schema.NODE_TYPE_ALIASES`, listed here with the others. The model needs it
  (registries resolve it, ``AddNode`` stores the current type, a version-1 project is migrated
  through it) and may not import the API, so it is defined, and filled, there.

A name chains: if a current name is renamed again later, add the new pair and keep the old one.
The task that renames fills the tables (T-015 the tags, T-016 the import command). Add entries to
the literals and never rebind a table: the registry and the session hold the dicts themselves.

Import rule: the standard library, ``stokes.errors`` and ``stokes.model.schema`` only.
``stokes.api.session`` imports this module, and ``import stokes.api`` has a time budget.
"""

from __future__ import annotations

from typing import Any

from stokes.errors import ValidationError
from stokes.model.schema import NODE_TYPE_ALIASES

COMMANDS: dict[str, str] = {
    "selection.create": "tag.create",
    "selection.add": "tag.add",
    "selection.remove": "tag.remove",
    "selection.set": "tag.set",
    "selection.invert": "tag.invert",
    "selection.grow": "tag.grow",
    "selection.rematch": "tag.rematch",
    "selection.rename": "tag.rename",
    "selection.delete": "tag.delete",
    "selection.list": "tag.list",
    "selection.members": "tag.members",
    "selection.by_color": "tag.by_color",
    "selection.by_name": "tag.by_name",
    "geometry.import_step": "geometry.import",
}
"""Old command name to current name (T-015: the 13 ``selection.*`` commands became ``tag.*``;
T-016: the import command became ``geometry.import``, STEP, IGES and BREP alike)."""

PARAMETERS: dict[str, dict[str, str]] = {
    "tag.add": {"selection": "tag"},
    "tag.remove": {"selection": "tag"},
    "tag.set": {"selection": "tag"},
    "tag.invert": {"selection": "tag"},
    "tag.grow": {"selection": "tag"},
    "tag.rematch": {"selection": "tag"},
    "tag.rename": {"selection": "tag"},
    "tag.delete": {"selection": "tag"},
    "tag.members": {"selection": "tag"},
    "geometry.export_stl": {"selections": "tags"},
}
"""Current command name to ``{old parameter name: current parameter name}``."""

PATHS: dict[str, str] = {"/geometry/named_selections": "/geometry/tags"}
"""Old node path prefix to current prefix, both absolute (``/geometry/...``) and without a
trailing ``/``."""

NODE_TYPES: dict[str, str] = NODE_TYPE_ALIASES
"""Old node type to current type: :data:`stokes.model.schema.NODE_TYPE_ALIASES` itself."""


def rename_parameters(command: str, params: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    """``params`` of ``command`` (its current name) with old parameter names replaced.

    Giving a parameter under both its old and its current name is a validation error. Returns
    ``params`` itself when the command has no renamed parameters.
    """
    renames = PARAMETERS.get(command)
    if not renames:
        return params
    result = dict(params)
    for old, new in renames.items():
        if old in result:
            if new in result:
                raise ValidationError(
                    f"command {command!r} got both {old!r} and {new!r}; "
                    f"{old!r} is the old name of {new!r}, give one",
                    details={"parameter": new, "old": old},
                )
            result[new] = result.pop(old)
    return result


def rewrite_params(params: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    """``params`` with every path under an old prefix moved to the current prefix.

    A string parameter, and a string item of a list parameter, is rewritten when it equals an old
    prefix or starts with one followed by ``/``. Nothing else is touched: other strings, node ids,
    nested objects. Returns ``params`` itself when there are no old paths.
    """
    if not PATHS:
        return params
    return {key: _rewrite_value(value) for key, value in params.items()}


def rewrite_path(value: str) -> str:
    """``value`` with an old path prefix replaced by the current one; other values unchanged."""
    for _ in range(len(PATHS)):  # follows a prefix renamed again later; a cycle stops here
        for old in sorted(PATHS, key=len, reverse=True):  # the longest prefix wins
            if value == old or value.startswith(old + "/"):
                value = PATHS[old] + value[len(old) :]
                break
        else:
            break
    return value


def _rewrite_value(value: Any) -> Any:  # Any: one JSON parameter value
    if isinstance(value, str):
        return rewrite_path(value)
    if isinstance(value, list):
        return [rewrite_path(item) if isinstance(item, str) else item for item in value]
    return value
