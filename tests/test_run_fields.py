"""The desktop's `STKFLD1` reader and the two payload codecs it serves (T-216).

**The buffer is built here by the agent's own `pack_fields`**, so the desktop is tested against the
real format rather than a copy of it. What this file pins:

* every offset and count in the header is checked against `len(data)` **before** anything is
  sliced — a truncated buffer, a wrong magic, an offset past the end and an over-cap header are all
  refusals rather than a view that reads whatever happened to follow;
* `field_array` returns the exact stored values, in the shape a renderer wants: `(points,)` for one
  component and `(points, 3)` for three;
* the field codec round-trips through a reader written **here**, independently, so the client's
  own decoder has something to agree with;
* a patch with no array stays empty rather than gaining zeros.
"""

from __future__ import annotations

import hashlib
import json
import struct
from typing import Any

import numpy as np
import pytest

from stokes.geometry.errors import MeshFormatError
from stokes.geometry.transport import unpack
from stokes.mesh.surfaces import unpack_edges
from stokes.run.fields import (
    MAX_HEADER_BYTES,
    field_array,
    field_payload,
    read_header,
    slice_arrays,
    slice_edges,
    slice_transport,
)
from stokes_agent.extract import pack_fields

#: The fields both patches carry; `U_mag` follows `U`, like the agent emits it.
FIELDS = ("U", "U_mag", "p")


def quad(offset: float = 0.0) -> list[list[float]]:
    return [
        [offset, 0.0, 0.0],
        [offset + 1.0, 0.0, 0.0],
        [offset + 1.0, 1.0, 0.0],
        [offset, 1.0, 0.0],
    ]


def arrays(points: int = 4, scale: float = 1.0) -> dict[str, np.ndarray]:
    """One patch's three fields, `points` long, scaled so two patches differ."""
    u = np.array([[scale, 0.0, 0.0]] * points, dtype="float32")
    u[:, 1] = np.arange(points, dtype="float32")
    return {
        "U": u,
        "U_mag": np.linalg.norm(u, axis=1).astype("float32"),
        "p": np.arange(points, dtype="float32") * scale,
    }


def buffer(*, slice_: bool = True, second_fields: bool = True) -> tuple[bytes, dict[str, Any]]:
    """A two-patch artifact with a slice, as the agent writes it.

    `inlet` is a quad with all three fields; `walls` is a quad with `p` only unless
    `second_fields` — the "a patch with no array" case. The slice is the inlet's own quad in the
    plane x = 0, so a plot of `p` over it is at least plausible.
    """
    first = {
        "name": "inlet",
        "points": np.array(quad(0.0), dtype="float32"),
        "fields": arrays(4, 1.0),
    }
    second = {
        "name": "walls",
        "points": np.array(quad(1.0), dtype="float32"),
        "fields": arrays(4, 2.0) if second_fields else {},
    }
    plane = None
    if slice_:
        plane = {
            "origin": [0.0, 0.0, 0.0],
            "normal": [1.0, 0.0, 0.0],
            "positions": np.array(quad(0.0), dtype="float32"),
            "triangles": np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"),
            "edges": np.array([[0, 1], [1, 2], [2, 3], [0, 3]], dtype="uint32"),
            "fields": arrays(4, 3.0),
        }
    return pack_fields([first, second], plane, 1.5)


# ---------------------------------------------------------------- read_header


def test_the_header_of_a_real_buffer_is_accepted() -> None:
    data, written = buffer()
    header = read_header(data)
    assert header["version"] == 1
    assert header["time"] == 1.5
    assert [patch["name"] for patch in header["patches"]] == ["inlet", "walls"]
    assert [patch["points"] for patch in header["patches"]] == [4, 4]
    assert header["data_start"] == 12 + int.from_bytes(data[8:12], "little")
    # **`f32[components x points]` in header order**: `U` is three components of four points, so
    # `U_mag` starts where `U` ends rather than at zero.
    first, second_field = written["patches"][0]["fields"]["U"], None
    assert first["offset"] == 0, "the very first array starts at the data section"
    second_field = written["patches"][0]["fields"]["U_mag"]["offset"]
    assert second_field == 4 * 3 * 4, "four points, three components, four bytes each"
    assert header["slice"]["triangles"] == 2


def test_a_memoryview_is_accepted_like_bytes() -> None:
    """The routes hand it a `memoryview` of the cache file, not a copy."""
    data, _ = buffer()
    assert read_header(memoryview(data))["patches"][0]["name"] == "inlet"


