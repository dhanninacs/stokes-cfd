"""``engine.info`` says whether an OpenFOAM install can be found, so the top bar does not read
"No engine" over a window that runs solves (it was a stub that always answered "none")."""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session


def test_an_install_the_agent_can_find_is_an_attached_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("stokes_agent.engine_env.bashrc_candidates", lambda: [Path("/opt/of14")])
    info = Session().execute("engine.info")
    assert info == {"attached": True, "name": "OpenFOAM 14", "version": None}


def test_no_install_is_no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stokes_agent.engine_env.bashrc_candidates", lambda: [])
    assert Session().execute("engine.info") == {"attached": False, "name": None, "version": None}
