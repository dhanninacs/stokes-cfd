"""A bounded ASCII polyMesh reader (brief M-506 Part B).

Reads ``constant/polyMesh/{points, faces, owner, neighbour}`` of a FINISHED mesh — the case is
written ASCII (``writeFormat ascii``, ``blockmesh._control_dict``), so the agent can compute the
quality distributions itself with numpy instead of asking checkMesh for per-cell fields it
cannot write (its ``-help`` lists ``-writeSets``/``-writeSurfaces`` only; ``-writeFields`` is the
other distribution's).

## Bounded, and hostile to everything we did not write

Every file is untrusted: **header check first** (a FoamFile ascii ``labelList``/``vectorList``
or the list-of-list shapes these five files carry — anything else refused), **counts before
allocation** (a declared count larger than the data is a refusal, not an allocation), **max
bytes** per file, and a **binary header refused with a plain reason** — a binary polyMesh would
otherwise parse as garbage numbers. A file that cannot be read whole within the limit is refused,
never truncated: a half-read mesh would produce quality numbers that look measured.

## The security review's R1-R8 (2026-10-21), each measured before writing here

- **R1 — the size check runs BEFORE the file is read**: the first draft read the whole file and
  then looked at its size; an 86-byte ``owner`` that claimed 2e9 entries cost 75 s of CPU before
  any check fired.
- **R2 — containment**: the mesh folder must be inside the job folder it is handed, resolved
  (no ``..`` out, no symlink out); a path is validated once, in :func:`read_polymesh`.
- **R3 — the count-line regex is bounded**: the first draft's ``re.search`` with ``^`` and
  ``\\n`` backtracked over 16,000 blank lines for 5.5 s; the list header is now found inside a
  bounded window, and a file that hides it deeper is refused rather than scanned.
- **R4 — counts and indices are validated**: a declared count that is not plausible (negative,
  absurd) is refused before allocation; face point ids and cell labels are range-checked so a
  cell id of 2e9 or -1 cannot reach numpy as an index; and the face ids are cross-checked
  against the points list once, before any metric can index with them.
- **R5 — everything raises the reader's own** :class:`PolyMeshError` — a ``ValueError`` from
  numpy escaping to the RPC would be an untyped crash.
- **R6 — no NaN/inf into the metrics**: a coordinate that is not finite is refused (the metrics
  read these arrays directly; a NaN point would poison every histogram).
- **R7 — a budget per call**: a face/cell cap (:data:`MAX_CELLS`, :data:`MAX_FACES`) with the
  typed "too large" refusal, and a wall-clock deadline the parser checks per list — the reader
  costs about 440 µs/face in the non-numpy paths, and a 5M-cell mesh is hours, not seconds.
- **R8 — no absolute host paths and no file text in error messages**: the messages name the
  FILE's basename and the problem, never the path they came from and never a quote of the
  content.

No ``eval``/``exec``/``pickle``/``shell``; no new executable — the review's pass on those holds.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: The most of any one polyMesh file that is read. A 5 M-cell ASCII mesh carries points and
#: faces of a few hundred megabytes total; a single file past this is not one we can analyse in
#: the host's free memory anyway (about 3 GiB), and refusing says so instead of swapping.
MAX_FILE_BYTES = 400 * 1024 * 1024

#: A line longer than any OpenFOAM list element line. OpenFOAM wraps its output; one line past
#: this is corrupt or built to defeat the scan.
MAX_LINE = 1024 * 1024

#: **The face/cell budget per call** (the review's R7): the reader's non-numpy paths cost about
#: 440 µs/face, so a 5M-cell mesh is hours of one worker thread. A mesh past this gets the
#: typed "too large" refusal and the distributions simply stay pending — never a timeout, never
#: a half-read mesh.
MAX_CELLS = 2_000_000
MAX_FACES = 4_000_000

#: The wall-clock budget for one read (R7's deadline), checked between the files and inside the
#: face walk.
DEADLINE_S = 30.0

#: A label a polyMesh file may carry: cells and points are 32-bit range in practice, and a
#: negative id is never a cell (R4).
MAX_LABEL = 2**31

_HEADER_OK = re.compile(r"FoamFile\s*\{[^}]*format\s+ascii;", re.DOTALL)
_BINARY_MARK = re.compile(r"FoamFile\s*\{[^}]*format\s+binary;", re.DOTALL | re.IGNORECASE)

#: The OF14 list shape: the count on its own line, then ``(``. The header is searched inside a
#: bounded window (R3): the first draft's ``re.search`` over the whole text backtracked for
#: seconds on a file of blank lines.
_LIST_SCAN_LIMIT = 64 * 1024


class PolyMeshError(Exception):
    """The reader's refusal: the file is not an ASCII polyMesh file we can analyse.

    Typed rather than a bare ValueError (R5), so the RPC's answer can say the quality is unknown
    because the files could not be read, and the caller never mistakes that for measured
    numbers. **Messages carry no paths and no file text** (R8): the file's basename and the
    problem, nothing else.
    """


def _deadline() -> float:
    return time.monotonic() + DEADLINE_S


def _check_deadline(limit: float, what: str) -> None:
    """The wall-clock budget (R7): checked between the files and inside the face walk."""
    if time.monotonic() > limit:
        raise PolyMeshError(
            f"reading {what} took too long; the mesh is too large for the distributions"
        )


def _check_containment(mesh_dir: Path, job_dir: Path | None) -> None:
    """The mesh folder must live inside the job folder (R2), resolved — no ``..`` out and no
    symlink out. A caller that cannot name a job folder passes None, and the mesh directory is
    still resolved so the reads see real files."""
    resolved = mesh_dir.resolve()
    if job_dir is None:
        return
    job = job_dir.resolve()
    if resolved != job and job not in resolved.parents:
        raise PolyMeshError(
            "the mesh folder is outside the job folder; the reader reads a job's own polyMesh only"
        )


def _read_bytes(path: Path, limit: float) -> bytes:
    """The file's bytes, with the size check BEFORE the read (R1) and the binary check."""
    try:
        # **R1: stat first, read second** — the first draft read the file and then measured it,
        # so an oversized file was fully read before the refusal.
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise PolyMeshError(
                f"{path.name} is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MiB the "
                "quality reader accepts"
            )
        raw = path.read_bytes()
    except OSError as exc:
        raise PolyMeshError(f"{path.name} could not be read: {exc.strerror or exc}") from exc
    if b"\x00" in raw[:4096] or _BINARY_MARK.search(raw[:4096].decode("latin-1")):
        raise PolyMeshError(
            f"{path.name} is binary; the quality reader reads the ASCII polyMesh this case writes"
        )
    if not _HEADER_OK.search(raw[:4096].decode("latin-1")):
        raise PolyMeshError(f"{path.name} is not an ASCII FoamFile")
    _check_deadline(limit, path.name)
    return raw


