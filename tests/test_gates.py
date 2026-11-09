"""The repository gates must pass as part of the test run."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "script",
    [
        "tools/check_forbidden_imports.py",
        "tools/check_ip.py",
        # **The encoding gate runs with the others** (claude-win2's 1230 review): a file written
        # with the wrong encoding is a shipped defect that no behavioural test can see, which is
        # exactly what a gate is for.
        "tools/check_encoding.py",
    ],
)
def test_gate(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def load_ip_gate() -> ModuleType:
    """``tools/check_ip.py`` as a module: tools is a folder of scripts, not a package."""
    spec = importlib.util.spec_from_file_location("check_ip_gate", ROOT / "tools" / "check_ip.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ip_gate_fails_a_machine_path() -> None:
    """A user's home folder names the person and the machine, in every spelling.

    The samples are assembled here: this file is scanned by the gate it tests.
    """
    gate = load_ip_gate()
    back, fwd = "\\", "/"
    paths = [
        f"C:{back}Users{back}alice{back}code",  # as written in a comment or a raw string
        f"c:{back}{back}users{back}{back}alice",  # inside an ordinary Python string
        f"D:{fwd}Users{fwd}alice{fwd}code",
        f"{fwd}home{fwd}alice{fwd}code",
        f"{fwd}Users{fwd}alice{fwd}code",
    ]
    for path in paths:
        problems = gate.line_problems(f'ROOT = "{path}"', [])
        assert len(problems) == 1 and "alice" not in problems[0], path  # the path is not echoed
    assert gate.line_problems(f"see {paths[0]} and {paths[3]}", []) != []


def test_ip_gate_passes_placeholders_and_relative_paths() -> None:
    gate = load_ip_gate()
    back, fwd = "\\", "/"
    quiet = [
        f"the owner uses C:{back}Users{back}<you>{back}code{back}app",
        f"C:{back}Users{back}%USERNAME%{back}code",
        f"C:{back}Users{back}",
        f"{fwd}home{fwd}{{user}}{fwd}code",
        f"{fwd}home{fwd}$USER{fwd}code",
        f"web{fwd}src{fwd}home{fwd}page.tsx",
        f"https://example.org{fwd}home{fwd}index.html",
        "~/code and $HOME/code",
    ]
    for text in quiet:
        assert gate.line_problems(text, []) == [], text


def test_ip_gate_still_matches_blocklist_terms_without_case() -> None:
    gate = load_ip_gate()
    assert gate.line_problems("An Acme Widget", ["acme"]) == ["contains 'acme'"]
    assert gate.line_problems("nothing here", ["acme"]) == []
