"""The Rust licence gate (ADR-0035 decision 33): SPDX expressions against the one allow-list, and
which crates of a ``cargo metadata`` graph count as shipped. No cargo needed."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "audit_rust_licenses", ROOT / "tools" / "audit_rust_licenses.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _tool()
CONFIG = tomllib.loads((ROOT / "tools" / "license_allowlist.toml").read_text(encoding="utf-8"))
ALLOWED = CONFIG["allowed"]["patterns"]
DENIED = CONFIG["allowed"]["denied"]


@pytest.mark.parametrize(
    ("expression", "ok"),
    [
        ("MIT", True),
        ("MPL-2.0", True),
        ("MIT OR Apache-2.0", True),
        ("MIT/Apache-2.0", True),
        ("Apache-2.0 WITH LLVM-exception", True),
        ("(MIT OR Apache-2.0) AND Unicode-3.0", False),  # Unicode-3.0 is not on the list
        ("GPL-3.0-only", False),
        ("GPL-3.0 OR MIT", True),  # an OR passes when one side is allowed
        ("GPL-3.0 AND MIT", False),
        ("LGPL-2.1-or-later", False),  # LGPL needs an explicit decision
        ("LicenseRef-Stokes-Proprietary", True),
    ],
)
def test_spdx_expressions(expression: str, ok: bool) -> None:
    assert TOOL.evaluate(expression, ALLOWED, DENIED) is ok


def test_a_broken_expression_is_an_error() -> None:
    with pytest.raises(TOOL.SpdxError):
        TOOL.evaluate("(MIT OR", ALLOWED, DENIED)


def _package(name: str, licence: str | None, macro: bool = False) -> dict[str, Any]:
    kind = ["proc-macro"] if macro else ["lib"]
    return {
        "id": name,
        "name": name,
        "version": "1.0.0",
        "license": licence,
        "targets": [{"kind": kind}],
    }


def _dep(name: str, kind: str | None = None) -> dict[str, Any]:
    return {"pkg": name, "dep_kinds": [{"kind": kind}]}


def test_only_linked_crates_are_audited() -> None:
    metadata = {
        "workspace_members": ["app"],
        "packages": [
            _package("app", "LicenseRef-Stokes-Proprietary"),
            _package("lib", "MIT"),
            _package("derive", "MIT", macro=True),
            _package("syn-like", "(MIT OR Apache-2.0) AND Unicode-3.0"),
            _package("tester", "GPL-3.0"),
            _package("bare", None),
        ],
        "resolve": {
            "nodes": [
                {
                    "id": "app",
                    "deps": [_dep("lib"), _dep("derive"), _dep("tester", "dev"), _dep("bare")],
                },
                {"id": "derive", "deps": [_dep("syn-like")]},
                {"id": "lib", "deps": []},
            ]
        },
    }
    assert [p["name"] for p in TOOL.shipped(metadata)] == ["app", "bare", "lib"]
    problems = TOOL.audit(metadata, CONFIG)
    assert len(problems) == 1 and problems[0].startswith("bare 1.0.0: no licence")
    assert TOOL.audit(metadata, {**CONFIG, "verified_rust": {"bare": "checked by hand"}}) == []