def _as_text(raw: bytes, path: Path) -> str:
    text = raw.decode("utf-8", errors="replace")
    if any(len(line) > MAX_LINE for line in text.splitlines()):
        raise PolyMeshError(f"{path.name} carries a line no OpenFOAM list writes")
    return text


def _list_body(text: str, path: Path) -> tuple[int, str]:
    """The declared count and the inner text of the file's first OF14 list (R3's bounded scan)."""
    window = text[:_LIST_SCAN_LIMIT]
    match = re.search(r"(\d+)\s*\n\s*\(", window)
    if match is None:
        raise PolyMeshError(f"{path.name} carries no OF14 list (count line, then the list)")
    declared = int(match.group(1))
    body = text[match.end() :]
    closer = body.rindex(")")
    return declared, body[:closer]


def _numbers(inner: str, path: Path) -> np.ndarray:
    """A list's scalar tokens to a finite float array (R6: a NaN or inf coordinate is refused).

    The parentheses separating rows are stripped first — OF14 writes one ``(x y z)`` per line,
    and the numbers are what this reader is after.
    """
    stripped = inner.replace("(", " ").replace(")", " ")
    try:
        values = np.array([float(tok) for tok in stripped.split()], dtype=np.float64)
    except ValueError as exc:
        raise PolyMeshError(f"{path.name} carries a list entry that is not a number") from exc
    if values.size and not np.all(np.isfinite(values)):
        raise PolyMeshError(f"{path.name} carries a number that is not finite")
    return values


def _check_count(declared: int, what: str, limit: float) -> None:
    """A declared count must be plausible BEFORE any allocation (R4) — the review's 86-byte
    ``owner`` claimed enough entries to cost 75 s of CPU before the first draft checked."""
    if declared < 0 or declared > MAX_FACES:
        raise PolyMeshError(f"{what} declares {declared} entries, which no acceptable mesh carries")
    _check_deadline(limit, what)


def _points(text: str, path: Path, limit: float) -> np.ndarray:
    """``points``: a count line, then ``(x y z)`` rows, as an ``(n, 3)`` array (R4: the count is
    plausible and exact; R6: every coordinate finite)."""
    declared, inner = _list_body(text, path)
    _check_count(declared, "points", limit)
    flat = _numbers(inner, path)
    if flat.size % 3 != 0:
        raise PolyMeshError("points does not hold a whole number of coordinates")
    points = flat.reshape(-1, 3)
    if declared != points.shape[0]:
        raise PolyMeshError(f"points declares {declared} entries and carries {points.shape[0]}")
    return points


