"""REFPROP through CoolProp's backend (ADR-0018 decision 6; contract "REFPROP detection"): the
search order (settings path, COOLPROP_REFPROP_ROOT, RPPREFIX, PATH) and the missing paths are
tested on simulated installations, so they run everywhere; REFPROP against HEOS and REFPROP's
transport for a fluid CoolProp lacks run only where REFPROP is installed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.physics.materials.errors import RefpropMissing
from stokes.physics.materials.providers import refprop
from stokes.physics.materials.providers.refprop import locate
from tests.test_materials_support import (  # noqa: F401 - fixtures
    HAS_COOLPROP,
    library_session,
    private_home,
    refprop_detection,
    warm_worker,
)

LIBRARY = "REFPRP64.DLL"


def files(*folders: str, name: str = LIBRARY) -> set[str]:
    return {str(Path(folder) / name) for folder in folders}


class FakeCoolProp:
    """CoolProp's three calls that :func:`refprop.load` makes, failing like CoolProp does when
    REFPROP cannot be opened."""

    ALTERNATIVE_REFPROP_PATH = "ALTERNATIVE_REFPROP_PATH"

    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []  # Any: the call's argument

    def set_config_string(self, key: str, value: str) -> None:
        self.calls.append(("config", (key, value)))

    def AbstractState(self, backend: str, fluid: str) -> object:
        self.calls.append(("state", (backend, fluid)))
        if self.fail:
            raise ValueError('Initialize failed for backend: "REFPROP"\nmore text')
        return object()

    def get_global_param_string(self, key: str) -> str:
        return "10.0"


def test_the_search_order() -> None:
    present = files("C:/settings", "C:/root", "C:/rp", "C:/onpath")
    env = {"COOLPROP_REFPROP_ROOT": "C:/root", "RPprefix": "C:/rp", "PATH": "C:/x;C:/onpath"}

    def where(settings: str | None, environ: dict[str, str]) -> tuple[str | None, str | None]:
        found = locate(settings, environ, is_file=present.__contains__, platform="win32")
        return found.source, found.folder

    assert where("C:/settings", env) == ("settings", "C:/settings")
    assert where(None, env) == ("COOLPROP_REFPROP_ROOT", "C:/root")
    assert where(None, {"RPprefix": "C:/rp", "PATH": "C:/onpath"}) == ("RPPREFIX", "C:/rp")
    assert where(None, {"RPPREFIX": "C:/rp"}) == ("RPPREFIX", "C:/rp")
    assert where(None, {"PATH": "C:/x;C:/onpath"}) == ("PATH", "C:/onpath")
    assert where("C:/empty", env) == ("COOLPROP_REFPROP_ROOT", "C:/root")  # no library there
    linux = locate(
        None,
        {"PATH": "/usr/bin:/opt/refprop"},
        is_file=files("/opt/refprop", name="librefprop.so").__contains__,
        platform="linux",
    )
    assert (linux.source, linux.folder) == ("PATH", "/opt/refprop")


def test_a_missing_refprop_is_a_typed_error() -> None:
    cp = FakeCoolProp(fail=False)
    environ = {"PATH": "C:/x"}
    detection = refprop.load("C:/wrong", environ, cp, is_file=lambda path: False, platform="win32")
    assert detection.available is False and cp.calls == []  # CoolProp's loader never asked
    assert "C:/wrong" in detection.reason and "material.providers" in detection.reason
    error = refprop.missing(detection)
    assert isinstance(error, RefpropMissing) and error.code == "materials.refprop_missing"


def test_a_refprop_that_will_not_load_is_reported() -> None:
    cp = FakeCoolProp(fail=True)
    environ: dict[str, str] = {}
    detection = refprop.load(
        "C:/broken", environ, cp, is_file=files("C:/broken").__contains__, platform="win32"
    )
    assert detection.available is False and detection.source == "settings"
    assert "could not be loaded" in detection.reason and "Initialize failed" in detection.reason
    assert environ["COOLPROP_REFPROP_ROOT"] == "C:/broken"  # CoolProp is pointed at the choice
    assert cp.calls[0] == ("config", ("ALTERNATIVE_REFPROP_PATH", "C:/broken"))
    working = refprop.load(
        "C:/good",
        {},
        FakeCoolProp(fail=False),
        is_file=files("C:/good").__contains__,
        platform="win32",
    )
    assert working.available and working.version == "10.0" and working.folder == "C:/good"


@pytest.mark.skipif(not HAS_COOLPROP, reason="CoolProp is not installed")
def test_an_undetected_refprop_refuses_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from stokes.physics.materials.providers import get_provider, remote

    report = {"refprop": {"available": False, "reason": "REFPROP was not found (simulated)"}}
    monkeypatch.setattr(remote._known, "report", report)
    with pytest.raises(RefpropMissing) as refused:
        get_provider("refprop")
    assert "simulated" in refused.value.message
    from stokes.physics.materials.providers import available_providers

    assert "refprop" not in available_providers()


@pytest.fixture
def refprop_found(warm_worker: None) -> dict[str, Any]:  # noqa: F811 - the fixture above
    detection = refprop_detection()
    if not detection or not detection.get("available"):
        pytest.skip("REFPROP is not installed here")
    return detection


@pytest.mark.usefixtures("private_home")
def test_refprop_water_matches_heos_to_1e_9(refprop_found: dict[str, Any]) -> None:
    from stokes.physics.materials.providers import get_provider

    t = np.array([300.0])
    by_refprop = get_provider("refprop").evaluate("WATER", "", "", "auto", t, 101325.0)
    by_heos = get_provider("coolprop").evaluate("Water", "", "", "auto", t, 101325.0)
    rho = float(by_refprop.values["density"][0])
    assert rho == pytest.approx(float(by_heos.values["density"][0]), rel=1e-9)
    assert refprop_found["version"] and Path(str(refprop_found["path"])).is_dir()


@pytest.mark.usefixtures("private_home")
def test_refprop_has_transport_coolprop_lacks(
    refprop_found: dict[str, Any], tmp_path: Path
) -> None:
    session = library_session(tmp_path)
    providers = {p["name"]: p for p in session.execute("material.providers")}
    assert providers["refprop"]["available"] is True
    assert providers["refprop"]["path"] == refprop_found["path"]
    added = session.execute("material.add_from_library", provider="refprop", fluid="NEON")
    assert added["missing"] == []
    assert set(added["fit"]) == {"density", "viscosity", "specific_heat", "thermal_conductivity"}
    assert session.require_project().get(f"{added['path']}/viscosity").meta["fit"]["provider"] == (
        "refprop"
    )
