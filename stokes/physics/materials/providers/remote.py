"""The library providers, as the API process sees them (ADR-0018 decision 6; contract
"Providers"): CoolProp HEOS (``coolprop``), CoolProp INCOMP (``coolprop_incomp``), Cantera
(``cantera``) and REFPROP through CoolProp (``refprop``).

Each is a :class:`RemoteProvider`: it implements the ``Provider`` protocol by asking the
materials worker process (:mod:`stokes.physics.materials.worker`), where the libraries are
imported. Whether a provider is installed is read from the package metadata without importing
it. A provider's searchable index (every fluid with its limits and which transport models it
has) is built once by the worker and kept in memory and on disk under
``<STOKES_HOME or ~/.stokes>/cache/materials``, keyed by the library's version (and, for
REFPROP, its folder and version), so a search after the first one costs nothing. Evaluations
travel in chunks of about 0.2 s, so a long one reports progress and can be cancelled between
chunks.

Import rule: the standard library, numpy and this package; never CoolProp or Cantera.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import time
from collections.abc import Callable
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np

from stokes import workspace_root
from stokes.errors import ValidationError
from stokes.log import fields, get_logger
from stokes.physics.materials import worker
from stokes.physics.materials.errors import OutOfRange, ProviderMissing
from stokes.physics.materials.methods import Array
from stokes.physics.materials.providers import refprop
from stokes.physics.materials.providers.base import (
    Availability,
    LibraryInfo,
    LibraryRecord,
    PropertyGrid,
    rank,
)
from stokes.physics.materials.providers.coolprop import crossing

log = get_logger(__name__)

LIBRARY: tuple[str, ...] = ("coolprop", "coolprop_incomp", "cantera", "refprop")
PACKAGES: dict[str, str] = {
    "coolprop": "CoolProp",
    "coolprop_incomp": "CoolProp",
    "refprop": "CoolProp",
    "cantera": "cantera",
}
INSTALL_HINT = "pip install 'stokes-cfd[materials]'"
CHUNK_SECONDS = 0.2
FIRST_CHUNK = 16
MAX_CHUNK = 2048
INDEX_FORMAT = 1
MAX_INDEX_BYTES = 16 * 1024 * 1024
COST_PER_STATE: dict[str, float] = {
    "coolprop": 1.5e-4,
    "coolprop_mixture": 0.085,
    "coolprop_incomp": 2e-5,
    "cantera": 6e-4,
    "refprop": 2e-4,
    "refprop_mixture": 2e-3,
}
"""Seconds per state the scheduler assumes before it has measured (contract "Cost": HEOS pure
25-150 us, HEOS mixtures 8-85 ms; Cantera and REFPROP measured 2026-10-04)."""
_RECORD_KEYS = frozenset(
    {
        "provider",
        "fluid",
        "name",
        "kind",
        "formula",
        "cas",
        "aliases",
        "T_min",
        "T_max",
        "p_max",
        "T_critical",
        "p_critical",
        "molar_mass",
        "properties",
        "composition",
        "basis",
        "reference",
    }
)


class _Known:
    """What this process has learnt from the worker: its provider report and the indexes."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.report: dict[str, Any] | None = None  # Any: JSON
        self.indexes: dict[str, tuple[LibraryRecord, ...]] = {}


_known = _Known()


@cache
def _found(package: str) -> bool:
    return importlib.util.find_spec(package) is not None


def installed(name: str) -> bool:
    """Whether the provider's package is importable here (without importing it)."""
    return _found(PACKAGES[name])


def package_version(name: str) -> str:
    try:
        return metadata.version(PACKAGES[name])
    except metadata.PackageNotFoundError:
        return ""


def require_installed(name: str) -> None:
    if not installed(name):
        raise ProviderMissing(
            f"provider {name!r} needs {PACKAGES[name]}, which is not installed; {INSTALL_HINT}",
            details={"provider": name, "package": PACKAGES[name]},
        )


def cost_per_state(provider: str, fluid: str) -> float:
    mixture = "&" in fluid or fluid.lower().endswith(".mix")
    key = f"{provider}_mixture" if mixture and f"{provider}_mixture" in COST_PER_STATE else provider
    return COST_PER_STATE.get(key, 1e-3)


# ---------------------------------------------------------------- the provider report


def report(*, wait: bool = True) -> dict[str, Any] | None:  # Any: JSON
    """The worker's report on every library provider: versions, fluid counts, REFPROP's
    detection. None when it is not known yet and ``wait`` is false."""
    with _known.lock:
        if _known.report is not None:
            return _known.report
    if not wait or (not installed("coolprop") and not installed("cantera")):
        return None
    reply = worker.request({"type": "providers"}, refprop_path=refprop.settings_path())
    found = reply.get("providers")
    if not isinstance(found, dict):
        raise ValidationError("the materials worker sent a malformed provider report")
    with _known.lock:
        _known.report = found
    return found


