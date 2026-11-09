"""The built-in catalogue (ADR-0018 decision 4; decisions log F5 (c)): ``data/materials.json``.

Twenty records, every one referenced. Fluid records were generated once from CoolProp at the
stated state (version, backend, fluid and pressure in ``generated``): their methods are the fits
the contract's candidate order chose within 1 %, verified on 201 points (the nodes, the
midpoints between them and both ends), and ``check`` keeps CoolProp's values at six
temperatures. Solid records carry constant (or, where the source gives an equation, polynomial)
properties, each value cited to a public source (NIST wherever NIST publishes it) and
cross-checked against a second public source in ``references``. Nothing is copied from textbook
or handbook tables.

The file ships inside the package and is read once per process, size-limited and validated
against the catalogue and the property node's parameters; a malformed file is a packaging
error and raises :class:`MaterialsError`.

Import rule: numpy (through ``evaluate``), ``stokes.model`` and this package.
"""

from __future__ import annotations

import copy
import json
import math
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

import numpy as np

from stokes.model.schema import NAME_RE, registry
from stokes.physics.materials import catalogue
from stokes.physics.materials.errors import FluidUnknown, MaterialsError
from stokes.physics.materials.evaluate import (
    Definition,
    State,
    definition_from_entry,
    evaluate_all,
    molar_mass_of,
)
from stokes.physics.materials.model import KIND_PROPERTIES, MATERIAL_KINDS
from stokes.physics.materials.providers.base import (
    Availability,
    LibraryInfo,
    LibraryRecord,
    PropertyGrid,
)

if TYPE_CHECKING:
    from stokes.physics.materials.methods import Array

DATA_FILE = "materials.json"
MAX_BYTES = 4 * 1024 * 1024
FORMAT = 1
KINDS: tuple[str, ...] = ("fluid", "solid", "any")

Record = dict[str, Any]  # Any: one record of the JSON catalogue


def _bad(message: str) -> MaterialsError:
    return MaterialsError(f"built-in catalogue: {message}", details={"file": DATA_FILE})


def _finite(value: Any) -> bool:  # Any: JSON value
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return False


def _check_record(record: Any, param_names: frozenset[str]) -> None:  # Any: raw JSON
    if not isinstance(record, dict):
        raise _bad("every record must be an object")
    rid = record.get("id")
    if not isinstance(rid, str) or not NAME_RE.match(rid):
        raise _bad(f"record id {rid!r} is not a valid name")
    kind = record.get("kind")
    if kind not in MATERIAL_KINDS:
        raise _bad(f"{rid}: kind {kind!r}")
    state = record.get("state")
    if not isinstance(state, dict) or not all(
        _finite(state.get(k)) for k in ("T_ref", "p_ref", "T_min", "T_max")
    ):
        raise _bad(f"{rid}: state needs T_ref, p_ref, T_min and T_max")
    props = record.get("properties")
    if not isinstance(props, dict) or set(props) != set(KIND_PROPERTIES[kind]):
        raise _bad(f"{rid}: properties must be exactly {', '.join(KIND_PROPERTIES[kind])}")
    for prop, entry in props.items():
        if not isinstance(entry, dict):
            raise _bad(f"{rid}.{prop} must be an object")
        spec = catalogue.method(prop, str(entry.get("method")))
        catalogue.check_choice(spec, kind)
        unknown = set(entry) - {"method"} - param_names
        if unknown:
            raise _bad(f"{rid}.{prop}: unknown parameters {sorted(unknown)}")
        if not all(_finite(v) for k, v in entry.items() if k != "method"):
            raise _bad(f"{rid}.{prop}: parameters must be finite numbers or lists of them")
    if not (record.get("references") or record.get("generated")):
        raise _bad(f"{rid}: every record names its source")


@cache
def load() -> tuple[Record, ...]:
    """The records, read and validated once per process."""
    data_file = resources.files("stokes.physics.materials").joinpath("data", DATA_FILE)
    raw = data_file.read_bytes()
    if len(raw) > MAX_BYTES:
        raise _bad(f"{len(raw)} bytes exceeds {MAX_BYTES}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise _bad(f"format must be {FORMAT}")
    records = data.get("records")
    if not isinstance(records, list):
        raise _bad("records must be a list")
    names = frozenset(p.name for p in registry.get("property").params)
    for record in records:
        _check_record(record, names)
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)):
        raise _bad("record ids must be unique")
    return tuple(records)


def catalogue_version() -> str:
    return f"{FORMAT}"


def record(fluid: str) -> Record:
    """A deep copy of one record; :class:`FluidUnknown` when the catalogue has none."""
    for found in load():
        if found["id"] == fluid:
            return copy.deepcopy(found)
    raise FluidUnknown(
        f"the built-in catalogue has no material {fluid!r}; see material.library_search",
        details={"provider": "builtin", "fluid": fluid},
    )


