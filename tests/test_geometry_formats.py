"""Import formats (T-016, ADR-0021 decisions 1.1, 1.2 and 1.4): the format table and its
suffixes, the header probes, ``check_source_file``, the IGES and BREP readers, ``read_source``,
and ``geometry.import`` end to end (units, ``family``, one part per body, the old name
journaled under the new one, ``geometry.formats``).

The end-to-end imports run through the geometry worker; the workers are started ahead, because
each spends seconds importing OCP.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from stokes.api import Journal, Session, replay
from stokes.errors import ValidationError
from stokes.geometry import formats, readers, step
from stokes.geometry.brepfile import read_brep
from stokes.geometry.errors import GeometryFileError, GeometryUnsupported, MeshOnly
from stokes.geometry.iges import read_iges
from stokes.geometry.store import GeometryStore
from stokes.geometry.worker import prewarm, shutdown_pool
from tests import geometry_fixtures as fx

BOX = fx.box(10.0, 20.0, 30.0)
"""The shared 10 x 20 x 30 millimetre box (OCCT model units: millimetres)."""


@pytest.fixture(scope="module", autouse=True)
def workers() -> Iterator[None]:
    prewarm(3)  # the end-to-end imports below, one warm worker each
    yield
    shutdown_pool()


@pytest.fixture(scope="module")
def cad(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("formats")
    fake = root / "fake.igs"
    fake.write_text("S is not in the right column\n", encoding="utf-8")
    fake_brep = root / "fake.brep"
    fake_brep.write_text("not a shape\n", encoding="utf-8")
    stl = root / "mesh.stl"
    stl.write_bytes(b"solid nothing\nendsolid\n")
    return {
        "box_step": fx.write_plain(BOX, root / "box.step"),
        "box_iges": fx.write_iges([("box", BOX, None)], root / "box.igs"),
        "two_iges": fx.write_iges(
            [
                ("box_a", BOX, None),
                ("box_b", fx.box(5.0, 5.0, 5.0, at=(40.0, 0.0, 0.0)), None),
            ],
            root / "two.igs",
        ),
        "loose_iges": fx.write_iges_shapes(fx.faces_of(BOX), root / "loose_box.igs"),
        "loose_two_iges": fx.write_iges_shapes(
            fx.faces_of(BOX) + fx.faces_of(fx.box(5.0, 5.0, 5.0, at=(40.0, 0.0, 0.0))),
            root / "loose_boxes.igs",
        ),
        "solid_iges": fx.write_iges_shapes([BOX], root / "solid_box.igs", brep_mode=1),
        "box_brep": fx.write_brep(BOX, root / "box.brep"),
        "box_brep_binary": fx.write_brep_binary(BOX, root / "box_binary.brep"),
        "two_brep": fx.write_brep(
            fx.compound([BOX, fx.box(5.0, 5.0, 5.0, at=(40.0, 0.0, 0.0))]), root / "two.brep"
        ),
        "fake_iges": fake,
        "fake_brep": fake_brep,
        "stl": stl,
    }


# ------------------------------------------------------------------ the table and the checks


def test_the_format_table() -> None:
    assert [f.id for f in formats.FORMATS] == ["step", "iges", "brep", "stl", "obj", "3mf"]
    assert [f.family for f in formats.FORMATS] == ["brep", "brep", "brep", "mesh", "mesh", "mesh"]
    assert [f.units for f in formats.FORMATS] == [
        "file",
        "file",
        "param",
        "param",
        "param",
        "file",
    ]
    step, iges, brep = formats.FORMATS[:3]
    assert step.suffixes == (".step", ".stp") and iges.suffixes == (".iges", ".igs")
    assert brep.suffixes == (".brep", ".brp")
    assert formats.format_for("P.BRPart.STEP").id == "step"  # the suffix decides, any case
    assert formats.family_for("mesh.3mf") == "mesh"


def test_the_filter_lists_every_format_and_then_all() -> None:
    text = formats.filter_string()
    assert text.startswith("Geometry files (") and text.endswith("All files (*)")
    for fmt in formats.FORMATS:
        for suffix in fmt.suffixes:
            assert f"*{suffix}" in text  # globs, as the native dialog's name filter reads them
        assert f"{fmt.label} (" in text
    assert "STEP (*.step *.stp);;IGES (*.iges *.igs)" in text


def test_format_for_refuses_an_unknown_suffix(tmp_path: Path) -> None:
    with pytest.raises(GeometryUnsupported) as info:
        formats.format_for(tmp_path / "model.xyz")
    assert info.value.code == "geometry.unsupported"
    assert "STEP" in info.value.message and "3MF" in info.value.message


def test_the_header_probes(cad: dict[str, Path], tmp_path: Path) -> None:
    step_fmt, iges_fmt, brep_fmt = formats.FORMATS[:3]
    for path, fmt in (
        (cad["box_step"], step_fmt),
        (cad["box_iges"], iges_fmt),
        (cad["box_brep"], brep_fmt),
        (cad["box_brep_binary"], brep_fmt),
    ):
        formats.probe(path, fmt)  # no exception: the header fits the format
    with pytest.raises(GeometryFileError):
        formats.probe(cad["fake_iges"], iges_fmt)
    with pytest.raises(GeometryFileError):
        formats.probe(cad["fake_brep"], brep_fmt)
    not_step = tmp_path / "notes.step"
    not_step.write_text("this file is not a step file\n", encoding="utf-8")
    with pytest.raises(GeometryFileError):
        formats.probe(not_step, step_fmt)


def test_check_source_file(cad: dict[str, Path], tmp_path: Path) -> None:
    assert formats.format_for(Path(cad["box_iges"])).id == "iges"
    from stokes.geometry.files import check_source_file

    for name in ("box_step", "box_iges", "box_brep", "box_brep_binary", "stl"):
        assert check_source_file(cad[name]) == Path(cad[name]).resolve()
    with pytest.raises(GeometryFileError) as info:
        check_source_file(cad["fake_iges"])
    assert info.value.code == "geometry.unreadable"
    unknown = tmp_path / "model.xyz"
    unknown.write_text("no format claims this suffix\n", encoding="utf-8")
    with pytest.raises(GeometryUnsupported):
        check_source_file(unknown)
    with pytest.raises(GeometryFileError):
        check_source_file(tmp_path)


def test_check_step_file_stays_the_step_only_wrapper(cad: dict[str, Path]) -> None:
    from stokes.geometry.files import check_step_file

    assert check_step_file(cad["box_step"]) == Path(cad["box_step"]).resolve()
    with pytest.raises(GeometryFileError) as info:
        check_step_file(cad["box_iges"])  # a real IGES file is still not a STEP file
    assert "not a STEP file" in info.value.message


# ------------------------------------------------------------------ the readers, in process


def test_read_iges_gives_named_shell_bodies_in_millimetres(cad: dict[str, Path]) -> None:
    model = read_iges(cad["box_iges"])
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [("box", "shell", 6)]
    assert model.units == "mm" and model.unit_scale == pytest.approx(1e-3)
    assert model.warnings == () and model.solids[0].color is None


def test_read_iges_two_boxes_are_two_bodies(cad: dict[str, Path]) -> None:
    model = read_iges(cad["two_iges"])
    assert [(s.name, s.faces) for s in model.solids] == [("box_a", 6), ("box_b", 6)]


def test_read_iges_gathers_the_loose_faces_of_a_box_into_one_part(cad: dict[str, Path]) -> None:
    """Six faces written as surfaces came back as six parts; they are one 6-face body now."""
    model = read_iges(cad["loose_iges"])
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [("loose_box", "faces", 6)]
    assert model.solids[0].color is None and model.units == "mm" and model.warnings == ()
    shape = model.solids[0].shape
    assert shape.ShapeType().name == "TopAbs_COMPOUND" and len(fx.faces_of(shape)) == 6
    # Not sewn: sewing is a repair the user chooses, and it would make a shell.
    assert not fx.subshapes(shape, "SHELL") and not fx.subshapes(shape, "SOLID")


def test_read_iges_gathers_the_loose_faces_of_two_boxes_into_one_part(
    cad: dict[str, Path],
) -> None:
    model = read_iges(cad["loose_two_iges"])
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [("loose_boxes", "faces", 12)]
    assert len(fx.faces_of(model.solids[0].shape)) == 12


def test_read_iges_written_with_brep_mode_one_still_gives_a_solid(cad: dict[str, Path]) -> None:
    model = read_iges(cad["solid_iges"])
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [("solid_1", "solid", 6)]


def test_read_iges_keeps_a_compound_a_shell_beside_the_gathered_faces(tmp_path: Path) -> None:
    """Only the lone faces gather, into one body where the first of them was in the file."""
    lone = fx.faces_of(fx.box(5.0, 5.0, 5.0, at=(40.0, 0.0, 0.0)))
    model = read_iges(fx.write_iges_shapes([BOX, *lone], tmp_path / "mixed.igs"))
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [
        ("solid_1", "shell", 6),
        ("mixed", "faces", 6),
    ]


def test_the_gathered_faces_take_a_name_no_other_body_has(tmp_path: Path) -> None:
    lone = fx.faces_of(fx.box(5.0, 5.0, 5.0, at=(40.0, 0.0, 0.0)))
    model = read_iges(fx.write_iges_shapes([BOX, *lone], tmp_path / "solid_1.igs"))
    assert [s.name for s in model.solids] == ["solid_1", "solid_1_2"]  # the compound got solid_1


def test_read_iges_with_a_solid_leaves_its_loose_faces_as_they_are(tmp_path: Path) -> None:
    """Gathering is for a file without solids; one with a solid keeps what the walk found."""
    lone = fx.faces_of(fx.box(5.0, 5.0, 5.0, at=(40.0, 0.0, 0.0)))
    model = read_iges(fx.write_iges_shapes([BOX, *lone], tmp_path / "both.igs", brep_mode=1))
    assert [s.kind for s in model.solids] == ["solid"] + ["shell"] * 6
    assert [s.faces for s in model.solids] == [6] + [1] * 6


def test_read_brep_gives_named_solids_and_the_given_units(cad: dict[str, Path]) -> None:
    model = read_brep(cad["box_brep"], "mm")
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [("box", "solid", 6)]
    assert model.units == "mm" and model.unit_scale == pytest.approx(1e-3)
    inches = read_brep(cad["box_brep"], "in")
    assert inches.unit_scale == pytest.approx(0.0254)  # 25.4 mm per inch
    default = read_brep(cad["box_brep"])
    assert default.units == "mm"  # the argument defaults to millimetres


def test_read_brep_two_boxes_are_two_bodies_named_from_the_file(cad: dict[str, Path]) -> None:
    model = read_brep(cad["two_brep"], "mm")
    assert [s.name for s in model.solids] == ["two", "two_2"]
    assert [s.faces for s in model.solids] == [6, 6]


def test_read_brep_reads_the_binary_form_as_it_does_the_text_one(cad: dict[str, Path]) -> None:
    """``BinTools`` files open with ``Open CASCADE Topology``; the text ones with DBRep."""
    binary, text = cad["box_brep_binary"], cad["box_brep"]
    assert b"Open CASCADE Topology" in binary.read_bytes()[:64]
    assert b"DBRep_DrawableShape" not in binary.read_bytes()[:64]
    assert b"DBRep_DrawableShape" in text.read_bytes()[:64]
    model = read_brep(binary, "in")
    assert [(s.name, s.kind, s.faces) for s in model.solids] == [("box_binary", "solid", 6)]
    assert model.units == "in" and model.unit_scale == pytest.approx(0.0254)
    twin = read_brep(text, "in")
    assert step.bbox_diagonal(model.solids[0].shape) == pytest.approx(
        step.bbox_diagonal(twin.solids[0].shape)
    )


def test_read_brep_refuses_a_non_shape(tmp_path: Path) -> None:
    bad = tmp_path / "bad.brep"
    bad.write_text("DBRep_DrawableShape\nthis is not a shape\n", encoding="utf-8")
    with pytest.raises(GeometryFileError):
        read_brep(bad, "mm")
    with pytest.raises(ValidationError):
        read_brep(cad_box(tmp_path), "boing")


def test_read_source_dispatches_and_refuses(cad: dict[str, Path], tmp_path: Path) -> None:
    assert readers.read_source(cad["box_step"]).units == "mm"
    assert readers.read_source(cad["box_iges"]).units == "mm"
    assert readers.read_source(cad["box_brep"]).units == "mm"
    assert readers.read_source(cad["box_brep"], units="cm").unit_scale == pytest.approx(1e-2)
    for path in (cad["box_step"], cad["box_iges"]):
        with pytest.raises(ValidationError):
            readers.read_source(path, units="mm")  # these files declare their own units
    with pytest.raises(MeshOnly) as info:
        readers.read_source(cad["stl"])  # T-017: the mesh formats have their own reader
    assert info.value.code == "geometry.mesh_only"
    with pytest.raises(GeometryFileError):
        readers.read_mesh(cad["stl"])  # an ASCII solid block with no triangles
    with pytest.raises(ValidationError):
        readers.read_source(cad_box(tmp_path), units="boing")


def cad_box(tmp_path: Path) -> Path:
    return fx.write_brep(BOX, tmp_path / "box.brep")


# ------------------------------------------------------------------ geometry.import end to end


def test_iges_import_gives_a_brep_part_with_the_file_units(
    cad: dict[str, Path], session: Session
) -> None:
    result = session.execute(
        "geometry.import", path=str(cad["box_iges"]), name="igs_box", wait=True
    )
    assert result["import"] == {"status": "ready"}
    params = result["params"]
    assert params["family"] == "brep" and params["units"] == "mm"
    assert params["faces"] == 6 and params["triangles"] == 12
    assert params["bbox"] == pytest.approx([0, 0, 0, 0.01, 0.02, 0.03], abs=1e-7)


def test_brep_imported_in_inches_is_25_4_times_the_millimetre_one(
    cad: dict[str, Path], session: Session
) -> None:
    millimetre = session.execute("geometry.import", path=str(cad["box_brep"]), name="mm", wait=True)
    inches = session.execute(
        "geometry.import", path=str(cad["box_brep"]), name="in", units="in", wait=True
    )
    assert millimetre["params"]["units"] == "mm" and inches["params"]["units"] == "in"
    assert millimetre["params"]["family"] == "brep" and inches["params"]["family"] == "brep"
    for low, high in zip(
        millimetre["params"]["bbox"][3:], inches["params"]["bbox"][3:], strict=True
    ):
        assert high == pytest.approx(25.4 * low, rel=1e-5)


def test_a_brep_in_inches_exports_at_its_own_size(cad: dict[str, Path], session: Session) -> None:
    """The check and export jobs read a BREP in the units it was imported in: they once read
    every BREP as millimetres, and an inch part exported 25.4 times too small."""
    inches = session.execute(
        "geometry.import", path=str(cad["box_brep"]), name="inch", units="in", wait=True
    )
    session.execute("geometry.check", part=inches["id"])  # reads the file again, no refusal
    out = session.execute("geometry.export_step", parts=[inches["id"]], path="inch.step")
    again = session.execute("geometry.import", path=out["file"], name="again", wait=True)
    assert again["params"]["bbox"][3:] == pytest.approx(inches["params"]["bbox"][3:], rel=1e-5)
    millimetre = session.execute("geometry.import", path=str(cad["box_brep"]), name="mm", wait=True)
    # Each part is loaded through the shape provider with its own units, so the same BREP file
    # imported twice, in two units, goes into one export at both sizes (T-030).
    both = session.execute(
        "geometry.export_step", parts=[inches["id"], millimetre["id"]], path="both.step"
    )
    assert both["solids"] == 2 and both["names"] == ["inch", "mm"]
    # The sizes are the point of this case: the same file read as inches must come out 25.4 times
    # the one read as millimetres, measured on the written file rather than assumed from the units.
    from tests.test_geometry_export import extents_m

    boxes = extents_m(Path(both["file"]))
    assert len(boxes) == 2
    inch_box, mm_box = boxes[0], boxes[1]
    for inch_extent, mm_extent in zip(inch_box, mm_box, strict=True):
        assert inch_extent == pytest.approx(25.4 * mm_extent, rel=1e-5)
    # And the millimetre solid is the size the import reported for it (the bbox is in metres).
    assert mm_box == pytest.approx(millimetre["params"]["bbox"][3:], rel=1e-5)
    assert inch_box == pytest.approx(inches["params"]["bbox"][3:], rel=1e-5)


def test_an_iges_file_of_two_boxes_gives_two_parts(cad: dict[str, Path], session: Session) -> None:
    result = session.execute("geometry.import", path=str(cad["two_iges"]), wait=True)
    assert result["type"] == "group" and result["import"] == {"status": "ready"}
    info = session.execute("geometry.info", node=result["id"])
    assert [p["name"] for p in info["parts"]] == ["box_a", "box_b"]
    assert all(p["family"] == "brep" and p["units"] == "mm" for p in info["parts"])
    assert [p["faces"] for p in info["parts"]] == [6, 6]


def test_an_iges_file_of_loose_faces_imports_as_one_part(
    cad: dict[str, Path], session: Session
) -> None:
    result = session.execute("geometry.import", path=str(cad["loose_iges"]), wait=True)
    assert result["type"] == "part" and result["import"] == {"status": "ready"}
    assert result["name"] == "loose_box"
    params = result["params"]
    assert params["faces"] == 6 and params["triangles"] == 12 and params["family"] == "brep"
    assert params["bbox"] == pytest.approx([0, 0, 0, 0.01, 0.02, 0.03], abs=1e-7)


def test_units_given_for_a_step_file_is_refused_without_a_node(
    cad: dict[str, Path], session: Session
) -> None:
    count = len(session.execute("geometry.list"))
    with pytest.raises(ValidationError) as info:
        session.execute("geometry.import", path=str(cad["box_step"]), units="mm")
    assert "declare their own units" in info.value.message
    with pytest.raises(ValidationError):
        session.execute("geometry.import", path=str(cad["box_brep"]), units="boing")
    assert len(session.execute("geometry.list")) == count


def test_a_mesh_file_imports_as_a_mesh_only_part_and_the_reader_owns_its_errors(
    cad: dict[str, Path], session: Session
) -> None:
    """T-017: the mesh formats are no longer refused as 'a later version', and the mesh reader's
    own refusals (here, an ASCII solid block with no triangles) reach the part."""
    result = session.execute("geometry.import", path=str(cad["stl"]), wait=True)
    assert result["params"]["family"] == "mesh"
    assert result["import"]["status"] == "failed"
    error = GeometryStore.of(session).error(result["id"])
    assert error is not None and error.code == "geometry.unreadable"


def test_the_old_name_still_works_and_journals_the_new_one(
    cad: dict[str, Path], tmp_path: Path
) -> None:
    session = Session(journal_path=tmp_path / "journal.jsonl")
    session.execute("project.new", name="old", path=str(tmp_path / "old"))
    part = session.execute("geometry.import_step", path=str(cad["box_brep"]), name="box", wait=True)
    assert part["import"] == {"status": "ready"}
    records = Journal.read(tmp_path / "journal.jsonl")
    assert [r["command"] for r in records] == ["project.new", "geometry.import"]
    fresh = Session()
    fresh.execute("project.new", name="again", path=str(tmp_path / "again"))
    replay(fresh, records[1:])
    store = GeometryStore.of(fresh)
    assert store.wait(part["id"], timeout=180)
    again = fresh.require_project().get(part["id"])
    assert again.id == part["id"] and again.params["family"] == "brep"
    store.close()
    session = None


def test_geometry_formats_lists_the_formats_and_the_filter(session: Session) -> None:
    result = session.execute("geometry.formats")
    assert [f["id"] for f in result["formats"]] == [f.id for f in formats.FORMATS]
    assert {f["family"] for f in result["formats"]} == {"brep", "mesh"}
    assert all(set(f) == {"id", "label", "suffixes", "family", "units"} for f in result["formats"])
    assert result["filter"] == formats.filter_string()


def test_palette_list_is_the_tag_palette_and_needs_no_project() -> None:
    """`palette.list` is the client's only source for the style card's colour circles.

    The palette is server-owned (`geometry.model.SELECTION_PALETTE`): the tag commands assign from
    it in turn, and the condition catalogue refuses a tag colour outside it. A client that hardcoded
    a copy would drift from both, so the list is read over the API instead — which is also why this
    command takes no project: nothing about the palette depends on one, and a client that has not
    opened a project still needs it to draw the swatches.
    """
    from stokes.geometry.model import SELECTION_PALETTE

    session = Session()  # deliberately NO project.new: the command must not need one
    result = session.execute("palette.list")
    assert result["colors"] == [color.lower() for color in SELECTION_PALETTE]
    assert len(result["colors"]) == 10
    # Every entry is a colour the style command accepts, which is the point of driving the UI from
    # it.
    assert all(color.startswith("#") and len(color) == 7 for color in result["colors"])


def test_the_formats_reply_carries_the_upload_cap_and_it_is_the_enforced_one() -> None:
    """**One constant, three readers** (Claude, 2026-10-15).

    The browser reads `max_upload_bytes` to refuse an oversize file before sending it, the import
    check refuses it on the way in, and the upload route refuses the body. **If they could
    disagree, the browser would either refuse a file the server would take or send one it will
    reject** - so this asserts the reply is the same number the route enforces, not a copy.

    The client-side check is a courtesy, not a boundary: the route refuses an oversize body whatever
    the browser did.
    """
    from stokes.geometry.files import MAX_SOURCE_BYTES
    from stokes.server.app import MAX_UPLOAD_BYTES

    session = Session()  # no project: a client builds its picker before opening one
    reply = session.execute("geometry.formats")
    assert reply["max_upload_bytes"] == MAX_SOURCE_BYTES
    assert reply["max_upload_bytes"] == MAX_UPLOAD_BYTES, (
        "the browser's limit and the route's are different numbers; a file the browser "
        "accepts would be refused on upload, or one it refuses was acceptable"
    )
    # And the formats themselves are still there, since this is the same reply.
    assert len(reply["formats"]) == 6