def known_report() -> dict[str, Any] | None:  # Any: JSON
    with _known.lock:
        return _known.report


def set_refprop_path(path: str | None) -> bool:
    """Change the REFPROP settings path; True when it changed (the worker restarts to load
    REFPROP from it, and REFPROP's report and index are forgotten)."""
    before = refprop.settings_path()
    refprop.set_settings_path(path)
    if refprop.settings_path() == before:
        return False
    with _known.lock:
        _known.report = None
        _known.indexes.pop("refprop", None)
    worker.shutdown()
    return True


def refprop_detection(*, wait: bool = True) -> dict[str, Any] | None:  # Any: JSON
    found = report(wait=wait)
    if found is None:
        return None
    detection = found.get("refprop")
    return detection if isinstance(detection, dict) else None


def availability(name: str, *, wait: bool = True) -> Availability:
    """Whether ``name`` can answer here; REFPROP also needs the worker's detection."""
    if not installed(name):
        return Availability(
            name, False, reason=f"{PACKAGES[name]} is not installed; {INSTALL_HINT}"
        )
    if name != "refprop":
        return Availability(name, True, version=package_version(name))
    detection = refprop_detection(wait=wait)
    if detection is None:
        return Availability(name, False, reason="REFPROP detection has not run yet")
    return Availability(
        name,
        bool(detection.get("available")),
        version=str(detection.get("version") or ""),
        reason=str(detection.get("reason") or ""),
    )


# ---------------------------------------------------------------- the index


def _home() -> Path:
    # **The one accessor** (`stokes.workspace_root`), so this and the other four callers cannot
    # drift: they each used to rebuild the default independently.
    return workspace_root.home()


def _index_key(name: str) -> str:
    parts = [str(INDEX_FORMAT), name, package_version(name)]
    if name == "refprop":
        detection = refprop_detection() or {}
        parts += [str(detection.get("path")), str(detection.get("version"))]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _index_file(name: str) -> Path:
    return _home() / "cache" / "materials" / f"index-{name}-{_index_key(name)}.json"


def _read_index(path: Path) -> list[dict[str, Any]] | None:  # Any: JSON records
    try:
        if not path.is_file() or path.stat().st_size > MAX_INDEX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        return None
    return data


def _write_index(path: Path, records: list[dict[str, Any]]) -> None:  # Any: JSON records
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".tmp")
        partial.write_text(json.dumps(records), encoding="utf-8")
        partial.replace(path)
    except OSError as exc:
        log.warning("materials index not cached", extra=fields(path=str(path), error=str(exc)))


def record_from_json(raw: dict[str, Any]) -> LibraryRecord:  # Any: a worker record
    def number(key: str) -> float | None:
        value = raw.get(key)
        return (
            float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
        )

    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    reference = raw.get("reference") if isinstance(raw.get("reference"), dict) else {}
    return LibraryRecord(
        provider=str(raw.get("provider", "")),
        fluid=str(raw.get("fluid", "")),
        name=str(raw.get("name", "")),
        kind=str(raw.get("kind", "fluid")),
        formula=str(raw.get("formula") or ""),
        cas=str(raw.get("cas") or ""),
        aliases=tuple(str(a) for a in raw.get("aliases") or ()),
        T_min=number("T_min"),
        T_max=number("T_max"),
        p_max=number("p_max"),
        T_critical=number("T_critical"),
        p_critical=number("p_critical"),
        molar_mass=number("molar_mass"),
        properties={str(k): bool(v) for k, v in (props or {}).items()},
        composition=str(raw["composition"]) if raw.get("composition") else None,
        basis=str(raw["basis"]) if raw.get("basis") else None,
        reference={str(k): str(v) for k, v in (reference or {}).items()},
        extra={k: v for k, v in raw.items() if k not in _RECORD_KEYS},
    )


def index(name: str) -> tuple[LibraryRecord, ...]:
    """Every fluid of a library provider: from memory, the disk cache, or the worker."""
    require_installed(name)
    with _known.lock:
        found = _known.indexes.get(name)
    if found is not None:
        return found
    if name == "refprop":
        _require_refprop()
    path = _index_file(name)
    raw = _read_index(path)
    if raw is None:
        reply = worker.request(
            {"type": "index", "provider": name}, refprop_path=refprop.settings_path()
        )
        records = reply.get("records")
        if not isinstance(records, list):
            raise ValidationError("the materials worker sent a malformed index")
        raw = [r for r in records if isinstance(r, dict)]
        _write_index(path, raw)
    built = tuple(record_from_json(r) for r in raw)
    with _known.lock:
        _known.indexes[name] = built
    return built


def _require_refprop() -> None:
    detection = refprop_detection()
    if not detection or not detection.get("available"):
        raise refprop.missing(
            refprop.Detection(
                False,
                folder=(detection or {}).get("path"),
                source=(detection or {}).get("source"),
                reason=str((detection or {}).get("reason") or ""),
            )
        )


# ---------------------------------------------------------------- the provider


