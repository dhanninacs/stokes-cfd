"""Tree nodes and the stored form of a quantity parameter."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.errors import PersistenceError, StokesError

if TYPE_CHECKING:
    from stokes.model.schema import NodeTypeRegistry

STATUSES: tuple[str, ...] = ("ok", "warning", "stale", "failed", "new")


@dataclass
class QuantityValue:
    """A quantity parameter as stored: SI magnitude, SI unit, and the expression text if any."""

    value: float
    unit: str
    expr: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"value": self.value, "unit": self.unit}
        if self.expr is not None:
            data["expr"] = self.expr
        return data

    @classmethod
    def from_json(cls, data: Any) -> QuantityValue:  # Any: raw JSON
        if not isinstance(data, dict) or "value" not in data:
            raise PersistenceError("quantity value must be an object with 'value'")
        expr = data.get("expr")
        return cls(float(data["value"]), str(data.get("unit", "")), str(expr) if expr else None)


@dataclass(eq=False)
class Node:
    """A node of the project tree. Mutate only through :class:`stokes.model.Project`."""

    id: str
    type: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)  # Any: per-kind JSON-able values
    children: list[Node] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False, compare=False)
    status: str = "ok"
    meta: dict[str, Any] = field(default_factory=dict)  # Any: free-form, persisted

    @property
    def path(self) -> str:
        parts: list[str] = []
        node: Node | None = self
        while node is not None and node.parent is not None:
            parts.append(node.name)
            node = node.parent
        return "/" + "/".join(reversed(parts))

    @property
    def depth(self) -> int:
        depth = 0
        node = self.parent
        while node is not None:
            depth += 1
            node = node.parent
        return depth

    def child(self, name: str) -> Node | None:
        for c in self.children:
            if c.name == name:
                return c
        return None

    def walk(self) -> Iterator[Node]:
        """Pre-order traversal including this node."""
        yield self
        for c in self.children:
            yield from c.walk()

    def _own_json(self) -> dict[str, Any]:
        """The node's own fields in JSON form, without its children."""
        params = {
            k: (v.to_json() if isinstance(v, QuantityValue) else v) for k, v in self.params.items()
        }
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "status": self.status,
            "params": params,
        }
        if self.meta:
            data["meta"] = self.meta
        return data

    def to_json(self) -> dict[str, Any]:
        data = self._own_json()
        if self.children:
            data["children"] = [c.to_json() for c in self.children]
        return data

    @classmethod
    def from_json(
        cls,
        data: Any,  # Any: raw JSON
        parent: Node | None = None,
        registry: NodeTypeRegistry | None = None,
    ) -> Node:
        if not isinstance(data, dict):
            raise PersistenceError("node must be an object")
        for key in ("id", "type", "name"):
            if not isinstance(data.get(key), str):
                raise PersistenceError(f"node is missing a string {key!r}")
        params_raw = data.get("params", {})
        if not isinstance(params_raw, dict):
            raise PersistenceError(f"node {data['id']}: params must be an object")
        params: dict[str, Any] = {}
        for k, v in params_raw.items():
            params[k] = (
                QuantityValue.from_json(v) if _is_quantity(registry, data["type"], k, v) else v
            )
        node = cls(
            id=data["id"],
            type=data["type"],
            name=data["name"],
            params=params,
            parent=parent,
            status=str(data.get("status", "ok")),
            meta=dict(data.get("meta", {})),
        )
        for child in data.get("children", []):
            node.children.append(cls.from_json(child, node, registry))
        return node

    def summary(self) -> dict[str, Any]:
        """The compact form returned by queries: no children, params in JSON form.

        Built from the node's own fields: serialising the subtree to drop it again cost a group
        of 4,500 parts a full walk on every ``node.remove``, ``node.get`` and event.
        """
        data = self._own_json()
        data["path"] = self.path
        data["children_count"] = len(self.children)
        return data


def _is_quantity(registry: NodeTypeRegistry | None, node_type: str, name: str, value: Any) -> bool:
    """Whether a stored parameter is a quantity object and not ``json`` data that looks like one.

    A node type's spec settles it (a ``json`` parameter is never a ``QuantityValue``). Without a
    registry (a plain tree read) the old test stands: an object with ``value`` is a quantity.
    """
    if not isinstance(value, dict) or "value" not in value:
        return False
    if registry is None:
        return True
    try:
        return registry.get(node_type).param(name).kind == "quantity"
    except StokesError:  # an unknown type or parameter: fall back to the shape, as before
        return True
