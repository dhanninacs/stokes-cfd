"""Extract a finished mesh's boundary surfaces as one artifact (T-147a, T-209).

The desktop never reads `polyMesh` (ADR-0005). The agent reads the mesh with VTK, turns every
boundary
patch into triangles (to draw it) and polygon edges (the cell edges on the surface, for the
"surface with
cells" view), and writes one binary artifact the desktop fetches in chunks.

## Four decisions worth knowing

**A fan per polygon, not `vtkTriangleFilter`.** A polygon `v0..vn-1` becomes `(v0, vi, vi+1)`. The
filter would triangulate just as correctly and hide **which edges are cell edges** — a triangle
filter's output has diagonals in it that are not mesh edges, so the "surface with cells" view would
draw lines across the middle of every quad. Keeping the original polygons is what lets
`unique_edges` produce the real thing.

**`patch_type` comes from `constant/polyMesh/boundary`, not from VTK.** Measured with VTK 9.6.2: the
reader exposes the patches and their geometry but NOT their OpenFOAM type — the type appears only as
a `group/empty` / `group/wall` entry in its status list. The brief forbids a hand-written `polyMesh`
parser in product code, and this is not one: it is a scan for `type …;` inside a named entry, and a
patch whose type cannot be found stays `"patch"`.

**VTK is imported inside functions.** `tools/check_forbidden_imports.py` allows it in the agent
only, and importing it at module level would make every agent start pay for it.

**EVERY path this module touches goes through `_contained`.** This is an invariant, not a style
note, and it exists because five separate escapes were found in this one file — each fixed, each
followed by another shape getting through:

1. a resolved target compared against an *unresolved* folder;
2. a target compared against **its own parent's resolution** — trivially equal;
3. a symlinked **file** inside a legitimate folder;
4. the folder **used** for the write being a different path from the folder that was **validated**;
5. **`index.json` written with no check at all** — the one name that is not a client's, and
   therefore the one a symlink can be planted at by name.

Every one of those was a *special case* that skipped the check. There are no special cases now: the
writer, both readers, the index and the cache lookup all call `_contained`, so a sixth shape would
have to be a path that never reaches it. **If you add a path here, that is the rule.**

A sixth, different in kind, was also fixed: `cached_artifact` caught `ValueError` around its whole
body, which is exactly what `_contained` raises — so a containment refusal was swallowed as a cache
miss and the caller reported an error about the wrong file. **The containment calls are deliberately
left unfenced**; only the *reading* is fenced, so a corrupt cache is a miss while a refusal
propagates.

## T-215: the point fields beside them

`extract_fields` writes a second artifact, `STKFLD1`, holding one value per **patch vertex** for the
fields the viewport colours. Its patch order and point counts are the surfaces artifact's, and that
is the one invariant the task exists to prove: the desktop tints the vertices it already draws, so
an array in a different order is a wrong picture, not a visible failure. `_check_patch_order`
compares them and raises `AgentMismatch` (`agent.mismatch`) rather than falling back.

`pack_fields` is the layout half and is **pure numpy — no VTK import, not even a lazy one**, so the
format is testable on a host without VTK. `extract_fields`, which reads the case, and `cut_plane`
are the only functions here that import it, inside the function as everywhere else.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np

from stokes.errors import StokesError

#: `bytes[8]`, so the file starts with a readable tag and the version is in the name.
MAGIC = b"STKMSH1\0"

#: Bumped when the layout changes in a way a reader must know about.
VERSION = 1

#: The most an artifact may be. Past this the extraction is refused rather than written: a 2 GiB
#: file the desktop cannot fetch is worse than an error that says so.
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024

#: The most one `artifact.get` chunk may return, matching the brief's 8 MiB.
MAX_CHUNK = 8 * 1024 * 1024

#: An artifact name: no path separators, **not a bare dot entry** — `.` and `..` match the character
#: class but name directories, so the containment check would be doing all the work — and bounded in
#: length.
NAME_RE = re.compile(r"^(?!\.{1,2}$)[a-z0-9_.-]{1,64}$")

# ---------------------------------------------------------------- the point fields (T-215)

#: `bytes[8]`, the field artifact's own readable tag. A second magic rather than the mesh one,
#: because a client that read one as the other would take a header length for a patch offset.
MAGIC_FIELDS = b"STKFLD1\0"

#: What is read from the case, in the order it is stored. `U_mag` is **computed**, never read, and
#: it follows `U` so a client can use the first two entries as one vector.
FIELDS: tuple[str, ...] = ("U", "p")

#: The SI units of the stored quantities. Incompressible OpenFOAM's `p` is `p/rho`, so it is
#: kinematic pressure, m2/s2 — the brief's correction, and a claim the desktop's colour legend
#: repeats to the user.
FIELD_UNITS: dict[str, str] = {"U": "m/s", "U_mag": "m/s", "p": "m2/s2"}

#: How many components a field has. Measured rather than assumed: the reader reports
#: `GetNumberOfComponents()` 3 for `U` and 1 for `p` on a case with a `0/U`/`0/p`, and deriving it
#: from the array's length would silently read a 1-component array as a third of a vector.
COMPONENTS: dict[str, int] = {
    "U": 3,
    "U_mag": 1,
    "p": 1,
    "T": 1,
    "UMean": 3,
    "UMean_mag": 1,
    "pMean": 1,
    "TMean": 1,
    "k": 1,
    "epsilon": 1,
    "omega": 1,
    "nut": 1,
    "kMean": 1,
    "nutMean": 1,
}

#: Fields a model run reads **when the case has them**: the turbulence model's own variables and
#: the time averages a transient run's `fieldAverage` writes (`UMean`, `pMean`, `kMean`, `nutMean`,
#: and `TMean` on the energy engine). Every reader takes a field from the arrays it finds and skips
#: one that is absent, so a laminar steady case reads exactly `U` and `p` as before and asking for
#: one of these costs nothing. `UMean_mag` is derived from `UMean` as `U_mag` is from `U`.
OPTIONAL_FIELDS: tuple[str, ...] = (
    "UMean",
    "pMean",
    "k",
    "epsilon",
    "omega",
    "nut",
    "kMean",
    "nutMean",
)

#: The SI units of the optional fields; `pMean` takes the engine's `p` unit (`units_for`).
OPTIONAL_UNITS: dict[str, str] = {
    "UMean": "m/s",
    "UMean_mag": "m/s",
    "k": "m2/s2",
    "kMean": "m2/s2",
    "epsilon": "m2/s3",
    "omega": "1/s",
    "nut": "m2/s",
    "nutMean": "m2/s",
}

#: Each derived magnitude and the vector it is the length of, in header order.
MAGNITUDES: dict[str, str] = {"U": "U_mag", "UMean": "UMean_mag"}


def with_magnitudes(found: dict[str, np.ndarray], shape: tuple[int, ...] = (-1,)) -> None:
    """Add `U_mag` and `UMean_mag` beside their vectors when absent (in place)."""
    for vector, magnitude in MAGNITUDES.items():
        values = found.get(vector)
        if values is not None and magnitude not in found and values.ndim == 2:
            found[magnitude] = np.linalg.norm(values, axis=1).astype("float32").reshape(shape)


#: The energy engine (ADR-0027 step 4). Its `p` is the **absolute** static pressure in Pa
#: (`p = p_rgh + rho*gh + pRef`), and `T` is the temperature in K. `p_rgh` is the solver's working
#: variable and is deliberately not read: it is not a pressure a user should be shown.
ENERGY_ENGINE = "of14.fluid"
ENERGY_FIELDS: tuple[str, ...] = ("U", "p", "T")
ENERGY_FIELD_UNITS: dict[str, str] = {"U": "m/s", "U_mag": "m/s", "p": "Pa", "T": "K"}

#: The engine a solve step's `foamRun -solver <name>` belongs to. The agent imports nothing from
#: `stokes.*`, so the job record's own argv is how it knows which engine wrote the case.
SOLVER_ENGINES: dict[str, str] = {
    "incompressibleFluid": "of14.incompressibleFluid",
    "fluid": ENERGY_ENGINE,
}


#: An imported case (ADR-0029, a job under the `external` profile). Its fields are whatever the
#: case writes - `k`, `alpha.water`, `T`, `nut` - so none is named here: `fields_for` returns the
#: empty tuple, which every reader below takes as "every scalar and vector array the reader found",
#: and the units come from each field file's own `dimensions` (`case_units`).
EXTERNAL_ENGINE = "of14.external"

#: The most fields one artifact carries when they are discovered rather than named.
MAX_DISCOVERED = 64


def fields_for(engine: str | None) -> tuple[str, ...]:
    """The fields read from a case written for ``engine``; the incompressible set otherwise.

    **Per job, not global**, so an incompressible result reads exactly what it always did. An
    imported case gets the empty tuple: every field it has (`_names`).
    """
    if engine == EXTERNAL_ENGINE:
        return ()
    if engine == ENERGY_ENGINE:
        return (*ENERGY_FIELDS, *OPTIONAL_FIELDS, "TMean")
    return (*FIELDS, *OPTIONAL_FIELDS)


def engine_of_job(state: Any) -> str | None:
    """The engine a job record belongs to: `of14.external` for an external-profile job, else the
    one its `foamRun -solver` names (`engine_of_steps`)."""
    if isinstance(state, dict) and state.get("profile") == "external":
        return EXTERNAL_ENGINE
    return engine_of_steps(state.get("steps") if isinstance(state, dict) else None)


def _names(data: Any, fields: tuple[str, ...]) -> tuple[str, ...]:
    """The array names to read from VTK point or cell data: ``fields`` when given, else every
    scalar or vector array present (at most `MAX_DISCOVERED`), in the order VTK holds them."""
    if fields:
        return fields
    found: list[str] = []
    for index in range(int(data.GetNumberOfArrays())):
        array = data.GetArray(index)
        name = data.GetArrayName(index)
        if array is None or not isinstance(name, str) or not name:
            continue
        if int(array.GetNumberOfComponents()) in (1, 3) and name not in found:
            found.append(name)
        if len(found) >= MAX_DISCOVERED:
            break
    return tuple(found)


def units_for(engine: str | None) -> dict[str, str]:
    """The SI units of the stored quantities for ``engine``, as the header records them.

    An imported case's units are not known by engine; they are read from its files (`case_units`),
    so this returns an empty table for it.
    """
    if engine == EXTERNAL_ENGINE:
        return {}
    base = dict(ENERGY_FIELD_UNITS if engine == ENERGY_ENGINE else FIELD_UNITS)
    extra = {**OPTIONAL_UNITS, "pMean": base["p"]}
    if engine == ENERGY_ENGINE:
        extra["TMean"] = "K"
    return {**base, **extra}


#: OpenFOAM 14's named dimension sets, as `[mass, length, time, temperature, moles, current,
#: luminous intensity]` exponents. Measured over the 260 tutorials: these names cover every named
#: set their field files use but a handful; an unknown name gives no unit rather than a wrong one.
NAMED_DIMENSIONS: dict[str, tuple[float, ...]] = {
    "dimless": (0, 0, 0, 0, 0, 0, 0),
    "mass": (1, 0, 0, 0, 0, 0, 0),
    "length": (0, 1, 0, 0, 0, 0, 0),
    "time": (0, 0, 1, 0, 0, 0, 0),
    "temperature": (0, 0, 0, 1, 0, 0, 0),
    "moles": (0, 0, 0, 0, 1, 0, 0),
    "area": (0, 2, 0, 0, 0, 0, 0),
    "volume": (0, 3, 0, 0, 0, 0, 0),
    "velocity": (0, 1, -1, 0, 0, 0, 0),
    "acceleration": (0, 1, -2, 0, 0, 0, 0),
    "density": (1, -3, 0, 0, 0, 0, 0),
    "pressure": (1, -1, -2, 0, 0, 0, 0),
    "kinematicPressure": (0, 2, -2, 0, 0, 0, 0),
    "kinematicStress": (0, 2, -2, 0, 0, 0, 0),
    "kinematicViscosity": (0, 2, -1, 0, 0, 0, 0),
    "dynamicViscosity": (1, -1, -1, 0, 0, 0, 0),
    "dynamicDiffusivity": (1, -1, -1, 0, 0, 0, 0),
    "turbulentViscosity": (0, 2, -1, 0, 0, 0, 0),
    "turbulentKineticEnergy": (0, 2, -2, 0, 0, 0, 0),
    "turbulentEpsilon": (0, 2, -3, 0, 0, 0, 0),
    "turbulentOmega": (0, 0, -1, 0, 0, 0, 0),
    "kineticEnergyDensity": (1, -1, -2, 0, 0, 0, 0),
    "momentumDensity": (1, -2, -1, 0, 0, 0, 0),
    "heatFluxDensity": (1, 0, -3, 0, 0, 0, 0),
    "power": (1, 2, -3, 0, 0, 0, 0),
    "energy": (1, 2, -2, 0, 0, 0, 0),
    "force": (1, 1, -2, 0, 0, 0, 0),
    "massFlux": (1, 0, -1, 0, 0, 0, 0),
    "volumetricFlux": (0, 3, -1, 0, 0, 0, 0),
}
_SI_BASE = ("kg", "m", "s", "K", "mol", "A", "cd")
_DIMS_RE = re.compile(r"\bdimensions\s*\[([^\]\n]{0,200})\]")
_NAMED_TERM_RE = re.compile(r"^([A-Za-z]+)(?:\^(-?[0-9]+(?:\.[0-9]+)?))?$")
_TIME_DIR_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
#: How much of a field file is read for its `dimensions` line: the header, never the values.
UNITS_READ_BYTES = 8192


def parse_dimensions(text: str) -> tuple[float, ...] | None:
    """The seven exponents of a dimension set's inside (`0 1 -1 0 0 0 0`, `velocity`,
    `length*velocity`, `volume^-1`), or None when it is not one this reader knows."""
    text = text.strip()
    if not text:
        return NAMED_DIMENSIONS["dimless"]
    parts = text.split()
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        numbers = []
    if numbers and len(numbers) in (5, 7):
        return tuple(numbers + [0.0] * (7 - len(numbers)))
    total = [0.0] * 7
    for term in text.replace(" ", "").split("*"):
        match = _NAMED_TERM_RE.match(term)
        if match is None or match.group(1) not in NAMED_DIMENSIONS:
            return None
        power = float(match.group(2) or 1)
        for index, exponent in enumerate(NAMED_DIMENSIONS[match.group(1)]):
            total[index] += exponent * power
    return tuple(total)


def si_unit(exponents: tuple[float, ...]) -> str:
    """A dimension set as SI text in the header's style (`m/s`, `m2/s2`, `Pa`, `K`, `1`)."""
    if tuple(exponents) == NAMED_DIMENSIONS["pressure"]:
        return "Pa"
    top: list[str] = []
    bottom: list[str] = []
    for symbol, exponent in zip(_SI_BASE, exponents, strict=False):
        if exponent == 0:
            continue
        power = abs(exponent)
        text = symbol if power == 1 else f"{symbol}{power:g}"
        (top if exponent > 0 else bottom).append(text)
    if not top and not bottom:
        return "1"
    head = " ".join(top) if top else "1"
    return head if not bottom else f"{head}/{' '.join(bottom)}"