def test_a_wrong_magic_is_refused() -> None:
    data, _ = buffer()
    with pytest.raises(MeshFormatError):
        read_header(b"NOTFLD1\0" + data[8:])


def test_a_truncated_buffer_is_refused() -> None:
    data, _ = buffer()
    with pytest.raises(MeshFormatError):
        read_header(data[: len(data) - 8])


def test_an_offset_past_the_end_of_the_data_is_refused() -> None:
    """**The header is the one thing that can lie about the bytes beside it.** A patch field whose
    offset runs past the buffer must be refused at read time, not sliced into whatever follows."""
    data, _ = buffer()
    header = json.loads(data[12 : 12 + int.from_bytes(data[8:12], "little")].decode())
    header["patches"][0]["fields"]["p"]["offset"] += 10_000
    with pytest.raises(MeshFormatError):
        read_header(rebuild(data, header))


def test_a_slice_offset_past_the_end_is_refused() -> None:
    data, _ = buffer()
    header = json.loads(data[12 : 12 + int.from_bytes(data[8:12], "little")].decode())
    header["slice"]["offset"] += 10_000
    with pytest.raises(MeshFormatError):
        read_header(rebuild(data, header))


def test_a_header_over_the_cap_is_refused() -> None:
    """16 MiB, like the surfaces reader: a length field can claim anything."""
    raw = b"STKFLD1\0" + struct.pack("<I", MAX_HEADER_BYTES + 1) + b" " * 4
    with pytest.raises(MeshFormatError):
        read_header(raw)


def test_an_unknown_version_is_refused() -> None:
    data, _ = buffer()
    header = json.loads(data[12 : 12 + int.from_bytes(data[8:12], "little")].decode())
    header["version"] = 2
    with pytest.raises(MeshFormatError):
        read_header(rebuild(data, header))


def test_a_header_that_is_not_json_is_refused() -> None:
    body = b"not json at all "
    with pytest.raises(MeshFormatError):
        read_header(b"STKFLD1\0" + struct.pack("<I", len(body)) + body)


def rebuild(data: bytes, header: dict[str, Any]) -> bytes:
    """The same body under a patched header — how a corrupt buffer is built for a test."""
    length = int.from_bytes(data[8:12], "little")
    body = data[12 + length :]
    text = json.dumps(header, separators=(",", ":")).encode("utf-8")
    text += b" " * ((-len(text)) % 4)
    return b"STKFLD1\0" + struct.pack("<I", len(text)) + text + body


# ---------------------------------------------------------------- field_array


def test_field_array_returns_the_exact_values() -> None:
    data, _ = buffer()
    header = read_header(data)
    got = field_array(data, header, 0, "p")
    assert got.dtype == np.dtype("<f4")
    assert got.shape == (4,)
    assert np.array_equal(got, np.arange(4, dtype="float32"))
    vector = field_array(data, header, 0, "U")
    assert vector.shape == (4, 3)
    assert np.array_equal(vector, arrays(4, 1.0)["U"])
    # A view, not a copy: the bytes are already in memory and this is the hot path.
    assert vector.base is not None


def test_field_array_of_the_second_patch_is_its_own() -> None:
    data, _ = buffer()
    header = read_header(data)
    assert np.array_equal(field_array(data, header, 1, "p"), np.arange(4, dtype="float32") * 2.0)


def test_a_patch_with_no_array_has_an_empty_fields_dict() -> None:
    """**Not zeros.** An `empty` patch has no `U`, and a zero there says the wall is at rest."""
    data, _ = buffer(second_fields=False)
    header = read_header(data)
    assert header["patches"][1]["fields"] == {}
    with pytest.raises(MeshFormatError):
        field_array(data, header, 1, "p")


def test_an_unknown_field_or_patch_is_refused() -> None:
    data, _ = buffer()
    header = read_header(data)
    with pytest.raises(MeshFormatError):
        field_array(data, header, 0, "nope")
    with pytest.raises(MeshFormatError):
        field_array(data, header, 9, "p")


# ---------------------------------------------------------------- the field codec


def read_payload(payload: bytes) -> tuple[dict[str, Any], np.ndarray]:
    """The client's own decoder, written here so the codec has two independent halves."""
    length = struct.unpack_from("<I", payload, 0)[0]
    assert length % 4 == 0, "the header is padded to a multiple of four"
    head = json.loads(payload[4 : 4 + length].decode("utf-8"))
    count, components = int(head["count"]), int(head["components"])
    values = np.frombuffer(payload, dtype="<f4", count=count * components, offset=4 + length)
    assert 4 + length + count * components * 4 == len(payload), "no bytes beyond the array"
    return head, values.reshape(count, components)