def state_of(found: Record) -> State:
    state = found["state"]
    definitions = definitions_of(found)
    return State(
        kind=found["kind"],
        T_ref=float(state["T_ref"]),
        p_ref=float(state["p_ref"]),
        T_min=float(state["T_min"]),
        T_max=float(state["T_max"]),
        molar_mass=molar_mass_of(definitions),
    )


def definitions_of(found: Record) -> dict[str, Definition]:
    return {prop: definition_from_entry(prop, entry) for prop, entry in found["properties"].items()}


def _reference(found: Record) -> dict[str, str]:
    generated = found.get("generated")
    if generated:
        refs = {
            "source": f"{generated['library']} {generated['version']} "
            f"{generated['backend']}::{generated['fluid']} at {generated['p']:g} Pa",
        }
        refs.update({k: str(v) for k, v in generated.get("references", {}).items()})
        return refs
    return {
        prop: str(cited["primary"]["source"])
        for prop, cited in found.get("references", {}).items()
        if isinstance(cited, dict) and "primary" in cited
    }


def library_record(found: Record) -> LibraryRecord:
    """The search form of a record."""
    molar = float(found["properties"].get("molar_mass", {}).get("value", 0.0))
    return LibraryRecord(
        provider="builtin",
        fluid=found["id"],
        name=found["name"],
        kind=found["kind"],
        formula=found.get("formula", ""),
        cas=found.get("cas", ""),
        aliases=tuple(found.get("aliases", ())),
        T_min=float(found["state"]["T_min"]),
        T_max=float(found["state"]["T_max"]),
        molar_mass=molar or None,
        properties={prop: prop in found["properties"] for prop in KIND_PROPERTIES["fluid"][:4]},
        reference=_reference(found),
    )


def _rank(found: Record, query: str) -> int | None:
    """0 for an exact id, name or alias; 1 for a CAS or formula; 2 for a substring; None for no
    match. The query is already folded."""
    if not query:
        return 0
    names = [found["id"], found["name"], *found.get("aliases", ())]
    folded = [n.casefold() for n in names]
    if query in folded:
        return 0
    if query in (found.get("cas", "").casefold(), found.get("formula", "").casefold()):
        return 1
    haystack = [
        *folded,
        found.get("formula", "").casefold(),
        found.get("description", "").casefold(),
    ]
    if any(query in text for text in haystack):
        return 2
    return None


class BuiltinProvider:
    """The ``builtin`` provider: always available, no dependencies."""

    name = "builtin"

    def available(self) -> Availability:
        return Availability(self.name, True, version=catalogue_version())

    def search(self, query: str, kind: str = "any") -> list[LibraryRecord]:
        if kind not in KINDS:
            raise MaterialsError(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")
        folded = query.strip().casefold()
        ranked: list[tuple[int, str, Record]] = []
        for found in load():
            if kind != "any" and found["kind"] != kind:
                continue
            rank = _rank(found, folded)
            if rank is not None:
                ranked.append((rank, found["name"].casefold(), found))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [library_record(found) for _, _, found in ranked]

    def info(
        self,
        fluid: str,
        composition: str = "",
        basis: str = "",
        *,
        T_ref: float = 300.0,  # noqa: N803 - the protocol's name; a record has its own state
        p_ref: float = 101325.0,
        phase: str = "auto",
    ) -> LibraryInfo:
        found = record(fluid)
        state = found["state"]
        detail: dict[str, Any] = {  # Any: JSON
            "description": found.get("description", ""),
            "phase": found.get("phase", "auto"),
            "state": state,
            "methods": found["properties"],
        }
        for key in ("generated", "check"):
            if key in found:
                detail[key] = found[key]
        return LibraryInfo(
            record=library_record(found),
            limits={
                "T_min": state["T_min"],
                "T_max": state["T_max"],
                "T_ref": state["T_ref"],
                "p_ref": state["p_ref"],
                "phase": found.get("phase", "auto"),
            },
            references=found.get("references") or found.get("generated", {}).get("references", {}),
            detail=detail,
        )

    def evaluate(
        self,
        fluid: str,
        composition: str,
        basis: str,
        phase: str,
        t: Array,
        p: float,
    ) -> PropertyGrid:
        """The record's own methods on the grid at ``p``; nothing is missing in this catalogue."""
        found = record(fluid)
        kinds = list(KIND_PROPERTIES[found["kind"]])
        pressures = np.full_like(t, p)
        values = evaluate_all(definitions_of(found), state_of(found), kinds, t, pressures)
        return PropertyGrid(t=t, p=p, values=values, missing=[])