def _time_dir(case_dir: Path, time: float) -> Path | None:
    """The case's folder for ``time``: the numeric folder whose value is nearest."""
    best: tuple[float, Path] | None = None
    try:
        entries = list(case_dir.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not _TIME_DIR_RE.match(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        gap = abs(float(entry.name) - time)
        if best is None or gap < best[0]:
            best = (gap, entry)
    return best[1] if best is not None else None


def case_units(case_dir: Path, time: float, names: list[str]) -> dict[str, str]:
    """Each field's SI unit from its own file's `dimensions`, at ``time``; unknown ones are left
    out. `U_mag` takes `U`'s unit. **Only the header is read**, and never through a link."""
    folder = _time_dir(Path(case_dir), time)
    units: dict[str, str] = {}
    for name in names:
        if folder is None or name in MAGNITUDES.values() or not NAME_RE.match(name.lower()):
            continue
        path = folder / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as handle:
                head = handle.read(UNITS_READ_BYTES).decode("latin-1")
        except OSError:
            continue
        match = _DIMS_RE.search(head)
        exponents = parse_dimensions(match.group(1)) if match is not None else None
        if exponents is not None:
            units[name] = si_unit(exponents)
    for vector, magnitude in MAGNITUDES.items():
        if vector in units:
            units[magnitude] = units[vector]
    return units


def engine_of_steps(steps: Any) -> str | None:
    """The engine a job record's steps name through `foamRun -solver <name>`, or None.

    A record is data from disk, so anything that is not a list of step objects with a string argv
    is read as "no engine" rather than raised on: the caller then falls back to the incompressible
    fields, which is what every job before ADR-0027 was.
    """
    if not isinstance(steps, list):
        return None
    for step in steps:
        argv = step.get("argv") if isinstance(step, dict) else None
        if not isinstance(argv, list) or not argv or argv[0] != "foamRun":
            continue
        for index, item in enumerate(argv[:-1]):
            if item == "-solver" and isinstance(argv[index + 1], str):
                return SOLVER_ENGINES.get(argv[index + 1])
    return None


#: The fraction of the bbox **diagonal** the default plane is moved along the normal.
#:
#: The centre of a block-meshed duct is exactly a face plane of its grid, and `vtkCutter` through a
#: face's vertices gives duplicated or zero-area polygons from both neighbouring cells
#: (correction 1 of the brief). An offset scaled by the diagonal is the same fraction of a cell
#: whatever the mesh size, so it steps off the face without leaving the interior.
SLICE_OFFSET_FRACTION = 1e-3

#: The most a `float32` holds, used as an overflow guard before the artifact is measured. Packing
#: past this and refusing afterwards would mean allocating a buffer over the limit in order to
#: discover it is over the limit.
_F32_MAX = 3.4028234663852886e38

#: The key `unpack_fields` returns the slice's arrays under. A slice is a separate section of the
#: buffer from the patches, so it needs a name; an OpenFOAM patch name cannot start with an
#: underscore, and `_check_patch_order` compares names position by position anyway, so this cannot
#: shadow a real one.
SLICE_KEY = "__slice__"


# ---------------------------------------------------------------------- the pure core


def triangulate_fan(offsets: np.ndarray, connectivity: np.ndarray) -> np.ndarray:
    """A fan per polygon: `n - 2` triangles for an `n`-gon, as a **`uint32`** `(T, 3)` array.

    (A reviewer checked both: a triangle gives 1, a quad 2, a pentagon 3 — so `n - 2`, not `n -
    1`; and the
    dtype the code produces is `uint32`, not the `int32` this docstring used to claim.)

    Polygon `v0..vn-1` gives `(v0, vi, vi+1)` for `i = 1..n-2`. **No Python loop over faces**: the
    polygons are unpacked with numpy, which is what makes a million-cell mesh's surface tractable.
    """
    counts = np.diff(np.asarray(offsets).astype("int64"))
    if counts.size == 0:
        return np.zeros((0, 3), dtype="uint32")
    total = int(counts.sum())
    # Which polygon each connectivity entry belongs to, and its position within it.
    polygon = np.repeat(np.arange(counts.size, dtype="int64"), counts)
    within = np.arange(total, dtype="int64") - np.repeat(
        np.concatenate(([0], np.cumsum(counts)[:-1])), counts
    )
    corners = np.asarray(connectivity).astype("int64")
    # `v0` of the polygon each entry belongs to.
    first = corners[np.concatenate(([0], np.cumsum(counts)[:-1]))][polygon]
    # The fan: `(v0, vi, vi+1)` for i = 1 .. n-2, i.e. every position except the first and the last.
    keep = (within >= 1) & (within <= counts[polygon] - 2)
    here = corners[keep]
    nxt = corners[np.nonzero(keep)[0] + 1]
    return np.stack((first[keep], here, nxt), axis=1).astype("uint32")


def unique_edges(offsets: np.ndarray, connectivity: np.ndarray) -> np.ndarray:
    """The polygons' edges, each once, sorted within every pair, as a **`uint32`** `(E, 2)` array.

    **The polygon edges, not the triangles'.** An edge shared by two polygons appears once, so the
    "surface with cells" view draws the mesh's own edges and no diagonals.
    """
    counts = np.diff(np.asarray(offsets).astype("int64"))
    if counts.size == 0:
        return np.zeros((0, 2), dtype="uint32")
    total = int(counts.sum())
    polygon = np.repeat(np.arange(counts.size, dtype="int64"), counts)
    corners = np.asarray(connectivity).astype("int64")
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    # Every consecutive pair within a polygon. **The wrap from the last corner back to the first
    # is a real
    # edge** (a polygon is closed), and it is the one pair an `+1` index would run off the end
    # for, so it
    # is computed by position rather than by shifting the array.
    position = np.arange(total, dtype="int64") - np.repeat(starts, counts)
    last = counts[polygon] - 1
    following = np.arange(total, dtype="int64") + 1
    nxt = np.where(position == last, np.repeat(starts, counts), following)
    pairs = np.stack((corners, corners[nxt]), axis=1)
    pairs = np.sort(pairs, axis=1)
    return np.unique(pairs, axis=0).astype("uint32")


def pack_artifact(patches: list[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    """The artifact bytes and its header from already-computed patches.

    Each patch is `{"name", "patch_type", "points" (N,3) float32, "triangles" (T,3) uint32,
    "edges" (E,2) uint32, "faces" int}`. Indices refer to the patch's own points, so a patch is
    self-contained and the desktop can draw one without the others.
    """
    header: dict[str, Any] = {"version": VERSION, "units": "m", "patches": []}
    lo = np.array([np.inf, np.inf, np.inf], dtype="float64")
    hi = np.array([-np.inf, -np.inf, -np.inf], dtype="float64")
    blocks: list[bytes] = []
    offset = 0
    for index, patch in enumerate(patches):
        positions = np.ascontiguousarray(patch["points"], dtype="<f4")
        triangles = np.ascontiguousarray(patch["triangles"], dtype="<u4")
        edges = np.ascontiguousarray(patch["edges"], dtype="<u4")
        if positions.size:
            lo = np.minimum(lo, positions.min(axis=0))
            hi = np.maximum(hi, positions.max(axis=0))
        raw = positions.tobytes() + triangles.tobytes() + edges.tobytes()
        blocks.append(raw)
        header["patches"].append(
            {
                "name": str(patch["name"]),
                "index": index,
                "patch_type": str(patch.get("patch_type") or "patch"),
                "points": int(positions.shape[0]),
                "triangles": int(triangles.shape[0]),
                "edges": int(edges.shape[0]),
                "faces": int(patch.get("faces") or 0),
                "offset": offset,
            }
        )
        offset += len(raw)
    if np.isfinite(lo).all():
        header["bbox"] = {"min": [float(v) for v in lo], "max": [float(v) for v in hi]}
    else:
        header["bbox"] = {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}
    text = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (-len(text)) % 4
    text += b" " * pad
    body = b"".join(blocks)
    return MAGIC + len(text).to_bytes(4, "little") + text + body, header


def unpack_artifact(data: bytes) -> tuple[dict[str, Any], dict[str, dict[str, np.ndarray]]]:
    """The header and each patch's arrays, for a reader (the tests use this; the desktop
    has its own)."""
    if data[:8] != MAGIC:
        raise ValueError("not a STKMSH1 artifact")
    length = int.from_bytes(data[8:12], "little")
    header = json.loads(data[12 : 12 + length].decode("utf-8"))
    body = data[12 + length :]
    out: dict[str, dict[str, np.ndarray]] = {}
    for patch in header["patches"]:
        start = patch["offset"]
        n_points, n_tris, n_edges = patch["points"], patch["triangles"], patch["edges"]
        pbytes = n_points * 3 * 4
        tbytes = n_tris * 3 * 4
        positions = np.frombuffer(body, dtype="<f4", count=n_points * 3, offset=start)
        triangles = np.frombuffer(body, dtype="<u4", count=n_tris * 3, offset=start + pbytes)
        edges = np.frombuffer(body, dtype="<u4", count=n_edges * 2, offset=start + pbytes + tbytes)
        out[patch["name"]] = {
            "points": positions.reshape(-1, 3),
            "triangles": triangles.reshape(-1, 3),
            "edges": edges.reshape(-1, 2),
        }
    return header, out


# ---------------------------------------------------------------------- the point fields (T-215)


class AgentMismatch(StokesError):
    """The field artifact would not line up with the surfaces artifact.

    Code `agent.mismatch`, named in the brief. **This is the one refusal the task exists for**: the
    desktop tints the vertices it draws from the surfaces artifact, so a patch list in a different
    order — or with different point counts — would colour the wrong vertices and look like a result.
    """

    code = "agent.mismatch"


class AgentBadParams(StokesError):
    """A caller asked for something that cannot be measured or extracted.

    **Defined here rather than imported from `server`**, which would be circular:
    `server` imports this module. The code matches the one `server.AgentBadParams` carries, so a
    client sees one error family whichever side refused.
    """

    code = "agent.bad_params"


class AgentTooLarge(StokesError):
    """The artifact would be past the 2 GiB ceiling.

    Code `agent.too_large`, the same one `write_artifact` refuses with; raised **before** the buffer
    is built, so an empty patch set with an absurd plane does not allocate 2 GiB to find out.
    """

    code = "agent.too_large"


def plane_offset_overflows(
    patches: list[dict[str, Any]], slice_: dict[str, Any] | None
) -> str | None:
    """A description of the array that would not fit, or None.

    Offsets are measured from the start of the data section, not of the file, so the fixed 12-byte
    header does not enter them. Every count comes from an array's **shape** before any block is
    packed, because measuring an over-large artifact is not a way to discover it is over-large: the
    caller may be holding a zero-stride view that would cost 2 GiB to materialise.

    The patch points are counted too, not only the field arrays: they are what the offsets are
    measured between, so a patch with more points than a float32 buffer can hold is over the limit
    even before its fields are considered.
    """
    limit = MAX_ARTIFACT_BYTES // 4
    total = 0
    for patch in patches:
        points = int(np.asarray(patch["points"]).reshape(-1, 3).shape[0])
        total += points * 3
        for name, array in patch.get("fields", {}).items():
            total += points * _component_count(name, array, points)
        if total > limit:
            return (
                f"the patch {patch['name']!r} takes the data section to {total * 4 / 2**30:.1f} GiB"
            )
    if slice_ is not None:
        points = int(np.asarray(slice_["positions"]).reshape(-1, 3).shape[0])
        triangles = len(slice_["triangles"])
        edges = len(slice_["edges"])
        geometry = points * 3 * 4 + triangles * 3 * 4 + edges * 2 * 4
        if geometry > MAX_ARTIFACT_BYTES:
            return f"the slice geometry would be {geometry / 2**30:.1f} GiB"
        total += points * 3 + triangles * 3 + edges * 2
        for name, array in slice_.get("fields", {}).items():
            total += points * _component_count(name, array, points)
        if total > limit:
            return f"the slice field {name!r} takes the data section to {total * 4 / 2**30:.1f} GiB"
    return None


def _component_count(name: str, array: Any, points: int) -> int:
    """How many components a field has: the table for a known name, the array's own size otherwise.

    **The size is divided by the point count rather than the array reshaped.** A field array with
    the wrong number of values must reach `_as_rows` and be refused with a message naming the point
    count — that is the failure this whole task turns on — and a reshape here would raise numpy's
    own "cannot reshape array" first, hiding which patch was wrong.
    """
    if name in COMPONENTS:
        return COMPONENTS[name]
    size = int(np.asarray(array).size)
    if points <= 0 or size % points:
        return 0
    return max(size // points, 1)


def _components_of(name: str, array: Any) -> int:
    """A packed field's components: the table for a known name, else the array's own columns.

    **The table wins for a known name**, so a `U` that arrives with the wrong shape is still refused
    by `_as_rows`; a discovered field (an imported case's `k`, `alpha.water`) has only its shape.
    """
    if name in COMPONENTS:
        return COMPONENTS[name]
    block = np.asarray(array)
    return 1 if block.ndim == 1 else int(block.shape[1])


def _as_rows(array: Any, points: int, components: int, what: str) -> np.ndarray:
    """`array` as a contiguous `(points, components)` `float32` block, or a refusal.

    **The row count is asserted, not reshaped into.** `np.reshape` would happily turn three values
    per point into three points, which is the cell/point mix-up this whole task turns on; a refusal
    names the array and both counts instead.

    A 1-component array may arrive flat — `np.linalg.norm(U, axis=1)` gives `(points,)`, and a
    caller passing one is not making a mistake — so a flat array of the right length is taken as
    one component per point and anything else is refused.
    """
    block = np.ascontiguousarray(array, dtype="<f4")
    if components == 1 and block.ndim == 1:
        block = block.reshape(-1, 1)
    if block.ndim != 2 or block.shape != (points, components):
        raise ValueError(
            f"{what} is {block.shape}, not ({points}, {components}) as its {points} points and "
            f"{components} components require"
        )
    return block


def _field_entry(block: np.ndarray, offset: int, components: int, measured: Any) -> dict[str, Any]:
    """One field's header entry: where it is, how it is shaped, and what is in it.

    `measured` is what `min`/`max` are taken over. For `U` that is the magnitude rather than each
    component separately (the brief's rule), and for everything else it *is* the stored array — so
    the caller passes `block` for those and the two cannot drift apart.
    """
    values = np.asarray(measured, dtype="float64")
    high = float(values.max()) if values.size else 0.0
    low = float(values.min()) if values.size else 0.0
    if not math.isfinite(high) or not math.isfinite(low) or abs(high) > _F32_MAX:
        raise ValueError(
            f"a field holds {high!r}, which float32 cannot represent; "
            "refusing rather than writing an infinity the client would plot"
        )
    entry: dict[str, Any] = {
        "offset": offset,
        "components": components,
        "min": low,
        "max": high,
    }
    # **The 1st and 99th percentiles, for the legend's auto range** (RESULTS-OVERHAUL §3.7): a
    # stagnation point or one bad cell sets `max` and washes the colours out of everything else;
    # the old viewer's "auto 1-99 %" is what made its first picture readable. Measured here, over
    # the same values as min/max, so the desktop never opens the case to find them.
    if values.size >= 10:
        p01, p99 = (float(v) for v in np.percentile(values, [1.0, 99.0]))
        if math.isfinite(p01) and math.isfinite(p99) and p01 <= p99:
            entry["p01"], entry["p99"] = p01, p99
    return entry


def _volume_table(volume: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
    """The header's `volume` table; a non-finite or inverted range is dropped, not written."""
    table: dict[str, dict[str, float]] = {}
    for name, bounds in volume.items():
        low, high = float(bounds[0]), float(bounds[1])
        if math.isfinite(low) and math.isfinite(high) and low <= high:
            table[str(name)] = {"min": low, "max": high}
    return table


def pack_fields(
    patches: list[dict[str, Any]],
    slice_: dict[str, Any] | None,
    time: float,
    units: dict[str, str] | None = None,
    volume: dict[str, tuple[float, float]] | None = None,
    times: list[float] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """The field artifact's bytes and header from already-computed arrays. **Pure numpy, no VTK.**

    ``times`` is every time the case has written (the reader's own list), so a client can offer
    the others; None writes no `times` key, which is what a plane or iso cut packs.

    ``units`` is the header's unit table; None means the incompressible `FIELD_UNITS`, and an
    energy job passes `units_for("of14.fluid")` so `p` is labelled Pa and `T` K.

    ``volume`` is each scalar field's `(min, max)` over the whole internal mesh (`volume_ranges`),
    written as the header's `volume` table. It is what the desktop checks an iso value against
    (ADR-0026 amendment 2): the patches and one slice can miss an extreme only the interior reaches.
    None writes no table, which is what a plane or iso cut packs.

    Each patch is `{"name", "points" (N,3), "fields": {name: (N, components)}}`; `slice_` is
    `{"origin", "normal", "positions" (P,3), "triangles" (T,3), "edges" (E,2), "fields"}` or None.
    The caller (`extract_fields`) has already read them, so this half can be tested — and is —
    without a mesh, without OpenFOAM and without VTK.

    **A patch with no fields packs an empty dict.** Not zeros: an `empty` patch has no `U`, and a
    zero there is a claim that the wall is at rest.

    **The order is the caller's**, which `extract_fields` takes from the surfaces artifact; the
    header keeps it and `_check_patch_order` is what proves the two agree.
    """
    overflow = plane_offset_overflows(patches, slice_)
    if overflow is not None:
        raise AgentTooLarge(f"{overflow}, over the {MAX_ARTIFACT_BYTES / 2**30:.0f} GiB limit")

    header: dict[str, Any] = {
        "version": VERSION,
        "time": float(time),
        "units": dict(FIELD_UNITS if units is None else units),
        "patches": [],
    }
    if volume is not None:
        header["volume"] = _volume_table(volume)
    if times is not None:
        header["times"] = [float(value) for value in times]
    blocks: list[bytes] = []
    offset = 0
    for index, patch in enumerate(patches):
        points = np.asarray(patch["points"]).reshape(-1, 3).shape[0]
        entry: dict[str, Any] = {
            "name": str(patch["name"]),
            "index": index,
            "points": points,
            "fields": {},
        }
        for name, array in patch.get("fields", {}).items():
            components = _components_of(name, array)
            block = _as_rows(array, points, components, f"{patch['name']!r}.{name}")
            measured = np.linalg.norm(block.astype("float32"), axis=1) if name == "U" else block
            entry["fields"][name] = _field_entry(block, offset, components, measured)
            blocks.append(block.tobytes())
            offset += block.nbytes
        header["patches"].append(entry)

    header["slice"] = None
    if slice_ is not None:
        positions = np.ascontiguousarray(slice_["positions"], dtype="<f4").reshape(-1, 3)
        triangles = np.ascontiguousarray(slice_["triangles"], dtype="<u4").reshape(-1, 3)
        points = int(positions.shape[0])
        triangles = drop_zero_area(positions, triangles)
        edges = drop_degenerate_edges(
            np.ascontiguousarray(slice_["edges"], dtype="<u4").reshape(-1, 2)
        )
        # **A plane cut names its plane; an iso-surface names its field and value.** Both travel in
        # the same section (the reader needs only the counts and the offset), and the header must
        # say which surface it is: the server's cache checks a file's contents against its name.
        # An iso cut has no plane, so reading `origin` unconditionally raised `KeyError` on every
        # `iso.cut`.
        slice_entry: dict[str, Any] = {}
        if "origin" in slice_ and "normal" in slice_:
            slice_entry["origin"] = [float(v) for v in slice_["origin"]]
            slice_entry["normal"] = [float(v) for v in slice_["normal"]]
        if "field" in slice_ and "value" in slice_:
            slice_entry["iso"] = {"field": str(slice_["field"]), "value": float(slice_["value"])}
        slice_entry.update(
            {
                "points": points,
                "triangles": int(triangles.shape[0]),
                "edges": int(edges.shape[0]),
                "offset": offset,
                "fields": {},
            }
        )
        blocks.append(positions.tobytes() + triangles.tobytes() + edges.tobytes())
        offset += positions.nbytes + triangles.nbytes + edges.nbytes
        for name, array in slice_.get("fields", {}).items():
            components = _components_of(name, array)
            block = _as_rows(array, points, components, f"the slice's {name}")
            measured = np.linalg.norm(block.astype("float32"), axis=1) if name == "U" else block
            slice_entry["fields"][name] = _field_entry(block, offset, components, measured)
            blocks.append(block.tobytes())
            offset += block.nbytes
        header["slice"] = slice_entry

    text = json.dumps(header, separators=(",", ":")).encode("utf-8")
    text += b" " * ((-len(text)) % 4)
    body = b"".join(blocks)
    data = MAGIC_FIELDS + len(text).to_bytes(4, "little") + text + body
    # **The ceiling is enforced before the buffer exists, not after.** The byte count above is the
    # only place the size is known cheaply; a caller that never calls `write_artifact` (the tests do
    # not) must get the same refusal it would.
    if len(data) > MAX_ARTIFACT_BYTES:
        raise AgentTooLarge(
            f"the artifact would be {len(data) / 2**30:.1f} GiB, over the "
            f"{MAX_ARTIFACT_BYTES / 2**30:.0f} GiB limit"
        )
    return data, header


def unpack_fields(
    data: bytes,
) -> tuple[dict[str, Any], dict[str, dict[str, np.ndarray]]]:
    """The header and every array of a `STKFLD1` buffer (the tests use this; T-216 has the
    desktop's own).

    Patch arrays come back keyed by patch name and shaped `(points, components)`, because that is
    how a renderer consumes them. The slice, when there is one, is under the key `__slice__` — the
    sections are separate parts of the buffer and a reader that merged them would have to guess
    which arrays were a patch's. Its geometry is `positions` `(P, 3)`, `triangles` `(T, 3)` and
    `edges` `(E, 2)`.

    Every array is a **view into `data`**, so a caller wanting a mutable copy must take one.

    **It refuses what it cannot read rather than guessing**: a wrong magic, a buffer shorter than
    its header, a header length that is not a multiple of four, a header that runs past the end, a
    JSON error, an unknown version, and any array whose offset or count runs past the data. A
    header is the one thing in this format that can lie about the bytes beside it, and reading
    whatever happened to follow would be the failure this reader exists to prevent.
    """
    if data[:8] != MAGIC_FIELDS:
        raise ValueError("not a STKFLD1 artifact")
    if len(data) < 12:
        raise ValueError("the artifact is shorter than its 12-byte header")
    length = int.from_bytes(data[8:12], "little")
    if length % 4 != 0:
        raise ValueError(f"the header length {length} is not a multiple of four")
    if 12 + length > len(data):
        raise ValueError(f"the header of {length} bytes runs past the end of the artifact")
    header = json.loads(data[12 : 12 + length].decode("utf-8"))
    if header.get("version") != VERSION:
        raise ValueError(f"unknown artifact version {header.get('version')!r}")
    body = data[12 + length :]
    out: dict[str, dict[str, np.ndarray]] = {}
    for patch in header["patches"]:
        out[str(patch["name"])] = _fields_of(body, patch["fields"], int(patch["points"]))
    if header.get("slice") is not None:
        out[SLICE_KEY] = _slice_arrays(body, header["slice"])
    return header, out


def _fields_of(body: bytes, fields: dict[str, Any], points: int) -> dict[str, np.ndarray]:
    """The field arrays of one patch or slice, each shaped and checked against its own count."""
    found: dict[str, np.ndarray] = {}
    for name, field in fields.items():
        components = int(field["components"])
        found[str(name)] = _view(body, "<f4", points * components, int(field["offset"])).reshape(
            points, components
        )
    return found


def _slice_arrays(body: bytes, entry: dict[str, Any]) -> dict[str, np.ndarray]:
    """The slice's geometry and fields, at the one offset the geometry starts from.

    The geometry offset is stated once and the two index arrays follow it at widths the header
    already gives (three `uint32` per triangle, two per edge), which is why only `points`,
    `triangles` and `edges` are needed to find them.
    """
    points = int(entry["points"])
    triangles = int(entry["triangles"])
    edges = int(entry["edges"])
    start = int(entry["offset"])
    found: dict[str, np.ndarray] = {
        "positions": _view(body, "<f4", points * 3, start).reshape(points, 3),
        "triangles": _view(body, "<u4", triangles * 3, start + points * 12).reshape(-1, 3),
        "edges": _view(body, "<u4", edges * 2, start + points * 12 + triangles * 12).reshape(-1, 2),
    }
    found.update(_fields_of(body, entry["fields"], points))
    return found


def _view(body: bytes, dtype: str, count: int, offset: int) -> np.ndarray:
    """A `count`-element view at `offset`, refused when it runs past the end of the data.

    `np.frombuffer` refuses that too, but its message names a buffer while this one names the offset
    and the count — which is what a person debugging a header needs to see.
    """
    width = np.dtype(dtype).itemsize
    if offset < 0 or count < 0 or offset + count * width > len(body):
        raise ValueError(
            f"an array of {count} at offset {offset} is past the end of {len(body)} data bytes"
        )
    return np.frombuffer(body, dtype=dtype, count=count, offset=offset)


def open_case(
    case_dir: Path,
    *,
    patches: bool,
    internal: bool,
    fields: tuple[str, ...] = (),
) -> Any:
    """A configured `vtkOpenFOAMReader` for a case, updated once.

    **Shared with T-215**, which wants the internal mesh and fields; this task wants the patches and
    no cell-to-point. One function, so the two cannot disagree about how a case is opened.

    Method notes, each measured against VTK **9.6.2** on this machine:

    * `SetFileName` takes the **`case.foam` marker**, not the directory. Created when missing.
    * **`EnableAllPatchArrays()`** — there is no `EnableAllPatches()`; it raises `AttributeError`.
    * **`SetRefresh()` takes no argument** — `SetRefresh(True)` raises `TypeError`.
    * `SetCreateCellToPoint(internal)` keeps the output as the mesh's own cells when false; T-215
      turns it on.
    * `SetSkipZeroTime(False)` so a case whose only time is 0 still reads. **`False`, not `0`** —
      mypy caught the integer as a type error, and the C++ signature wants a bool.
    """
    from vtkmodules.vtkIOGeometry import vtkOpenFOAMReader

    from stokes_agent.vtk_quiet import quiet_vtk

    quiet_vtk()
    case_dir = Path(case_dir)
    marker = case_dir / "case.foam"
    # **The marker IS created here — deliberately, and a test asserts it** — but creation without a
    # check was escape shape #3 in a new place, so both checks come first:
    #
    # 1. **the DIRECTORY is not a link out of itself.** `touch()` follows a symlinked `case_dir` the
    #    same way `mkdir` followed a symlinked `artifacts`, which `write_artifact` already guards.
    # 2. **the MARKER is not a link.** `Path.exists()` follows symlinks and is `False` for a
    #    DANGLING one, so `touch()` would create the link's *target* — a write outside the case with
    #    no error at all. Refusing the link is what `_contained` does for a named artifact.
    if case_dir.is_symlink():
        raise ValueError(f"{case_dir} is a symbolic link, not a case directory")
    if marker.is_symlink():
        raise ValueError(f"{marker} is a symbolic link, not a case marker")
    if not marker.exists():
        marker.touch()
    reader = vtkOpenFOAMReader()
    reader.SetFileName(str(marker))
    reader.UpdateInformation()  # the patch names are known only after this
    if patches:
        reader.EnableAllPatchArrays()
    reader.SetCreateCellToPoint(internal)
    reader.SetSkipZeroTime(False)
    if fields:
        # **`SetPointArrayStatus`/`SetCellArrayStatus`, not `SetArrayStatus`.** mypy caught the
        # latter as
        # absent and it is: a field is a point or a cell array, so each has its own method.
        # Measured on
        # VTK 9.6.2.
        #
        # **What this does and does not do, measured rather than assumed:** `vtkOpenFOAMReader`
        # enables
        # every field it finds by default and reports no arrays of its own before a read
        # (`GetNumberOfPointArrays()` is 0), so asking for a name *enables* it and asking for
        # one does
        # **not** disable the rest. A caller that wanted one field and nothing else would have
        # to disable
        # the others explicitly. T-215 should know that before it relies on `fields` as a filter.
        reader.UpdateInformation()
        for name in fields:
            reader.SetPointArrayStatus(name, 1)
            reader.SetCellArrayStatus(name, 1)
    reader.Update()
    return reader


def _patch_types(case_dir: Path) -> dict[str, str]:
    """Each patch's OpenFOAM `type` from `constant/polyMesh/boundary`.

    **Not a `polyMesh` parser.** It scans for an entry's name followed by a `type …;` before the
    next entry, which is what the file's own structure guarantees; anything it cannot read leaves
    the patch as `"patch"`. VTK does not expose the type (measured), and the brief forbids a real
    parser here.
    """
    path = Path(case_dir) / "constant" / "polyMesh" / "boundary"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    # Drop the banner and comments so a `type` in prose cannot be read as an entry's.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    found: dict[str, str] = {}
    # **Any indentation, not exactly four spaces.** An adversarial review re-indented a legal
    # `boundary` file to two spaces and the anchored `^\s{4}` matched nothing, so EVERY patch
    # silently
    # became `patch_type="patch"` — a wrong-but-plausible result with no error anywhere.
    # OpenFOAM does not
    # promise an indent, and real blockMesh output happens to use four.
    for match in re.finditer(r"^[ \t]*(\w+)[ \t]*\n[ \t]*\{([^}]*)\}", text, flags=re.M):
        name, body = match.group(1), match.group(2)
        kind = re.search(r"\btype[ \t]+(\w+)[ \t]*;", body)
        if kind is not None:
            found[name] = kind.group(1)
    return found


def extract_surfaces(case_dir: Path) -> tuple[bytes, dict[str, Any]]:
    """The artifact bytes and header for a case's boundary patches.

    Every patch in `constant/polyMesh/boundary` order, each with its own points, triangles and the
    real polygon edges. A patch VTK did not report is skipped rather than invented.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy

    reader = open_case(case_dir, patches=True, internal=False)
    output = reader.GetOutput()
    boundary = None
    for index in range(output.GetNumberOfBlocks()):
        meta = output.GetMetaData(index)
        name = meta.Get(_name_key()) if meta is not None else None
        if name == "boundary":
            boundary = output.GetBlock(index)
            break
    if boundary is None:
        return pack_artifact([])
    kinds = _patch_types(case_dir)
    patches: list[dict[str, Any]] = []
    for index in range(boundary.GetNumberOfBlocks()):
        block = boundary.GetBlock(index)
        meta = boundary.GetMetaData(index)
        name = str(meta.Get(_name_key())) if meta is not None else f"patch{index}"
        if block is None or block.GetNumberOfPoints() == 0:
            continue
        positions = np.asarray(vtk_to_numpy(block.GetPoints().GetData()), dtype="float32")
        polys = np.asarray(vtk_to_numpy(block.GetPolys().GetConnectivityArray()), dtype="uint32")
        offsets = np.asarray(vtk_to_numpy(block.GetPolys().GetOffsetsArray()), dtype="uint32")
        patches.append(
            {
                "name": name,
                "patch_type": kinds.get(name, "patch"),
                "points": positions,
                "triangles": triangulate_fan(offsets, polys),
                "edges": unique_edges(offsets, polys),
                "faces": int(block.GetNumberOfPolys()),
            }
        )
    return pack_artifact(patches)


# ---------------------------------------------------------------------- fields from the case


def _check_patch_order(surfaces_header: dict[str, Any], found: list[dict[str, Any]]) -> None:
    """Refuse unless the field patches are the surfaces patches, in order, with equal point counts.

    **This is the invariant T-215 exists to prove.** The desktop writes a per-vertex tint onto the
    patches it already drew from the surfaces artifact, so the field arrays must be indexed by the
    same vertices in the same order. A different *count* is the symptom; the same count with a
    different *order* is the same bug and is just as invisible, which is why the names are compared
    position by position rather than looked up.

    The message names the patch and both counts, one message per disagreement, so a caller can say
    which patch is wrong instead of "the artifacts differ". It does **not** fall back: an
    artifact of
    the wrong shape is worse than none, because it renders.
    """
    wanted = list(surfaces_header.get("patches") or [])
    if len(wanted) != len(found):
        raise AgentMismatch(
            f"the surfaces artifact has {len(wanted)} patches and the case has {len(found)}; "
            "the field arrays cannot be matched to the drawn vertices"
        )
    for position, (surface, patch) in enumerate(zip(wanted, found, strict=True)):
        expected = int(surface.get("points", -1))
        actual = int(np.asarray(patch["points"]).reshape(-1, 3).shape[0])
        if surface.get("name") != patch["name"]:
            raise AgentMismatch(
                f"patch {position} is {patch['name']!r} in the case and "
                f"{surface.get('name')!r} in the surfaces artifact; the patch order differs"
            )
        if expected != actual:
            raise AgentMismatch(
                f"patch {patch['name']!r} has {actual} points in the case and {expected} in the "
                "surfaces artifact; the field arrays would tint the wrong vertices"
            )


def all_times(reader: Any) -> list[float]:
    """Every time the reader found, ascending: the reader's own list (see `latest_time`)."""
    values = reader.GetTimeValues()
    return [float(values.GetValue(index)) for index in range(values.GetNumberOfValues())]


def pick_time(reader: Any, wanted: float | None) -> float:
    """The written time nearest ``wanted``, or the latest when None. Raises when there is none.

    **Nearest, not exact**, because a client sends back the float it was given and a time written
    as `0.1` may come back as `0.10000000000000001`; the reader's own value is what is used.
    """
    times = all_times(reader)
    if not times:
        raise ValueError("the case has no time steps; there are no fields to read")
    if wanted is None:
        return times[-1]
    return min(times, key=lambda value: abs(value - float(wanted)))


def latest_time(reader: Any) -> float:
    """The latest time the reader found, as a float.

    **The reader's own time array**, not a scan of the case's folder names: a folder called `1e-3`
    or `latest` would have to be parsed and ordered by hand, and VTK has already done it. The
    brief's rule (the largest numeric folder holding `U` and `p`, with `0` counting when it is all
    there is) is what the reader applies before reporting these values, so the two agree.

    `GetTimeValues()` returns a `vtkDoubleArray`, **not a Python sequence** — measured, `TypeError:
    ... object is not iterable` — so it is indexed. The values are ascending in every case measured
    here; the last is taken because that is what "latest" means for the reader that produced it.
    """
    values = reader.GetTimeValues()
    if values.GetNumberOfValues() == 0:
        raise ValueError("the case has no time steps; there are no fields to read")
    return float(values.GetValue(values.GetNumberOfValues() - 1))


def cut_plane(
    reader: Any, origin: Any, normal: Any, fields: tuple[str, ...] = FIELDS
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """One plane through the internal mesh: positions, triangles, edges and point fields.

    **`GenerateTrianglesOff()`**, so each cut cell gives one polygon and `triangulate_fan` makes the
    fan the patches get — a `vtkTriangleFilter` would add diagonals that are not mesh edges, and the
    "surface with cells" view would draw them (the same reason `triangulate_fan` exists at all).
    Measured on VTK 9.6.2: one hex cut through its centre gives one quad, `4(0 1 3 2)`.

    **The point arrays come from the cutter**, which interpolates the cell-to-point values it was
    given onto the cut points. Measured rather than assumed: with the reader's cell-to-point on, a
    one-hex case carrying `U (1 0 0)` and `p 5` gives `[(1, 0, 0)] * 4` and `[5, 5, 5, 5]` on the
    four cut points.

    The plane is a `vtkPlane` at `origin` with `normal`, both used as given; choosing the default is
    `extract_fields`' job, because it needs the mesh's bounds.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkPlane
    from vtkmodules.vtkFiltersCore import vtkCutter

    mesh = _internal_mesh(reader)
    if mesh is None:
        empty = np.zeros((0, 3), dtype="float32")
        return empty, np.zeros((0, 3), dtype="uint32"), np.zeros((0, 2), dtype="uint32"), {}

    plane = vtkPlane()
    plane.SetOrigin(float(origin[0]), float(origin[1]), float(origin[2]))
    plane.SetNormal(float(normal[0]), float(normal[1]), float(normal[2]))
    cutter = vtkCutter()
    cutter.SetInputData(mesh)
    cutter.SetCutFunction(plane)
    cutter.GenerateTrianglesOff()
    cutter.Update()
    output = cutter.GetOutput()

    positions = np.asarray(vtk_to_numpy(output.GetPoints().GetData()), dtype="float32").reshape(
        -1, 3
    )
    polys = np.asarray(vtk_to_numpy(output.GetPolys().GetConnectivityArray()), dtype="uint32")
    offsets = np.asarray(vtk_to_numpy(output.GetPolys().GetOffsetsArray()), dtype="uint32")
    triangles = triangulate_fan(offsets, polys)
    edges = unique_edges(offsets, polys)
    triangles = drop_zero_area(positions, triangles)
    edges = drop_degenerate_edges(edges)
    found: dict[str, np.ndarray] = {}
    point_data = output.GetPointData()
    for name in _names(point_data, fields):
        array = point_data.GetArray(name)
        if array is None:
            continue
        components = int(array.GetNumberOfComponents())
        block = np.asarray(vtk_to_numpy(array), dtype="float32")
        found[name] = block.reshape(-1, components)
    return positions.astype("float32"), triangles, edges, found


def drop_zero_area(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """The triangles of non-zero area, in place order; the rest are dropped.

    **A `vtkCutter` plane that lies on a face plane returns the face polygons from both neighbouring
    cells**, and the slivers and duplicates among them are what correction 1 of the brief is about.
    They carry no information, would be drawn as flickering geometry, and make a "unique triangles"
    assertion true only by accident, so they are dropped rather than stored.

    A triangle whose three points are collinear has zero area and goes by the same test, which is
    what it is: a line, not a face.
    """
    if triangles.shape[0] == 0:
        return triangles
    corners = positions[triangles.astype("int64")]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return triangles[np.linalg.norm(cross, axis=1) > 0.0]


def drop_degenerate_edges(edges: np.ndarray) -> np.ndarray:
    """The edges whose two ends are different points.

    A polygon that repeats a corner gives a self-loop, which is not an edge anything can draw and
    which a viewer would render as a stray tick.
    """
    if edges.shape[0] == 0:
        return edges
    # Annotated rather than returned directly: numpy's indexing types as `Any` here, and mypy
    # strict refuses an `Any` return from a typed function.
    kept: np.ndarray = edges[edges[:, 0] != edges[:, 1]]
    return kept


def duplicate_triangles(triangles: np.ndarray) -> int:
    """How many index triples the slice repeats, ignoring winding.

    **Not a deduplication, a measurement**, and it lives in the module rather than only in the
    engine
    test because correction 1 of the brief names the duplication as the thing the default plane
    exists to avoid: a cut through a face plane gives the same polygon from both neighbouring cells.
    The *sorted* triple is the identity, so `(1, 2, 3)` and `(3, 2, 1)` are one triangle.
    """
    if triangles.shape[0] == 0:
        return 0
    unique = np.unique(np.sort(triangles.astype("int64"), axis=1), axis=0)
    return int(triangles.shape[0] - unique.shape[0])


def _internal_mesh(reader: Any) -> Any:
    """The reader's `internalMesh` block, or None when it reported none.

    Found by NAME rather than by position, exactly as `extract_surfaces` finds `boundary`: the
    composite dataset's order is the reader's business, and a block index is not a contract. The
    name key is `vtkCompositeDataSet.NAME()` — the key OBJECT, because `vtkInformation.Get` refuses
    a `str` (measured; `_name_key` has the details).
    """
    from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet

    output = reader.GetOutput()
    for index in range(output.GetNumberOfBlocks()):
        meta = output.GetMetaData(index)
        name = meta.Get(vtkCompositeDataSet.NAME()) if meta is not None else None
        if name == "internalMesh":
            return output.GetBlock(index)
    return None


def _patch_point_fields(block: Any, fields: tuple[str, ...] = FIELDS) -> dict[str, np.ndarray]:
    """One boundary patch's point arrays, by name, as `(points, components)` float32.

    **A field the patch does not have is absent, not zero.** An `empty` patch has no `U`; writing
    `(0, 0, 0)` there would say the wall is at rest. VTK reports such an array as present with all
    the volume's values or as absent depending on the case, so the presence test is the array
    itself — and `_check_patch_order` is what catches the case where the *count* is wrong, which is
    the failure that would otherwise pass silently.
    """
    found: dict[str, np.ndarray] = {}
    from vtkmodules.util.numpy_support import vtk_to_numpy

    point_data = block.GetPointData()
    for name in _names(point_data, fields):
        array = point_data.GetArray(name)
        if array is None:
            continue
        components = int(array.GetNumberOfComponents())
        if components != COMPONENTS.get(name, components) or components not in (1, 3):
            continue
        values = np.asarray(vtk_to_numpy(array), dtype="float32").reshape(-1, components)
        if values.shape[0] != int(block.GetNumberOfPoints()):
            continue
        found[name] = values
    with_magnitudes(found)
    return found


def _order_fields(
    found: dict[str, np.ndarray], fields: tuple[str, ...] = FIELDS
) -> dict[str, np.ndarray]:
    """The arrays in the header's order: `U`, `U_mag`, `p` (then `T`), then anything else by name.

    `fields` gives the order, and `U_mag` sits after `U` because it is derived from it;
    `_fields_of` reads the header back in this order, so a client can rely on it.
    """
    ordered: dict[str, np.ndarray] = {}
    for name in fields:
        if name in found:
            ordered[name] = found[name]
        magnitude = MAGNITUDES.get(name)
        if magnitude is not None and magnitude in found:
            ordered[magnitude] = found[magnitude]
    for name, values in found.items():
        if name not in ordered:
            ordered[name] = values
    return ordered


def _mesh_bounds(reader: Any) -> tuple[list[float], list[float]]:
    """The internal mesh's bounds as two triples, refused when the mesh has none."""
    mesh = _internal_mesh(reader)
    if mesh is None or mesh.GetNumberOfPoints() == 0:
        raise ValueError("the case has no internal mesh, so there is no plane to cut")
    bounds = mesh.GetBounds()  # xmin, xmax, ymin, ymax, zmin, zmax
    low = [float(bounds[0]), float(bounds[2]), float(bounds[4])]
    high = [float(bounds[1]), float(bounds[3]), float(bounds[5])]
    return low, high


def default_plane(reader: Any) -> dict[str, Any]:
    """The plane used when the caller gives none: the bbox centre, moved off the faces.

    **The offset is the whole point of this function.** The centre of a block-meshed block is
    exactly a face plane of its own grid — the duct's is z = 0.010 m on a 2 mm grid — and a
    `vtkCutter` plane through a face's vertices takes zero-area or duplicated polygons from both
    neighbouring cells. So the origin moves along the normal by `SLICE_OFFSET_FRACTION` of the bbox
    **diagonal**, a step that is the same fraction of a cell at any mesh size and far smaller than
    one cell.

    The offset is written into the header's `origin`, so a client that wants to say which plane was
    cut reads it there rather than re-deriving the rule.
    """
    low, high = _mesh_bounds(reader)
    centre = [(lo + hi) / 2.0 for lo, hi in zip(low, high, strict=True)]
    # **The normal is the mesh's SHORTEST axis**, so the cut holds the two longest: a pipe along x
    # is cut lengthwise (the developing profile), not across (a disc), which a fixed z-normal gave
    # for every pipe that did not happen to run along x or y; a one-cell-thick 2-D case is cut in
    # its own plane. Ties go to z, the old answer, so the duct and the cavity keep their planes.
    extents = [hi - lo for lo, hi in zip(low, high, strict=True)]
    axis = min(range(3), key=lambda i: (extents[i], -i))
    normal = [1.0 if i == axis else 0.0 for i in range(3)]
    diagonal = math.dist(low, high)
    origin = list(centre)
    origin[axis] += SLICE_OFFSET_FRACTION * diagonal
    return {"origin": origin, "normal": normal}


def extract_fields(
    case_dir: Path,
    surfaces_header: dict[str, Any],
    slice_plane: dict[str, Any] | None,
    engine: str | None = None,
    time: float | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """The `STKFLD1` artifact for a case: one value per patch vertex, plus one cut plane.

    ``time`` picks the written time (`pick_time`: the nearest, or the latest when None); the
    header's `times` lists every one the case has.

    ``engine`` picks the fields and units (`fields_for`, `units_for`): None or the incompressible
    engine reads `U` and kinematic `p`; `of14.fluid` reads `U`, absolute `p` in Pa and `T` in K.

    `surfaces_header` is the surfaces artifact's header for the same mesh. It is **the authority on
    patch order and point counts** — the fields are not allowed to disagree, and
    `_check_patch_order`
    is what says so.

    Five things worth knowing, each measured rather than assumed:

    * **Cell-to-point is on.** The patches' point arrays then hold the patch's own face values
      interpolated to its vertices, which is what the walls-are-at-rest and inlet-speed checks need.
      Without it the arrays are the per-cell values and there are not enough of them.
    * **`CreateCellToPointOn()`** is the spelling that exists on the reader, not
      `SetCreateCellToPointOn` — the setter is `SetCreateCellToPoint(bool)`.
    * **The latest time**, from the reader's own `GetTimeValues()` (see `latest_time`).
    * **Point array status does NOT filter.** `open_case`'s own note applies: the reader enables
      every field it finds, so `fields=FIELDS` *asks for* `U` and `p` and does not disable anything
      else. This function therefore takes `U` and `p` from the arrays and ignores the rest rather
      than trusting `fields` to have hidden them.
    * **The slice is cut from the internal mesh only** (`patches=False`), because the volume's
      values are what a plane through the interior should carry. A second reader is created for it:
      the patch reader has `internal=False`, and one reader cannot be both.

    A slice with no polygons is written with `points: 0` rather than refused: a plane outside the
    mesh is a legitimate question with the answer "nothing there".
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet

    fields = fields_for(engine)
    reader = open_case(case_dir, patches=True, internal=False, fields=fields)
    # **`UpdateTimeStep`, not `Update`, and the time is read BEFORE the update.**
    #
    # `Update()` reads the reader's CURRENT time, which is 0 — and at time 0 the fields are the
    # initial condition, so every patch came back all zeros. Measured on the solved duct: with
    # `Update()` the patches carry `U_mag = 0` and `p = 0`; with
    # `UpdateTimeStep(latest_time(reader))`
    # they carry `inlet U_mag = 0.078669` (the inlet speed), `walls = 0` (no-slip) and `outlet =
    # 0.157644` (the developed peak), and `p` from `0.0489` at the inlet to `0` at the outlet.
    #
    # **Cell-to-point must be on for the patch point arrays to exist at all** (measured: with
    # it off the
    # boundary blocks report `arrays=[]`), and the setting only takes effect at the next
    # update — which
    # is why the update below follows it.
    reader.CreateCellToPointOn()
    times = all_times(reader)
    time = pick_time(reader, time)
    reader.UpdateTimeStep(time)
    output = reader.GetOutput()
    boundary = None
    for index in range(output.GetNumberOfBlocks()):
        meta = output.GetMetaData(index)
        name = meta.Get(vtkCompositeDataSet.NAME()) if meta is not None else None
        if name == "boundary":
            boundary = output.GetBlock(index)
            break
    if boundary is None:
        raise AgentMismatch(
            "the case has no boundary block, so its patches cannot be matched to the surfaces "
            "artifact's"
        )

    patches: list[dict[str, Any]] = []
    for index in range(boundary.GetNumberOfBlocks()):
        block = boundary.GetBlock(index)
        meta = boundary.GetMetaData(index)
        name = str(meta.Get(_name_key())) if meta is not None else f"patch{index}"
        if block is None or block.GetNumberOfPoints() == 0:
            continue
        positions = np.asarray(vtk_to_numpy(block.GetPoints().GetData()), dtype="float32")
        if positions.size % 3:
            raise AgentMismatch(
                f"patch {name!r} reports {positions.size} coordinates, which is not three per point"
            )
        patches.append(
            {
                "name": name,
                "points": positions.reshape(-1, 3),
                "fields": _order_fields(_patch_point_fields(block, fields), fields),
            }
        )

    _check_patch_order(surfaces_header, patches)

    # **`slice_plane is None` means the default plane, not "no slice".** The brief says so: the
    # caller that wants no plane at all passes `{"slice": false}` at the method level (T-216's
    # business), and this function always cuts one. A caller that wanted the plane omitted would
    # otherwise silently get fields with no slice section and no way to tell that from a failure.
    plane = slice_plane
    if plane is not None and "origin" in plane and "normal" in plane:
        origin = [float(v) for v in plane["origin"]]
        normal = [float(v) for v in plane["normal"]]
    else:
        chosen = default_plane(open_case(case_dir, patches=False, internal=True, fields=fields))
        origin, normal = chosen["origin"], chosen["normal"]
    slice_ = cut_slice(case_dir, origin, normal, time, engine=engine)
    # **The volume's range rides on the slice's reader**, which has already read the internal mesh
    # at this time: no second read, and the desktop never opens the case (ADR-0026 amendment 2).
    volume = slice_.pop("volume", None)
    units = units_for(engine)
    if engine == EXTERNAL_ENGINE:
        names = {name for patch in patches for name in patch["fields"]}
        names |= set(slice_.get("fields") or {})
        units = case_units(case_dir, time, sorted(names))
    return pack_fields(patches, slice_, time, units, volume=volume, times=times)


def cut_slice(
    case_dir: Path,
    origin: list[float],
    normal: list[float],
    time: float | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    """One plane through a solved case: positions, triangles, edges and point fields.

    **The slice half of `extract_fields`, split out so a plane can be cut WITHOUT the patches**
    (the slices contract). The two jobs share only the readers and the cutter: the patch half needs
    the surfaces header for patch order and reads the boundary, while this half needs the internal
    mesh and nothing else. Cutting a plane used to mean re-extracting every patch's points as well,
    which is the expensive part and has nothing to do with the plane.

    `time` is the reader's latest when None, which is what a caller with no opinion wants;
    passing it keeps a second read from picking a different time if the case is still being
    written.
    """
    fields = fields_for(engine)
    slice_reader = open_case(case_dir, patches=False, internal=True, fields=fields)
    # **`Update()` again here too**, for the same reason as the patch reader: the cell-to-point
    # setting only takes effect at the next update, and a cutter fed per-cell arrays would
    # interpolate the wrong values onto the cut points.
    slice_reader.CreateCellToPointOn()
    slice_reader.UpdateTimeStep(latest_time(slice_reader) if time is None else time)
    slice_reader.Update()
    positions, triangles, edges, found = cut_plane(slice_reader, origin, normal, fields)
    # **An empty cut carries no fields.** A plane outside the mesh gives VTK an empty polydata whose
    # point arrays are zero-length; storing an entry per field with `min: 0, max: 0` would tell a
    # client the plane holds a value of zero everywhere, which is a different answer from "the plane
    # is not there". The brief's rule for a patch with no array applies unchanged: no array, no
    # entry.
    if positions.shape[0] == 0:
        found = {}
    slice_: dict[str, Any] = {
        "origin": origin,
        "normal": normal,
        "positions": positions,
        "triangles": triangles,
        "edges": edges,
        "fields": _order_fields(
            {
                name: values
                for name, values in found.items()
                if values.shape[0] == positions.shape[0]
            },
            fields,
        ),
    }
    with_magnitudes(slice_["fields"])
    # **The time travels with the cut.** A caller that packed a slice with no time would write
    # `time: 0.0`, which is a claim about which instant the contours belong to - and `0` is the
    # initial condition, so it would be a believable wrong answer.
    slice_["time"] = float(latest_time(slice_reader) if time is None else time)
    # The whole volume's scalar ranges, from the mesh this reader already holds; `extract_fields`
    # packs them as the header's `volume` table and a plane cut's packer ignores the key.
    mesh = _internal_mesh(slice_reader)
    if mesh is not None:
        slice_["volume"] = volume_ranges(mesh, fields)
    return slice_


def volume_ranges(mesh: Any, fields: tuple[str, ...] = FIELDS) -> dict[str, tuple[float, float]]:
    """Each single-component field's `(min, max)` over a dataset's cells **and** points.

    **Why both**: the cell values are the solution, and the point values (cell-to-point, with the
    boundary's own values on boundary points) are what `vtkContourFilter` contours. A value inside
    the union is one the field takes somewhere in the volume; one outside it gives an empty surface.
    The patches and a single slice, which is all the field artifact otherwise measures, can miss an
    extreme that only the interior reaches (ADR-0026 amendment 2).

    A vector field is skipped (an iso value needs one component), and so is an array with a
    non-finite value: a range of `nan` would refuse every value or none.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy

    found: dict[str, tuple[float, float]] = {}
    for data in (mesh.GetCellData(), mesh.GetPointData()):
        for name in _names(data, fields):
            array = data.GetArray(name)
            if array is None or int(array.GetNumberOfComponents()) != 1:
                continue
            values = np.asarray(vtk_to_numpy(array), dtype="float64")
            if values.size == 0:
                continue
            low, high = float(values.min()), float(values.max())
            if not (math.isfinite(low) and math.isfinite(high)):
                continue
            if name in found:
                low, high = min(found[name][0], low), max(found[name][1], high)
            found[name] = (low, high)
    return found


def cut_iso(
    case_dir: Path,
    field: str,
    value: float,
    time: float | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    """The surface where one scalar point field equals `value`.

    **The iso-surface half of the Results surfaces (ADR-0026 decision 3).** It reads the same
    internal mesh as `cut_slice`, with cell-to-point on, so the contour's values are the ones the
    slice would interpolate. A value outside the field's range gives an empty surface, and an empty
    surface carries no fields, the same rule `cut_slice` applies to an empty cut.

    `field` must be a single-component point field the reader produced (`p`, `T`). A vector has no
    single value to contour, so the caller refuses it before this runs.
    """
    fields = fields_for(engine)
    reader = open_case(case_dir, patches=False, internal=True, fields=fields)
    reader.CreateCellToPointOn()
    reader.UpdateTimeStep(latest_time(reader) if time is None else time)
    reader.Update()
    time_used = float(latest_time(reader) if time is None else time)
    mesh = _internal_mesh(reader)
    if mesh is None:
        return _empty_iso(field, value, time_used)
    return contour_surface(mesh, field, value, time_used, fields)


def contour_surface(
    mesh: Any, field: str, value: float, time: float, fields: tuple[str, ...] = FIELDS
) -> dict[str, Any]:
    """The contour of one point field of a VTK dataset at `value`, as the cut's arrays.

    **Split from `cut_iso` so the contour is tested on a dataset VTK builds in memory**: a hand
    written OpenFOAM case has one cell, whose point values are all the same, so it cannot show a
    surface. The reader supplies the mesh; this function owns the arrays it returns.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkDataObject
    from vtkmodules.vtkFiltersCore import vtkContourFilter

    if mesh.GetPointData().GetArray(field) is None:
        if field == "U_mag" and mesh.GetPointData().GetArray("U") is not None:
            # **Speed is derived here, as the fields extraction derives it**: the case has no
            # `U_mag` array, and an iso-surface of speed is the figure most people want of a flow
            # (the Looks card's Iso was disabled for velocity until this, review §5a). The point
            # `U` is the reader's cell-to-point value, so the contour sits where the slice's speed
            # would interpolate to the same number.
            from vtkmodules.util.numpy_support import numpy_to_vtk

            speed = np.linalg.norm(vtk_to_numpy(mesh.GetPointData().GetArray("U")), axis=1)
            array = numpy_to_vtk(np.ascontiguousarray(speed, dtype="float32"), deep=1)
            array.SetName("U_mag")
            mesh.GetPointData().AddArray(array)
        else:
            raise ValueError(f"the case has no point field {field!r} to contour")

    contour = vtkContourFilter()
    contour.SetInputData(mesh)
    contour.SetInputArrayToProcess(0, 0, 0, vtkDataObject.FIELD_ASSOCIATION_POINTS, field)
    contour.SetValue(0, float(value))
    # **Scalars on, so the contoured field itself travels with the surface** - with them off, VTK
    # drops the field from the output and a client could not colour the surface by the value it
    # was cut at. The value it carries is the iso value, which is the point of the surface.
    contour.ComputeScalarsOn()
    contour.ComputeNormalsOff()
    contour.Update()
    output = contour.GetOutput()
    if output.GetNumberOfPoints() == 0:
        return _empty_iso(field, value, time)

    positions = np.asarray(vtk_to_numpy(output.GetPoints().GetData()), dtype="float32").reshape(
        -1, 3
    )
    polys = np.asarray(vtk_to_numpy(output.GetPolys().GetConnectivityArray()), dtype="uint32")
    offsets = np.asarray(vtk_to_numpy(output.GetPolys().GetOffsetsArray()), dtype="uint32")
    triangles = drop_zero_area(positions, triangulate_fan(offsets, polys))
    edges = drop_degenerate_edges(unique_edges(offsets, polys))
    found: dict[str, np.ndarray] = {}
    point_data = output.GetPointData()
    for name in _names(point_data, fields):
        array = point_data.GetArray(name)
        if array is None:
            continue
        components = int(array.GetNumberOfComponents())
        found[name] = np.asarray(vtk_to_numpy(array), dtype="float32").reshape(-1, components)
    # The speed on the surface, as the fields extraction derives it from `U` (`_collect`): the
    # surface of constant speed must carry the value it was cut at, like `p` and `T` do.
    with_magnitudes(found, (-1, 1))
    return {
        "field": field,
        "value": float(value),
        "positions": positions,
        "triangles": triangles,
        "edges": edges,
        "fields": _order_fields(found, fields),
        "time": time,
    }


def _empty_iso(field: str, value: float, time: float) -> dict[str, Any]:
    """An iso-surface with no points: the answer for a value the field never takes."""
    return {
        "field": field,
        "value": float(value),
        "positions": np.zeros((0, 3), dtype="float32"),
        "triangles": np.zeros((0, 3), dtype="uint32"),
        "edges": np.zeros((0, 2), dtype="uint32"),
        "fields": {},
        "time": time,
    }


def _name_key() -> Any:
    """The metadata key a composite dataset uses for a block's name.

    `vtkCompositeDataSet.NAME()` — **not** `vtkCompositeDataPipeline.NAME()`, which does not exist
    (`AttributeError`, measured).

    **The key OBJECT, not its string.** `vtkInformation.Get` is overloaded on the key's type and
    raises
    `TypeError: arguments do not match any overloaded methods` for a `str`, so `str(NAME())` —
    which is
    what a name-printing probe suggests — is exactly wrong here.
    """
    from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet

    return vtkCompositeDataSet.NAME()


# ---------------------------------------------------------------------- the job folder


def _contained(job_dir: Path, name: str) -> Path:
    """The path of an artifact, refused unless it really is inside `<job_dir>/artifacts`.

    **The five shapes of escape found in this one file, all closed here.**

    1. *The folder itself is a link.* When `<job>/artifacts` points outside the job, a check that
       resolves **both** sides agrees with itself — `folder.resolve()` follows the link, so the
       prefix test passes and the write lands outside the job entirely. Two earlier versions of this
       function had that bug: one compared a resolved target against an unresolved folder, the next
       compared a target against its own parent's resolution, which is trivially equal. **The
       comparison that means something is between the resolved job and the resolved artifacts
       folder.**
    2. *A file inside a legitimate folder is a link.* A legal name pointing at `~/.ssh/id_rsa` reads
       whatever it points at, with the folder check passing. An artifact the extractor wrote is a
       regular file, so a link is refused outright.
    3. *The folder used for the write was not the folder that was checked* — `artifacts_dir` ran
       first and returned an unresolved path. Fixed in `write_artifact`.
    4. *`index.json` was written with no check* — the one name that is not a client's, and therefore
       the one a symlink can be planted at by name.
    5. *A refusal swallowed as a cache miss* — `cached_artifact` caught the `ValueError` this
       function raises. No longer; the containment calls are unfenced there.

    **The rule that ends the series: every path this module touches goes through this function.**
    Five special cases were five chances to be wrong; one entry point is one place to be right.
    """
    if not NAME_RE.match(name):
        raise ValueError(f"{name!r} is not a valid artifact name")
    # **Compare the resolved JOB against the resolved ARTIFACTS folder.** Comparing a target against
    # its own parent's resolution — which two earlier versions of this check did — is trivially
    # true, because both sides follow the same symlink. The question that means something is whether
    # the artifacts folder really lives under the job.
    root = Path(job_dir).resolve()
    folder = root / "artifacts"
    real = folder.resolve()
    if real != folder and not str(real).startswith(str(root) + os.sep):
        raise ValueError(f"the artifacts folder of {job_dir} is not inside the job")
    target = folder / name
    # **And the FILE must not be a link either.** A link inside a legitimate folder, from a legal
    # name to anywhere else, reads or overwrites whatever it points at — the same leak one level
    # down. `is_symlink` is checked rather than the resolution, because an artifact the extractor
    # wrote is a regular file and nothing legitimate makes it a link.
    if target.is_symlink():
        raise ValueError(f"{name!r} is a symbolic link, not an artifact")
    return target


def artifacts_dir(job_dir: Path) -> Path:
    """`<job_dir>/artifacts`, created when missing.

    **This CREATES the folder, so it must never run before a containment check.** `write_artifact`
    used to call it first and then use the path it returned; because it does
    `mkdir(parents=True, exist_ok=True)` it follows a symlinked `artifacts` and hands back the
    *unresolved* path, while `_contained` validates a *resolved* one. The check passed and did not
    govern what was written.

    Reading the index is what this is for now. Anything that writes must go through `_contained`.
    """
    folder = Path(job_dir) / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_artifact(job_dir: Path, name: str, data: bytes) -> dict[str, Any]:
    """Write an artifact atomically and record it in `artifacts/index.json`.

    **Atomic against a READER**: the desktop fetches chunks while the agent may be writing, and
    a reader
    that caught a half-written file would get a sha256 that never matches. The rename is atomic
    on the same
    filesystem, and a test races a reader against a burst of writes.

    **Not atomic against a second WRITER of the same name.** The temp path is deterministic
    (`.<name>.tmp`), so two concurrent writers race on it and one gets `FileNotFoundError` — a
    reviewer
    reproduced 312 of them in a three-second loop. The agent writes one artifact per job from one
    `extract.surfaces` call, so it is not a case this module creates; a per-writer temp name
    (`tempfile.mkstemp`) would fix it if a second writer ever appears.
    """
    # **`_contained` FIRST, and the folder comes from it.** `artifacts_dir` does
    # `mkdir(parents=True, exist_ok=True)`, so calling it before the check creates or follows a
    # symlinked `artifacts` folder and hands back the *unresolved* path — while `_contained` returns
    # the resolved one. The two then disagree, and the write goes to the unchecked path: the check
    # passes and does not govern what is written. Measured by reading the two lines next to each
    # other; the ordering is the bug, not the check.
    target = _contained(job_dir, name)
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError(
            f"the artifact would be {len(data) / 2**30:.1f} GiB, over the "
            f"{MAX_ARTIFACT_BYTES / 2**30:.0f} GiB limit"
        )
    temp = folder / f".{name}.tmp"
    temp.write_bytes(data)
    temp.replace(target)
    digest = hashlib.sha256(data).hexdigest()
    # **The index goes through the SAME check as the artifact.** It is the one file whose name is
    # hardcoded, so it is the one file a symlink can be planted at by name — and the first version
    # built this path by hand and wrote it unchecked, so a symlinked `index.json` wrote outside the
    # job while the artifact beside it was correctly refused. Fifth shape of the same escape; the
    # rule is now that every path this module writes comes from `_contained`.
    index_path = _contained(job_dir, "index.json")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A missing index and a corrupt one are the same thing here: nothing is known yet.
        index = {}
    index[name] = {"size": len(data), "sha256": digest}
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    return {"name": name, "size": len(data), "sha256": digest}


def read_chunk(job_dir: Path, name: str, offset: int, length: int) -> dict[str, Any]:
    """A slice of an artifact, as bytes, with the whole file's size and digest.

    The digest is of the **whole file**, so a client can verify what it assembled from the
    chunks rather than each chunk separately.

    **Cost is the chunk, not the file** (FIXES-B B6). The size and digest are the ones
    `write_artifact` hashed once, when it wrote the file, and recorded in the index; this seeks to
    ``offset`` and reads ``length`` bytes and nothing more. It used to read and hash the whole
    artifact (up to 2 GiB) for every 8 MiB chunk. An artifact with no usable index entry (written
    by an older agent, or an entry that no longer matches the file's size) is hashed by streaming
    it in blocks, which is slow but exact and never holds the file in memory.
    """
    # **Before the read**, so a request for an absurd chunk is refused without touching the disk.
    if length > MAX_CHUNK:
        raise ValueError(f"{length} bytes is over the {MAX_CHUNK} chunk limit")
    if offset < 0 or length < 0:
        raise ValueError("offset and length must not be negative")
    path = _contained(job_dir, name)
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if offset > size:
            raise ValueError(f"offset {offset} is past the end of {size} bytes")
        digest = _recorded_digest(job_dir, name, size)
        if digest is None:
            digest = _stream_digest(handle)
        handle.seek(offset)
        data = handle.read(length)
    return {"size": size, "sha256": digest, "offset": int(offset), "data": data}


def _recorded_digest(job_dir: Path, name: str, size: int) -> str | None:
    """The sha256 `write_artifact` recorded for ``name``, or None when it cannot be trusted.

    Only an entry whose recorded size equals the file's size on disk counts: a file replaced after
    it was indexed must not be reported with the old file's digest.
    """
    index_path = _contained(job_dir, "index.json")
    try:
        entry = json.loads(index_path.read_text(encoding="utf-8")).get(name)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(entry, dict) or entry.get("size") != size:
        return None
    digest = entry.get("sha256")
    return digest if isinstance(digest, str) and len(digest) == 64 else None


def _stream_digest(handle: Any) -> str:
    """The sha256 of an open file from its start, one block at a time."""
    hasher = hashlib.sha256()
    handle.seek(0)
    while True:
        block = handle.read(1024 * 1024)
        if not block:
            return hasher.hexdigest()
        hasher.update(block)


def artifact_bytes(job_dir: Path, name: str) -> bytes:
    """A whole artifact, for a caller that wants it in one piece.

    **Not `read_chunk(..., MAX_ARTIFACT_BYTES)`**, which is what the first version did: that
    passes a
    *length* past `MAX_CHUNK` and is refused. The chunk cap is a rule about the wire, and a
    caller in this
    process is not on the wire. Containment goes through `_contained`, the same helper the other
    two use,
    so the three cannot disagree about what a legal artifact is.
    """
    return _contained(job_dir, name).read_bytes()


def cached_artifact(job_dir: Path, name: str) -> dict[str, Any] | None:
    """A recorded artifact and its header's patch list, or None when there is not one.

    **Read-only, and that is the point.** The cache lookup in `extract.surfaces` used to call
    `artifacts_dir` — which *creates* the folder — just to ask whether an artifact was there, so a
    lookup made a directory and followed a symlinked `artifacts` to do it. This touches nothing: it
    returns None for a job with no index, a name with no entry, or an entry whose file has gone.

    The header is read rather than the whole artifact, so a repeated `extract.surfaces` costs a few
    hundred bytes instead of the file.

    **A containment failure is NOT a cache miss.** An earlier version caught `ValueError` wholesale,
    which is exactly what `_contained` raises for a symlinked index or artifact — so an escape was
    swallowed, reported as "no cache", and the caller then re-extracted and failed on the *write*
    instead, with a message about the wrong file. Both `_contained` calls are therefore left
    unfenced; only the reading is fenced, so a corrupt cache returns None while a refusal
    propagates.
    """
    # **Every containment check stays OUTSIDE the swallow.**
    #
    # `_contained` refuses a bad name or a symlink with `ValueError`, and a refusal must surface:
    # hiding it made an escape look like a cache miss, after which the caller re-extracted and
    # failed on the *write* with a message about the wrong file. A corrupt index raises `ValueError`
    # too (from `json.loads`) — and that one IS a miss, because it costs a re-extraction rather than
    # indicating an attack. The two calls below are deliberately unfenced so each failure means what
    # it should; only the *reading* is fenced.
    # The name comes from a client: refuse a bad one BEFORE looking for an index, or a job with no
    # index would answer a hostile name with "no cache" instead of a refusal.
    if not NAME_RE.match(name):
        raise ValueError(f"{name!r} is not a valid artifact name")
    index_path = _contained(job_dir, "index.json")
    try:
        entry = json.loads(index_path.read_text(encoding="utf-8")).get(name)
    except (OSError, ValueError):
        # No index yet, or one that is not JSON: the ordinary first-run and corrupt-cache cases
        # alike.
        return None
    if not isinstance(entry, dict):
        return None
    # The artifact's own check, for the same reason: a symlinked artifact file is an escape, not a
    # miss. Fixing only the index's call would have left this one hiding the identical attack.
    target = _contained(job_dir, name)
    try:
        if not target.is_file():
            return None
        with target.open("rb") as handle:
            head = handle.read(12)
        # **Both artifact formats are cached through this one lookup.** This checked `MAGIC`
        # alone, so
        # a `STKFLD1` fields artifact (T-215) was treated as "not an artifact" and the cache
        # answered
        # `None` for a file that was there and correct — every repeated `extract.fields` would have
        # re-read the case. The two magics share the `MAGIC + u32 length + JSON` framing,
        # which is why
        # one check serves both.
        if head[:8] not in (MAGIC, MAGIC_FIELDS) or len(head) < 12:
            return None
        length = int.from_bytes(head[8:12], "little")
        with target.open("rb") as handle:
            handle.seek(12)
            header = json.loads(handle.read(length).decode("utf-8"))
        return {
            "size": int(entry["size"]),
            "sha256": str(entry["sha256"]),
            "patches": list(header["patches"]),
        }
    except (OSError, ValueError, KeyError, TypeError):
        # Here a problem really does mean "no usable cache": the index recorded an entry and what is
        # on disk does not match it, so re-extracting is the right answer. A corrupt cache costs
        # work, never a failed command.
        return None


# ------------------------------------------------------------------ fidelity inputs


def read_stl(path: Path) -> tuple[Any, Any]:
    """An STL's points and triangles, as arrays.

    **`vtkSTLReader` handles both ASCII and binary**, so a surface that reached the job by any route
    can be read. The agent's linter refuses a binary one at STAGING, which is the right place for
    that rule; duplicating it here would be a second implementation of it.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkIOGeometry import vtkSTLReader

    reader = vtkSTLReader()
    reader.SetFileName(str(path))
    reader.Update()
    output = reader.GetOutput()
    if output is None or output.GetNumberOfPoints() == 0:
        raise AgentBadParams(
            f"the surface {path.name} has no triangles to measure against",
            details={"path": path.name},
        )
    points = np.asarray(vtk_to_numpy(output.GetPoints().GetData()), dtype=np.float64)
    polys = output.GetPolys()
    if polys is None or polys.GetNumberOfCells() == 0:
        raise AgentBadParams(f"the surface {path.name} has no faces", details={"path": path.name})
    offsets = np.asarray(vtk_to_numpy(polys.GetOffsetsArray()), dtype=np.int64)
    connectivity = np.asarray(vtk_to_numpy(polys.GetConnectivityArray()), dtype=np.int64)
    return points, triangulate_fan(offsets.astype("uint32"), connectivity.astype("uint32"))


def boundary_points(job_dir: Path) -> Any:
    """Every boundary vertex of a finished mesh, as one array.

    **The boundary only, not the interior**: fidelity asks how closely the SURFACE follows the CAD,
    and an interior cell centre says nothing about that. One array over every patch, so a badly
    snapped wall cannot hide behind a well-snapped inlet in a per-patch average.
    """
    data, _header = extract_surfaces(job_dir)
    _header_out, patches = unpack_artifact(data)
    if not patches:
        raise AgentBadParams("the mesh has no boundary patches to measure")
    return np.concatenate(
        [np.asarray(entry["points"], dtype=np.float64) for entry in patches.values()], axis=0
    )