def _faces(text: str, path: Path, limit: float) -> list[np.ndarray]:
    """``faces``: a count line, then one ``(a b c ...)`` block per face, ids range-checked (R4).

    OF14 writes a **mixed** face list — most faces 4 points, boundary and cut faces whatever
    they have — so the shape cannot be fixed-width, and the reader walks the words.
    """
    declared, body = _list_body(text, path)
    _check_count(declared, "faces", limit)
    faces: list[np.ndarray] = []
    current: list[int] = []
    in_face = False
    for word in body.replace("(", " ( ").replace(")", " ) ").split():
        if word == "(":
            if in_face:
                raise PolyMeshError("faces carries a nested face")
            in_face = True
            current = []
        elif word == ")":
            if not in_face:
                raise PolyMeshError("faces carries a closing parenthesis without a face")
            faces.append(np.array(current, dtype=np.int64))
            in_face = False
            if len(faces) % 65536 == 0:
                _check_deadline(limit, "faces")
        elif in_face:
            try:
                point_id = int(word)
            except ValueError as exc:
                raise PolyMeshError("faces carries an entry that is not a label") from exc
            # **R4: the id is range-checked here**, before it becomes a numpy index.
            if point_id < 0 or point_id > MAX_LABEL:
                raise PolyMeshError("faces carries a point id that is not a label in range")
            current.append(point_id)
    if in_face:
        raise PolyMeshError("faces ends inside a face")
    if declared != len(faces):
        raise PolyMeshError(f"faces declares {declared} entries and carries {len(faces)}")
    return faces


def _labels(text: str, path: Path, limit: float) -> np.ndarray:
    """``owner``/``neighbour``: a count line, then one label per face, each in range (R4)."""
    declared, inner = _list_body(text, path)
    _check_count(declared, path.name, limit)
    flat = _numbers(inner, path)
    if not np.all(flat == np.floor(flat)):
        raise PolyMeshError(f"{path.name} carries labels that are not whole")
    labels = flat.astype(np.int64)
    if labels.size and (labels.min() < 0 or labels.max() > MAX_LABEL):
        raise PolyMeshError(f"{path.name} carries a label that is not a cell id in range")
    return labels


@dataclass(frozen=True)
class PolyMesh:
    """The four arrays the quality metrics read, in the file's own order.

    ``neighbour`` has one entry per INTERNAL face (the file's shape); faces past its end are
    boundary faces, whose skewness uses the boundary definition.
    """

    points: np.ndarray  # (nPoints, 3)
    faces: list[np.ndarray]  # nFaces point-id arrays
    owner: np.ndarray  # (nFaces,)
    neighbour: np.ndarray  # (nInternalFaces,)

    @property
    def n_internal_faces(self) -> int:
        return int(self.neighbour.shape[0])


def read_polymesh(mesh_dir: Path, job_dir: Path | None = None) -> PolyMesh:
    """The four files of one ``constant/polyMesh``, bounded and validated.

    ``job_dir`` is the job folder the reader works inside (R2's containment); the RPC passes
    the job's own path when it lands. ``neighbour`` may be ABSENT for a single-cell mesh — zero
    internal faces is a legal mesh, and the reader returns an empty array rather than refusing.
    """
    _check_containment(mesh_dir, job_dir)
    limit = _deadline()
    if not mesh_dir.is_dir():
        raise PolyMeshError("the mesh folder does not exist")
    points_file = mesh_dir / "points"
    faces_file = mesh_dir / "faces"
    owner_file = mesh_dir / "owner"
    neighbour_path = mesh_dir / "neighbour"
    points = _points(_as_text(_read_bytes(points_file, limit), points_file), points_file, limit)
    faces = _faces(_as_text(_read_bytes(faces_file, limit), faces_file), faces_file, limit)
    if len(faces) > MAX_FACES or points.shape[0] > MAX_CELLS:
        raise PolyMeshError(
            "the mesh is too large for the distributions; the reader accepts "
            f"{MAX_CELLS} cells and {MAX_FACES} faces"
        )
    owner = _labels(_as_text(_read_bytes(owner_file, limit), owner_file), owner_file, limit)
    if neighbour_path.is_file():
        neighbour = _labels(
            _as_text(_read_bytes(neighbour_path, limit), neighbour_path), neighbour_path, limit
        )
    else:
        neighbour = np.zeros(0, dtype=np.int64)
    if owner.shape[0] != len(faces):
        raise PolyMeshError(f"owner carries {owner.shape[0]} entries for {len(faces)} faces")
    if neighbour.shape[0] > owner.shape[0]:
        raise PolyMeshError("neighbour carries more entries than owner")
    # **R4's cross-checks**: every point id the faces names exists, and the owner's highest cell
    # is inside the budget — checked once, before any metric can index with them.
    if faces and points.shape[0]:
        highest_point = max(int(face.max()) for face in faces if face.size)
        if highest_point >= points.shape[0]:
            raise PolyMeshError("faces names a point the points list does not have")
    if owner.size and int(owner.max()) + 1 > MAX_CELLS:
        raise PolyMeshError("owner names a cell beyond the reader's budget")
    _check_deadline(limit, "the mesh")
    return PolyMesh(points=points, faces=faces, owner=owner, neighbour=neighbour)
