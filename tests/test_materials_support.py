"""Shared helpers of the library-provider tests (ADR-0018 slice 2): skip markers that read the
package metadata without importing CoolProp or Cantera, a warm worker for the whole run, and a
private STOKES_HOME for the index cache. No tests here."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session

HAS_COOLPROP = importlib.util.find_spec("CoolProp") is not None
HAS_CANTERA = importlib.util.find_spec("cantera") is not None
needs_coolprop = pytest.mark.skipif(
    not HAS_COOLPROP, reason="CoolProp is not installed (the 'materials' extra)"
)
needs_cantera = pytest.mark.skipif(
    not HAS_CANTERA, reason="Cantera is not installed (the 'materials' extra)"
)


@pytest.fixture(scope="session")
def warm_worker() -> None:
    """The materials worker started once per run, with CoolProp and Cantera imported in it
    (CoolProp's first import costs seconds on a slow machine)."""
    from stokes.physics.materials import worker

    providers = tuple(n for n, ok in (("coolprop", HAS_COOLPROP), ("cantera", HAS_CANTERA)) if ok)
    if providers:
        worker.prewarm(providers)


@pytest.fixture
def private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A STOKES_HOME of the test's own, so the provider index cache never touches ~/.stokes."""
    home = tmp_path / "stokes_home"
    monkeypatch.setenv("STOKES_HOME", str(home))
    return home


def library_session(tmp_path: Path) -> Session:
    s = Session(journal_path=tmp_path / "journal.jsonl")
    s.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    return s


def events(session: Session, *names: str) -> list[tuple[str, dict[str, Any]]]:
    """Every event of the given names, in order, as (name, payload)."""
    seen: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        session.events.subscribe(name, lambda e, d: seen.append((e, dict(d))))
    return seen


def fit_methods(result: dict[str, Any]) -> dict[str, str]:
    return {prop: fit["method"] for prop, fit in result["fit"].items()}


def refprop_detection() -> dict[str, Any] | None:
    """The worker's REFPROP detection, or None without CoolProp."""
    if not HAS_COOLPROP:
        return None
    from stokes.physics.materials.providers import remote

    return remote.refprop_detection()
