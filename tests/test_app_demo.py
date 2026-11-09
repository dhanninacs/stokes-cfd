"""The demo project: where its geometry lives, and what happens where there is none.

Claude's defect 5 had three parts, and each is a distinct failure of the same feature:

1. the generated STEP was written into the session's **scratch** folder, which `GeometryStore.close`
   deletes — so a saved demo named a source file that no longer existed, and a re-import, a
   `check.brep` or a units change would fail on a project the user had only opened and saved;
2. the demo **needed OCP**, so a build without the optional `cad` extra could not start the app at
   all: `stokes.server` and the window import this module on the way up;
3. the import's `wait` result was **ignored**, so a timeout produced a demo with no geometry, no
tags
   and no conditions and said nothing — the tree just looked empty, which reads as a tree bug.

These tests are deliberately about those three things rather than about the demo's contents; the
content is covered by the tests that build it against real geometry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.app.demo import build_demo


def test_the_demo_step_is_written_outside_the_session_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source a saved demo names must outlive the session that made it.

    `GeometryStore.close` removes the scratch folder, so a STEP written there is gone as soon as the
    project is closed — and the part nodes go on naming it. The check is that the file the project
    points at is NOT under the store's scratch folder, and that it is still there after the store is
    closed.
    """
    from stokes.geometry.store import GeometryStore

    # **`STOKES_HOME` is redirected**, so the test neither depends on nor pollutes the real user's
    # demo folder (Claude's point about the demo tests writing into the real cache).
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    build_demo(session)
    store = GeometryStore.of(session)
    scratch = store.shape_dir()
    parts = [n for n in session.require_project().nodes() if n.type == "part"]
    assert parts, "the demo must produce real part nodes"
    sources = {str(n.params.get("source")) for n in parts}
    assert len(sources) == 1, "the demo's parts all come from one source file"
    source = Path(next(iter(sources)))
    assert source.is_file(), f"the demo's source does not exist: {source}"
    assert scratch not in source.parents, (
        f"the demo's source is in the session scratch folder, which is deleted on close: {source}"
    )
    # And it survives the store closing, which is the whole point.
    store.close()
    assert source.is_file(), "the demo's source must survive the store closing"


def test_the_demo_builds_without_ocp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A build with no `cad` extra gets a project, not an exception.

    The window imports this module while starting, so raising here means the app does not open. The
    fallback is the geometry-free demo: the parameters and the study are real, and there are
    deliberately **no** tags, regions or boundaries, because every one of those names faces of
    bodies
    that were never imported — empty groups pretending to be a flow case are the old demo that
    "reads
    as a defect".
    """
    import stokes.app.demo_geometry as geometry

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(geometry, "demo_available", lambda: False)
    session = Session()
    build_demo(session)
    project = session.require_project()
    types = [n.type for n in project.nodes()]
    assert types.count("parameter") == 6
    assert types.count("study") == 1
    assert "tag" not in types, "a tag with no faces behind it is the defect this fallback avoids"
    assert "region" not in types
    assert "boundary" not in types
    # The parameters are defined exactly once: the fallback is reached after `_define_parameters`
    # has already run, and defining them twice raises `already has a child named 'U_in'`.
    names = [n.name for n in project.nodes() if n.type == "parameter"]
    assert sorted(names) == ["D", "Q", "T_in", "U_in", "dp_dyn", "rho"]


def test_the_demo_step_is_reused_rather_than_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second build must not touch the file, so a saved project's source stamp stays put.

    Claude's point about the previous version of this test: it asserted `>=` on the mtime under a
    name saying "reused rather than regenerated" — the two contradict each other, and `>=` passes
    whether the file was rewritten or left alone, so the test could not fail. The real property is
    that the
    file is **identical and untouched**, which is what a saved project depends on: a changed mtime
    makes its source look modified and its tags go stale on the next open.

    **`STOKES_HOME` is redirected to `tmp_path`** so the test cannot depend on, or pollute, the real
    user's demo folder — the other thing Claude flagged.
    """
    from stokes.app.demo_geometry import build_demo_step, demo_source_name

    monkeypatch.setenv("STOKES_HOME", str(tmp_path))
    first = build_demo_step()
    before = first.stat()
    assert first.name == demo_source_name(), "the name must carry the geometry's hash"
    assert str(tmp_path) in str(first), "the test must use its own STOKES_HOME, not the user's"

    again = build_demo_step()
    after = again.stat()
    # The same path, the same size, and — the assertion the old test lacked — **the same mtime**,
    # because a second build finds the file already correct and writes nothing.
    assert again == first
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), (
        "the demo STEP was rewritten: a saved project's source would look modified and its tags "
        "would go stale"
    )


def test_a_demo_step_the_disk_refuses_falls_back_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write the disk refuses must give the geometry-free demo, not an exception.

    Claude's defect 3, confirmed by him on Windows: `Path.replace` over a file another process holds
    open raises `PermissionError [WinError 5]`. Letting it escape meant the **window did not open**,
    because `stokes.server` and the app import this module on the way up. The disk is allowed to say
    no — the demo degrades and logs, exactly as it does when OCP is missing.

    `PermissionError` is a subclass of `OSError`, so this exercises the same handler the Windows
    failure reaches without needing Windows to produce it.
    """
    import stokes.app.demo as demo
    import stokes.app.demo_geometry as geometry

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))

    def refuse(*_args: object, **_kwargs: object) -> Path:
        raise PermissionError(5, "Access is denied", "demo.step")

    monkeypatch.setattr(geometry, "build_demo_step", refuse)
    session = Session()
    demo.build_demo(session)  # must not raise
    project = session.require_project()
    types = [n.type for n in project.nodes()]
    assert types.count("parameter") == 6, "the fallback still defines the parameters"
    assert "tag" not in types and "region" not in types, (
        "a tag with no faces behind it is the defect the fallback avoids"
    )


def test_the_demo_source_name_follows_the_geometry(tmp_path: Path) -> None:
    """The name is a hash of the geometry, not a fixed string.

    Claude's defect 3: the name was a fixed `demo_pipe.step` while the docstring claimed it carried
    a version, so every launch rewrote one path. Hashed on the geometry's own recipe, a geometry
    change must produce a different name — otherwise an old file would be reused for new geometry,
    which is worse than rewriting.
    """

    from stokes.app import demo_geometry

    first = demo_geometry.demo_source_name()
    # Change a dimension and the name must move. The module is reloaded so the constant change takes
    # effect; the point is that the name DEPENDS on it, not how the change is applied.
    original = demo_geometry.BORE
    try:
        demo_geometry.BORE = original + 1.0
        changed = demo_geometry.demo_source_name()
    finally:
        demo_geometry.BORE = original
    assert changed != first, "a geometry change must change the source name"
    assert demo_geometry.demo_source_name() == first, "and restoring it must restore the name"
    # **No `importlib.reload`.** It was here as a belt-and-braces restore, but the `finally` above
    # already puts `BORE` back — so the reload was redundant, and worse than redundant: reloading a
    # module mid-suite rebinds it for every later test while leaving any other importers holding the
    # old object, which is a way to make one test's state another test's mystery. (Claude's minor
    # item.) The assertion above is the real check: the name came back without it.
