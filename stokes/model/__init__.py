"""The project model: typed tree, node schemas, commands with undo, persistence.

Import rule: headless. This package imports ``stokes.units`` and nothing from
``stokes.api`` or any client (ADR-0006, docs/02 §4).

One thing beside ``stokes.units`` is imported at start-up: ``stokes.model.schema`` registers the
built-in node types when it is first imported, and those include the study types of
``stokes.explore.model`` (ADR-0016), so every project knows them. That module imports only
``stokes.units.dimensions`` and the standard library: no numpy, nothing else from
``stokes.explore``.
"""

from stokes.model.commands import (
    AddNode,
    Command,
    Composite,
    ConvertGroup,
    RemoveNode,
    RenameNode,
    SetDisplayUnits,
    SetParam,
    UndoStack,
    command_from_record,
)
from stokes.model.nodes import Node, QuantityValue
from stokes.model.persist import SCHEMA_VERSION, load_project, save_project
from stokes.model.project import Project
from stokes.model.schema import NodeSpec, NodeTypeRegistry, ParamSpec, registry

__all__ = [
    "SCHEMA_VERSION",
    "AddNode",
    "Command",
    "Composite",
    "ConvertGroup",
    "Node",
    "NodeSpec",
    "NodeTypeRegistry",
    "ParamSpec",
    "Project",
    "QuantityValue",
    "RemoveNode",
    "RenameNode",
    "SetDisplayUnits",
    "SetParam",
    "UndoStack",
    "command_from_record",
    "load_project",
    "registry",
    "save_project",
]