class RemoteProvider:
    """A library provider whose calls run in the materials worker."""

    def __init__(self, name: str) -> None:
        if name not in LIBRARY:
            raise ValidationError(f"{name!r} is not a library provider")
        self.name = name

    def available(self) -> Availability:
        return availability(self.name)

    def search(self, query: str, kind: str = "any") -> list[LibraryRecord]:
        if kind not in ("fluid", "solid", "any"):
            raise ValidationError(f"kind must be fluid, solid or any; got {kind!r}")
        if kind == "solid":
            return []
        folded = query.strip().casefold()
        ranked = [(rank(r, folded), r.name.casefold(), r) for r in index(self.name)]
        hits = [(found, name, r) for found, name, r in ranked if found is not None]
        hits.sort(key=lambda item: (item[0], item[1]))
        return [r for _, _, r in hits]

    def _message(
        self, kind: str, fluid: str, composition: str, basis: str, phase: str
    ) -> dict[str, Any]:  # Any: JSON
        require_installed(self.name)
        if self.name == "refprop":
            _require_refprop()
        return {
            "type": kind,
            "provider": self.name,
            "fluid": fluid,
            "composition": composition,
            "basis": basis,
            "phase": phase,
        }

    def info(
        self,
        fluid: str,
        composition: str = "",
        basis: str = "",
        *,
        T_ref: float = 300.0,  # noqa: N803 - the contract's name
        p_ref: float = 101325.0,
        phase: str = "auto",
    ) -> LibraryInfo:
        message = self._message("info", fluid, composition, basis, phase)
        reply = worker.request(
            {**message, "T_ref": T_ref, "p_ref": p_ref}, refprop_path=refprop.settings_path()
        )
        found = reply.get("info")
        if not isinstance(found, dict) or not isinstance(found.get("record"), dict):
            raise ValidationError("the materials worker sent a malformed info reply")
        record = record_from_json(found["record"])
        detail = {
            key: found.get(key)
            for key in ("values_at_ref", "missing", "outputs", "nasa7")
            if key in found
        }
        detail["state"] = {"T_ref": T_ref, "p_ref": p_ref, "phase": phase}
        limits = found.get("limits") if isinstance(found.get("limits"), dict) else {}
        return LibraryInfo(
            record=record,
            limits=dict(limits or {}),
            references=dict(record.reference),
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
        return self.evaluate_chunked(fluid, composition, basis, phase, t, p)

    def evaluate_chunked(
        self,
        fluid: str,
        composition: str,
        basis: str,
        phase: str,
        t: Array,
        p: float,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> PropertyGrid:
        """The grid in chunks of about :data:`CHUNK_SECONDS`, reporting (done, total)."""
        message = self._message("evaluate", fluid, composition, basis, phase)
        temperatures = [float(v) for v in t]
        total = len(temperatures)
        columns: dict[str, list[float]] = {}
        missing: set[str] = set()
        phases: set[str] = set()
        size = max(1, min(FIRST_CHUNK, int(CHUNK_SECONDS / cost_per_state(self.name, fluid))))
        done = 0
        while done < total:
            chunk = temperatures[done : done + size]
            started = time.perf_counter()
            reply = worker.request(
                {**message, "T": chunk, "p": float(p)},
                refprop_path=refprop.settings_path(),
                cancel=cancel,
            )
            _merge(reply, chunk, columns, missing, phases)
            done += len(chunk)
            if on_progress is not None:
                on_progress(done, total)
            per_state = max((time.perf_counter() - started) / len(chunk), 1e-6)
            size = max(1, min(MAX_CHUNK, int(CHUNK_SECONDS / per_state)))
        if phase == "auto" and crossing(phases):
            raise OutOfRange(
                f"{fluid} changes phase between {min(temperatures):g} K and "
                f"{max(temperatures):g} K at {p:g} Pa; narrow the range or force the phase",
                details={"provider": self.name, "fluid": fluid, "phases": sorted(phases)},
            )
        values = {
            kind: np.asarray(column, dtype=np.float64)
            for kind, column in columns.items()
            if kind not in missing
        }
        return PropertyGrid(
            t=t, p=float(p), values=values, missing=sorted(missing), phases=sorted(phases)
        )


def _merge(
    reply: dict[str, Any],  # Any: JSON
    chunk: list[float],
    columns: dict[str, list[float]],
    missing: set[str],
    phases: set[str],
) -> None:
    values = reply.get("values")
    if not isinstance(values, dict):
        raise ValidationError("the materials worker sent malformed values")
    for kind, column in values.items():
        if not isinstance(column, list) or len(column) != len(chunk):
            raise ValidationError("the materials worker sent values of the wrong length")
        columns.setdefault(str(kind), []).extend(
            float(v) if isinstance(v, int | float) and not isinstance(v, bool) else float("nan")
            for v in column
        )
    missing.update(str(k) for k in reply.get("missing") or ())
    phases.update(str(k) for k in reply.get("phases") or ())
