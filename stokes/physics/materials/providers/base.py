"""The provider interface (ADR-0018 decision 6; contract "Providers").

One class per provider implements :class:`Provider`; ``providers.get_provider(name)`` imports it
lazily. A provider answers four questions: is it available, which materials match a query, what
is known about one material (at a reference state), and what are its properties on a
temperature grid at one pressure. Evaluation never raises for a property the provider has no
model for: it lists it in :attr:`PropertyGrid.missing`. :func:`rank` is the one search order
every provider's records are sorted by (exact names and aliases, then CAS and formula, then
substrings).

Import rule: the standard library only (numpy types for annotations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from stokes.physics.materials.methods import Array


@dataclass(frozen=True)
class Availability:
    """Whether a provider can be used here, its version, and why not when it cannot."""

    name: str
    available: bool
    version: str = ""
    reason: str = ""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "name": self.name,
            "available": self.available,
            "version": self.version,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LibraryRecord:
    """One material a provider offers, as ``material.library_search`` returns it."""

    provider: str
    fluid: str
    name: str
    kind: str
    formula: str = ""
    cas: str = ""
    aliases: tuple[str, ...] = ()
    T_min: float | None = None
    T_max: float | None = None
    p_max: float | None = None
    T_critical: float | None = None
    p_critical: float | None = None
    molar_mass: float | None = None
    properties: dict[str, bool] = field(default_factory=dict)
    composition: str | None = None
    basis: str | None = None
    reference: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # Any: provider-specific JSON fields
    """Provider-specific fields shown with the record: CoolProp's ``refprop_name``, an INCOMP
    solution's ``fraction_min`` and ``fraction_max``, a Cantera mechanism's ``species`` and
    ``example_composition``."""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            **self.extra,
            "provider": self.provider,
            "fluid": self.fluid,
            "name": self.name,
            "formula": self.formula,
            "cas": self.cas,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "T_min": self.T_min,
            "T_max": self.T_max,
            "p_max": self.p_max,
            "T_critical": self.T_critical,
            "p_critical": self.p_critical,
            "molar_mass": self.molar_mass,
            "properties": dict(self.properties),
            "composition": self.composition,
            "basis": self.basis,
            "reference": dict(self.reference),
        }


@dataclass(frozen=True)
class LibraryInfo:
    """Everything a provider knows about one material: the record, its limits at the reference
    state, its references, and provider-specific detail (the built-in catalogue's methods and
    check values)."""

    record: LibraryRecord
    limits: dict[str, Any] = field(default_factory=dict)  # Any: JSON
    references: dict[str, Any] = field(default_factory=dict)  # Any: JSON
    detail: dict[str, Any] = field(default_factory=dict)  # Any: JSON

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            **self.record.to_json(),
            "limits": self.limits,
            "references": self.references,
            **self.detail,
        }


@dataclass
class PropertyGrid:
    """Properties on a temperature grid at one pressure, SI, one array per property kind."""

    t: Array
    p: float
    values: dict[str, Array] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    """The phases the provider found on the grid (empty when it does not say)."""


class Provider(Protocol):
    """A source of material properties (built-in catalogue, CoolProp, Cantera, REFPROP)."""

    name: str

    def available(self) -> Availability: ...

    def search(self, query: str, kind: str) -> list[LibraryRecord]: ...

    def info(
        self,
        fluid: str,
        composition: str = "",
        basis: str = "",
        *,
        T_ref: float = 300.0,  # noqa: N803 - the contract's name
        p_ref: float = 101325.0,
        phase: str = "auto",
    ) -> LibraryInfo: ...

    def evaluate(
        self,
        fluid: str,
        composition: str,
        basis: str,
        phase: str,
        t: Array,
        p: float,
    ) -> PropertyGrid: ...


def rank(record: LibraryRecord, folded_query: str) -> int | None:
    """0 for an exact id, name or alias; 1 for a CAS number or formula; 2 for a substring of
    those; None for no match. ``folded_query`` is already ``casefold``-ed; empty matches all."""
    if not folded_query:
        return 0
    names = [n.casefold() for n in (record.fluid, record.name, *record.aliases)]
    if folded_query in names:
        return 0
    if folded_query in (record.cas.casefold(), record.formula.casefold()):
        return 1
    haystack = [*names, record.formula.casefold()]
    if any(folded_query in text for text in haystack):
        return 2
    return None
