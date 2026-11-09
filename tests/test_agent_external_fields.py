"""Results of an imported case (plan T-310): discovered fields, their units, and written times.

**What must hold.** An external-profile job reads every scalar and vector its case writes, not a
fixed `U`/`p`; each field's unit comes from its own file's `dimensions`, including OpenFOAM 14's
named sets (`[velocity]`, `[kinematicPressure]`) that VTK's reader cannot parse; and the artifact
lists every written time. Every case here is synthetic (ADR-0029 decision 2).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stokes_agent import extract

HEADER = "FoamFile\n{\n    format ascii;\n    class volScalarField;\n}\n"


@pytest.mark.parametrize(
    ("inside", "unit"),
    [
        ("0 1 -1 0 0 0 0", "m/s"),
        ("0 2 -2 0 0", "m2/s2"),
        ("velocity", "m/s"),
        ("kinematicPressure", "m2/s2"),
        ("pressure", "Pa"),
        ("1 -1 -2 0 0 0 0", "Pa"),
        ("temperature", "K"),
        ("turbulentEpsilon", "m2/s3"),
        ("turbulentOmega", "1/s"),
        ("density", "kg/m3"),
        ("length*velocity", "m2/s"),
        ("volume^-1", "1/m3"),
        ("", "1"),
    ],
)
def test_dimension_sets_read_as_si(inside: str, unit: str) -> None:
    exponents = extract.parse_dimensions(inside)
    assert exponents is not None
    assert extract.si_unit(exponents) == unit


@pytest.mark.parametrize("inside", ["notADimension", "velocity*bogus", "1 2 3", "velocity^x"])
def test_an_unknown_dimension_set_has_no_unit(inside: str) -> None:
    assert extract.parse_dimensions(inside) is None


def test_case_units_read_each_field_file_at_the_time(tmp_path: Path) -> None:
    for time, dims in (("0", "[velocity]"), ("0.5", "[0 1 -1 0 0 0 0]")):
        folder = tmp_path / time
        folder.mkdir()
        (folder / "U").write_text(HEADER + f"dimensions {dims};\ninternalField uniform (0 0 0);\n")
    (tmp_path / "0.5" / "k").write_text(HEADER + "dimensions [turbulentKineticEnergy];\n")
    (tmp_path / "0.5" / "alpha.water").write_text(HEADER + "dimensions [];\n")
    (tmp_path / "0.5" / "odd").write_text(HEADER + "dimensions [notKnown];\n")
    units = extract.case_units(tmp_path, 0.5, ["U", "U_mag", "k", "alpha.water", "odd", "gone"])
    assert units == {"U": "m/s", "U_mag": "m/s", "k": "m2/s2", "alpha.water": "1"}


def test_case_units_never_read_through_a_link(tmp_path: Path) -> None:
    folder = tmp_path / "1"
    folder.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("dimensions [velocity];\n")
    try:
        (folder / "U").symlink_to(outside)
    except OSError:
        pytest.skip("this platform cannot make symbolic links here")
    assert extract.case_units(tmp_path, 1.0, ["U"]) == {}


def test_an_external_job_reads_every_field() -> None:
    assert extract.fields_for("of14.external") == ()
    assert extract.units_for("of14.external") == {}
    assert extract.engine_of_job({"profile": "external", "steps": []}) == "of14.external"
    steps = [{"argv": ["foamRun", "-solver", "fluid"]}]
    assert extract.engine_of_job({"steps": steps}) == "of14.fluid"
    assert extract.fields_for(None)[:2] == extract.FIELDS, "a model run reads U and p first"


def test_discovered_fields_pack_with_their_own_shape() -> None:
    points = np.zeros((4, 3), dtype="float32")
    patch = {
        "name": "walls",
        "points": points,
        "fields": {
            "k": np.arange(4, dtype="float32"),
            "alpha.water": np.ones((4, 1), dtype="float32"),
            "Ub": np.ones((4, 3), dtype="float32"),
        },
    }
    data, header = extract.pack_fields([patch], None, 0.25, {"k": "m2/s2"}, times=[0.0, 0.25, 0.5])
    assert header["times"] == [0.0, 0.25, 0.5] and header["time"] == 0.25
    found = header["patches"][0]["fields"]
    assert found["k"]["components"] == 1 and found["k"]["max"] == 3.0
    assert found["alpha.water"]["components"] == 1
    assert found["Ub"]["components"] == 3
    back, arrays = extract.unpack_fields(data)
    assert back["units"] == {"k": "m2/s2"}
    assert arrays["walls"]["k"].reshape(-1).tolist() == [0.0, 1.0, 2.0, 3.0]


def test_a_known_field_with_the_wrong_shape_is_still_refused() -> None:
    patch = {"name": "w", "points": np.zeros((2, 3)), "fields": {"U": np.zeros((2, 1))}}
    with pytest.raises(ValueError, match="components"):
        extract.pack_fields([patch], None, 0.0)


def test_discovery_reads_every_scalar_and_vector_array() -> None:
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonDataModel import vtkPolyData

    data = vtkPolyData().GetPointData()
    for name, values in (
        ("k", np.zeros(3)),
        ("U", np.zeros((3, 3))),
        ("tensor", np.zeros((3, 9))),
    ):
        array = numpy_to_vtk(values, deep=True)  # type: ignore[no-untyped-call]
        array.SetName(name)
        data.AddArray(array)
    assert extract._names(data, ()) == ("k", "U")
    assert extract._names(data, ("p",)) == ("p",), "a named list is used as given"