def test_the_field_payload_round_trips() -> None:
    values = np.linspace(0.0, 3.0, 12, dtype="float32").reshape(4, 3)
    payload = field_payload(values, "U", "m/s", 0.5, 2.5)
    head, got = read_payload(payload)
    assert head == {
        "version": 1,
        "field": "U",
        "units": "m/s",
        "components": 3,
        "count": 4,
        "min": 0.5,
        "max": 2.5,
    }
    assert np.array_equal(got, values)


def test_a_one_component_payload_keeps_its_shape() -> None:
    values = np.array([1.0, 2.0, 3.0], dtype="float32")
    head, got = read_payload(field_payload(values, "p", "m2/s2", 1.0, 3.0))
    assert (head["components"], head["count"]) == (1, 3)
    assert got.shape == (3, 1)
    assert np.array_equal(got.reshape(-1), values)


def test_a_float64_array_is_stored_as_float32() -> None:
    values = np.array([1.0, 2.0], dtype="float64")
    _head, got = read_payload(field_payload(values, "p", "m2/s2", 1.0, 2.0))
    assert got.dtype == np.dtype("<f4")


# ---------------------------------------------------------------- the slice


def test_slice_arrays_are_the_stored_geometry() -> None:
    data, _ = buffer()
    header = read_header(data)
    positions, triangles, edges = slice_arrays(data, header)
    assert positions.shape == (4, 3)
    assert triangles.shape == (2, 3)
    assert edges.shape == (4, 2)
    assert np.array_equal(triangles, np.array([[0, 1, 2], [0, 2, 3]], dtype="uint32"))
    assert np.array_equal(positions, np.array(quad(0.0), dtype="float32"))
    assert np.array_equal(field_array(data, header, -1, "p"), np.arange(4, dtype="float32") * 3.0)


def test_slice_transport_is_one_mesh_group() -> None:
    """Transport version 2, one group, `kind: "mesh"`, every face id 0 — the mesh routes' shape."""
    data, _ = buffer()
    header = read_header(data)
    payload = slice_transport(data, header)
    part = unpack(payload)
    assert part.units == "m"
    assert len(part.solids) == 1
    group = part.solids[0]
    assert group.kind == "mesh"
    assert (group.vertex_count, group.triangle_count) == (4, 2)
    assert (group.faces, group.face_base) == (1, 0)
    assert group.face_ids is not None and group.face_ids.tolist() == [0, 0]
    assert np.array_equal(group.positions, np.array(quad(0.0), dtype="float32"))
    # Normals are computed, so a triangle in the z = 0 plane points along z and nowhere else.
    assert np.allclose(np.abs(group.normals[:, 2]), 1.0, atol=1e-6)


def test_slice_transport_takes_a_colour() -> None:
    data, _ = buffer()
    payload = slice_transport(data, read_header(data), color=(0.1, 0.2, 0.3))
    assert unpack(payload).solids[0].color == pytest.approx((0.1, 0.2, 0.3))


def test_slice_edges_round_trip_through_the_t_210_codec() -> None:
    """**The same codec the mesh routes serve**, so the viewport has one edge reader."""
    data, _ = buffer()
    header = read_header(data)
    payload = slice_edges(data, header)
    length = struct.unpack_from("<I", payload, 0)[0]
    head = json.loads(payload[4 : 4 + length].decode("utf-8"))
    assert head == {"version": 1, "units": "m", "segments": 4}
    segments = unpack_edges(payload)
    assert segments.shape == (4, 6)
    # The quad's perimeter, end points in order.
    positions = np.array(quad(0.0), dtype="float32")
    assert np.array_equal(segments[0], np.concatenate((positions[0], positions[1])))


def test_a_slice_that_missed_the_mesh_still_reads() -> None:
    """`triangles == 0` means the plane missed; the transport and edges are then empty, not
    refused."""
    empty = {
        "origin": [0.0, 0.0, 0.0],
        "normal": [1.0, 0.0, 0.0],
        "positions": np.zeros((0, 3), dtype="float32"),
        "triangles": np.zeros((0, 3), dtype="uint32"),
        "edges": np.zeros((0, 2), dtype="uint32"),
        "fields": {"p": np.zeros((0,), dtype="float32")},
    }
    data, _ = pack_fields(
        [
            {
                "name": "inlet",
                "points": np.array(quad(), dtype="float32"),
                "fields": arrays(4, 1.0),
            }
        ],
        empty,
        1.0,
    )
    header = read_header(data)
    assert header["slice"]["triangles"] == 0
    _positions, triangles, edges = slice_arrays(data, header)
    assert triangles.shape == (0, 3) and edges.shape == (0, 2)
    assert unpack(slice_transport(data, header)).solids[0].triangle_count == 0
    assert unpack_edges(slice_edges(data, header)).shape == (0, 6)


