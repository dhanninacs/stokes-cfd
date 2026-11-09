"""The iso-surface contour (ADR-0026 decision 3), tested on a dataset VTK builds in memory.

**A hand-written OpenFOAM case has one cell, whose point values are all equal**, so a contour of
it is empty whatever the value. The contour is therefore tested on a structured grid whose point
field is the x coordinate: the surface at `value` must then be the plane `x == value`, and the
interpolated field on it must equal `value`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest


def _x_ramp_grid() -> Any:
    """A 5x5x5-point grid on [0, 1]^3 whose point field `p` equals the point's x coordinate."""
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonDataModel import vtkImageData

    grid = vtkImageData()
    grid.SetDimensions(5, 5, 5)
    grid.SetSpacing(0.25, 0.25, 0.25)
    grid.SetOrigin(0.0, 0.0, 0.0)
    # VTK orders points with x fastest, so the x ramp repeats every five points.
    x = np.tile(np.arange(5, dtype=np.float64) * 0.25, 25)
    array = numpy_to_vtk(x, deep=True)
    array.SetName("p")
    grid.GetPointData().AddArray(array)
    grid.GetPointData().SetActiveScalars("p")
    return grid


def test_the_contour_of_an_x_ramp_is_the_plane_at_that_x() -> None:
    """**The surface is the plane x = 0.5**, every point of it on that plane, and the field carried
    onto it is the value the surface was cut at."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import contour_surface

    cut = contour_surface(_x_ramp_grid(), "p", 0.5, 0.0)
    positions = cut["positions"]
    assert positions.shape[0] > 0, "a value inside the range must give a surface"
    assert np.allclose(positions[:, 0], 0.5, atol=1e-5), "the surface is not the plane x = 0.5"
    assert cut["triangles"].shape[1] == 3
    assert np.allclose(cut["fields"]["p"][:, 0], 0.5, atol=1e-5), "the field is not the value"


def test_a_value_outside_the_range_gives_an_empty_surface_with_no_fields() -> None:
    """**An empty surface carries no fields**, the rule `cut_slice` applies to an empty cut: a
    zero-length array must not read as a field of zeros."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import contour_surface

    cut = contour_surface(_x_ramp_grid(), "p", 9.0, 0.0)
    assert cut["positions"].shape == (0, 3)
    assert cut["triangles"].shape == (0, 3)
    assert cut["fields"] == {}
    assert cut["value"] == 9.0


def test_speed_is_contoured_from_the_velocity_the_case_has() -> None:
    """**`U_mag` is derived from `U` before the contour** (RESULTS-OVERHAUL §5a): the case writes
    no speed array, and the Looks card's Iso was disabled for velocity because of it. With
    `U = (x, 0, 0)` the speed is the x ramp, so the surface at 0.5 is the plane x = 0.5 and
    carries `U_mag == 0.5`."""
    pytest.importorskip("vtkmodules")
    from vtkmodules.util.numpy_support import numpy_to_vtk

    from stokes_agent.extract import contour_surface

    grid = _x_ramp_grid()
    x = np.tile(np.arange(5, dtype=np.float64) * 0.25, 25)
    velocity = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    array = numpy_to_vtk(np.ascontiguousarray(velocity), deep=True)
    array.SetName("U")
    grid.GetPointData().AddArray(array)

    cut = contour_surface(grid, "U_mag", 0.5, 0.0)
    assert cut["positions"].shape[0] > 0
    assert np.allclose(cut["positions"][:, 0], 0.5, atol=1e-5)
    assert np.allclose(cut["fields"]["U_mag"][:, 0], 0.5, atol=1e-5)


def test_a_field_the_case_does_not_have_is_refused() -> None:
    """**A missing field is a refusal, not an empty surface**: an empty answer would hide that the
    caller asked for a field the case never wrote."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import contour_surface

    with pytest.raises(ValueError, match="no point field 'T'"):
        contour_surface(_x_ramp_grid(), "T", 0.5, 0.0)


def test_the_surface_is_the_same_on_two_contours() -> None:
    """**Determinism, as the slice's**: a contour that reorders points between two runs would make
    a cached surface line up with the wrong vertices."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import contour_surface

    first = contour_surface(_x_ramp_grid(), "p", 0.5, 0.0)
    second = contour_surface(_x_ramp_grid(), "p", 0.5, 0.0)
    assert np.array_equal(first["positions"], second["positions"])
    assert np.array_equal(first["triangles"], second["triangles"])


def test_an_iso_cut_packs_with_its_field_and_value_and_no_plane() -> None:
    """**`iso.cut` packs what `contour_surface` returns.** The header names the field and value the
    server's cache checks a file against, and claims no plane: `pack_fields` read `origin`
    unconditionally, so every iso cut raised `KeyError` before this."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import SLICE_KEY, contour_surface, pack_fields, unpack_fields

    cut = contour_surface(_x_ramp_grid(), "p", 0.5, 0.0)
    data, header = pack_fields([], cut, 0.0)
    entry = header["slice"]
    assert entry["iso"] == {"field": "p", "value": 0.5}
    assert "origin" not in entry and "normal" not in entry
    assert entry["triangles"] == cut["triangles"].shape[0] > 0
    _header, arrays = unpack_fields(data)
    assert np.array_equal(arrays[SLICE_KEY]["positions"], cut["positions"])


def test_the_volume_range_covers_cells_and_points_and_skips_vectors() -> None:
    """**ADR-0026 amendment 2**: the range is over every cell and point of the mesh, so an interior
    cell value past every point value still widens it; a vector has no single range."""
    pytest.importorskip("vtkmodules")
    from vtkmodules.util.numpy_support import numpy_to_vtk

    from stokes_agent.extract import volume_ranges

    grid = _x_ramp_grid()  # point p over 0..1
    cells = np.zeros(grid.GetNumberOfCells(), dtype=np.float64)
    cells[7] = 4.0  # an interior extreme no point carries
    cells[3] = -2.0
    array = numpy_to_vtk(cells, deep=True)
    array.SetName("p")
    grid.GetCellData().AddArray(array)
    vector = numpy_to_vtk(np.zeros((grid.GetNumberOfPoints(), 3)), deep=True)
    vector.SetName("U")
    grid.GetPointData().AddArray(vector)

    found = volume_ranges(grid, ("U", "p"))
    assert found == {"p": (-2.0, 4.0)}


def test_the_volume_table_is_packed_into_the_header() -> None:
    """`pack_fields(volume=)` writes the table the desktop checks an iso value against; a non-finite
    or inverted entry is dropped, and a cut packed without one has no table."""
    from stokes_agent.extract import pack_fields

    _data, header = pack_fields(
        [], None, 0.0, volume={"p": (-1.0, 5.0), "T": (float("nan"), 1.0), "x": (2.0, 1.0)}
    )
    assert header["volume"] == {"p": {"min": -1.0, "max": 5.0}}
    _data, plain = pack_fields([], None, 0.0)
    assert "volume" not in plain


def test_a_plane_cut_still_names_its_plane_and_is_no_iso() -> None:
    """The plane's header is unchanged by the iso entry: origin and normal first, no `iso`."""
    from stokes_agent.extract import pack_fields

    plane = {
        "origin": [0.0, 0.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
        "positions": np.zeros((3, 3), dtype="float32") + np.eye(3, dtype="float32"),
        "triangles": np.array([[0, 1, 2]], dtype="uint32"),
        "edges": np.array([[0, 1]], dtype="uint32"),
        "fields": {},
    }
    _data, header = pack_fields([], plane, 0.0)
    assert list(header["slice"])[:2] == ["origin", "normal"]
    assert "iso" not in header["slice"]