# ---------------------------------------------------------------- the cache path, no numpy sideways


def test_the_module_does_not_import_numpy_at_module_scope_in_the_startup_path() -> None:
    """`stokes.server.app` and `stokes.api.commands.run` import this package; numpy is imported
    inside the functions that need it, like the surfaces reader (performance.md, headless core).

    The `TYPE_CHECKING` import is the exception that proves the rule: it is never executed.
    """
    import ast
    import pathlib

    source = (pathlib.Path(__file__).parent.parent / "stokes" / "run" / "fields.py").read_text()
    tree = ast.parse(source)
    at_module_scope = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "numpy" in ast.dump(node)
        and not isinstance(node, ast.If)
    ]
    assert at_module_scope == [], "numpy must be imported inside the functions"
    # And it really is not in `sys.modules` after a fresh import of the module alone.
    code = "import sys; sys.modules['numpy'] = None; import stokes.run.fields as f; print(f.MAGIC)"
    out = subprocess_of(code)
    assert out.strip() == "b'STKFLD1\\x00'"


def subprocess_of(code: str) -> str:
    """Run `code` in a child interpreter with the repository on the path, and return its stdout."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    env = {
        "PYTHONPATH": f"{root}{':' if sys.platform != 'win32' else ';'}{root / 'agent'}",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_field_units_are_the_si_ones_the_agent_writes() -> None:
    """The legend a user reads repeats these; `p` in incompressible OpenFOAM is `p/rho`."""
    data, written = buffer()
    header = read_header(data)
    assert header["units"] == {"U": "m/s", "U_mag": "m/s", "p": "m2/s2"}
    assert written["units"] == header["units"]


def test_a_digest_of_the_buffer_is_stable() -> None:
    """A cheap sanity check that the fixture is deterministic: two builds hash the same."""
    first, _ = buffer()
    second, _ = buffer()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def _with_range(value: float) -> bytes:
    """A real buffer whose first patch's `U_mag` range is replaced by ``value``.

    The header is rewritten rather than the artifact rebuilt, so the test exercises exactly what a
    hostile or unlucky host can send: the same file, with one number changed.
    """
    raw, header = buffer()
    # **Eight bytes of magic, THEN the length** - `MAGIC + u32 + JSON`, measured, not assumed.
    # My first version read the length from offset 0 and decoded the magic as JSON.
    from stokes.run.fields import MAGIC

    length = struct.unpack_from("<I", raw, len(MAGIC))[0]
    body = raw[len(MAGIC) + 4 : len(MAGIC) + 4 + length]
    text = json.loads(body.decode("utf-8"))
    text["patches"][0]["fields"]["U_mag"]["min"] = value
    text["patches"][0]["fields"]["U_mag"]["max"] = value
    encoded = json.dumps(text).encode("utf-8")
    padded = encoded + b" " * (-len(encoded) % 4)
    del header
    return MAGIC + struct.pack("<I", len(padded)) + padded + raw[len(MAGIC) + 4 + length :]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_range_is_refused(value: float) -> None:
    """**Opus item 13: `json.loads` reads `NaN`, and JavaScript's `JSON.parse` does not.**

    Measured end to end: the reader accepted `min: NaN`, `field_payload` wrote it out literally, and
    a browser's `JSON.parse` **threw** `Unexpected token 'N'` on the header - so every field of that
    patch failed to draw although the DATA beside it was perfectly good. Python's `json.loads`
    tolerates `NaN`, which is why the failure only appears in the browser.

    `finite()` existed for this and nothing called it. Refusing at the reader means no payload
    with a
    non-finite number can be built at all, which is the right place: the value is the host's claim
    about a field, and a range no client can plot is not one to pass on.
    """
    with pytest.raises(MeshFormatError) as caught:
        read_header(_with_range(value))
    assert "non-finite" in str(caught.value)


def test_a_finite_range_is_still_accepted() -> None:
    """**The false-refusal half.** An ordinary range must pass, including the extremes."""
    for value in (0.0, -1.5e-3, 1.2e12):
        read_header(_with_range(value))
